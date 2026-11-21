# -*- coding: utf-8 -*-
"""
mcp/guard.py — MCP 앱 앞단 ASGI 미들웨어: 레이트리밋 → 인증 → 접근 로그.

외부에 열기 전 최소 방어선(설계 문서 §6). 키가 테스터 공용 1개라 레이트리밋은 'IP 기준'이다.
  · IP별 토큰 버킷(분당 rate_per_ip, 순간 burst_per_ip) + 서버 전체 버킷(분당 rate_global)
  · '틀린 키'가 window_sec 안에 auth_fail_max 회 넘으면 그 IP를 window 동안 429 (키 대입 차단).
    키를 아예 안 보낸 요청은 세지 않는다 — MCP 클라이언트는 401을 받으면 OAuth 탐색(/.well-known/… 여러 개)을
    연달아 시도하므로, 키 설정을 빠뜨린 테스터가 접속 한 번에 10회를 채워 잠기는 일이 실제로 있었다.
  · /.well-known/* 은 인증 전에 404 — OAuth를 쓰지 않는다는 걸 클라이언트가 바로 알게 한다.
  · 접근 로그: IP·JSON-RPC 메서드·상태·소요ms. 도구 인자(검색어 등)는 남기지 않는다.
동시 처리 상한과 본문 크기 상한은 uvicorn(limit_concurrency)·SDK(max_request_body_size)가 맡는다.

클라이언트 IP: Caddy 뒤에서는 접속 주소가 127.0.0.1이므로, 루프백에서 온 요청에 한해 X-Forwarded-For의
마지막 값을 쓴다. Caddy는 신뢰하지 않는 클라이언트가 보낸 X-Forwarded-For를 버리고 자기가 본 주소로 채운다.
"""
import hmac
import logging
import re
import time

log = logging.getLogger("sauron-mcp.access")

LOOPBACK = {"127.0.0.1", "::1"}
METHOD_RE = re.compile(rb'"method"\s*:\s*"([^"]{1,64})"')
UNAUTHORIZED = (b'{"error":"unauthorized","hint":"put the API key in the X-API-Key header '
                b'or Authorization: Bearer <key>"}')


class _Bucket:
    __slots__ = ("tokens", "last")

    def __init__(self, cap):
        self.tokens, self.last = float(cap), time.monotonic()

    def take(self, cap, per_sec):
        now = time.monotonic()
        self.tokens = min(cap, self.tokens + (now - self.last) * per_sec)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class Guard:
    def __init__(self, app, api_key, rate_per_ip=30, burst_per_ip=15, rate_global=120,
                 auth_fail_max=10, window_sec=600):
        self.app, self.key = app, api_key.encode()
        self.ip_cap, self.ip_rate = burst_per_ip, rate_per_ip / 60
        self.g_cap, self.g_rate = rate_global / 2, rate_global / 60
        self.fail_max, self.window = auth_fail_max, window_sec
        self.buckets = {}                     # ip → _Bucket
        self.fails = {}                       # ip → (count, window_start)
        self.global_bucket = _Bucket(self.g_cap)

    def _client_ip(self, scope):
        peer = (scope.get("client") or ("?", 0))[0]
        if peer in LOOPBACK:
            xff = dict(scope.get("headers") or {}).get(b"x-forwarded-for")
            if xff:
                return xff.decode("latin-1").split(",")[-1].strip()
        return peer

    def _gc(self):
        if len(self.buckets) > 5000:          # 오래 조용한 IP 정리(메모리 상한)
            cutoff = time.monotonic() - 3600
            self.buckets = {k: b for k, b in self.buckets.items() if b.last > cutoff}
        if len(self.fails) > 5000:
            cutoff = time.time() - self.window
            self.fails = {k: v for k, v in self.fails.items() if v[1] > cutoff}

    def _blocked(self, ip):
        n, start = self.fails.get(ip, (0, 0))
        if time.time() - start > self.window:
            return False
        return n >= self.fail_max

    def _fail(self, ip):
        n, start = self.fails.get(ip, (0, 0))
        now = time.time()
        self.fails[ip] = (1, now) if now - start > self.window else (n + 1, start)

    @staticmethod
    async def _reply(send, status, body, extra=()):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), *extra]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":           # lifespan 등은 그대로
            return await self.app(scope, receive, send)

        t0, ip, status, rpc = time.monotonic(), self._client_ip(scope), [0], ["-"]
        try:
            if scope.get("path", "").startswith("/.well-known/"):
                status[0] = 404
                return await self._reply(send, 404, b'{"error":"not found"}')
            self._gc()
            if self._blocked(ip):
                status[0] = 429
                return await self._reply(send, 429, b'{"error":"too many failed auth"}',
                                         [(b"retry-after", str(self.window).encode())])
            bucket = self.buckets.get(ip) or self.buckets.setdefault(ip, _Bucket(self.ip_cap))
            if not bucket.take(self.ip_cap, self.ip_rate) or not self.global_bucket.take(self.g_cap, self.g_rate):
                status[0] = 429
                return await self._reply(send, 429, b'{"error":"rate limited"}', [(b"retry-after", b"10")])

            # 키 전달: Authorization: Bearer <키> 또는 X-API-Key: <키>
            # (claude.ai 커스텀 커넥터는 authorization 헤더를 OAuth용으로 막아 두어 x-api-key만 쓸 수 있다)
            headers = dict(scope.get("headers") or {})
            auth = headers.get(b"authorization", b"")
            token = auth[7:].strip() if auth[:7].lower() == b"bearer " else headers.get(b"x-api-key", b"").strip()
            if not hmac.compare_digest(token, self.key):
                if token:                     # 틀린 키만 센다(키 없음 = 설정 누락, 대입 시도가 아님)
                    self._fail(ip)
                status[0] = 401
                return await self._reply(send, 401, UNAUTHORIZED, [(b"www-authenticate", b"Bearer")])

            async def send_wrap(msg):
                if msg["type"] == "http.response.start":
                    status[0] = msg["status"]
                await send(msg)

            async def receive_wrap():
                msg = await receive()
                if rpc[0] == "-" and msg.get("type") == "http.request":
                    m = METHOD_RE.search(msg.get("body") or b"")
                    if m:
                        rpc[0] = m.group(1).decode("ascii", "replace")
                return msg

            await self.app(scope, receive_wrap, send_wrap)
        finally:
            log.info("%s %s %s %s %d %.0fms", ip, scope.get("method"), scope.get("path"), rpc[0], status[0],
                     (time.monotonic() - t0) * 1000)
