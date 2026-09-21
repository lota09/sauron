# -*- coding: utf-8 -*-
"""core/crawl_health.py — 학과별 수집 실패를 '상태 변화'로만 알린다.

크롤은 10분마다 돈다. 실패할 때마다 감시채널에 보내면 사이트 하나가 죽었을 때 하루 수십 통이 쌓인다
(영화예술 사이트 장애 때 하루 45회 실패). 그래서:
  · 처음 실패          → 알림 (원인·발생 위치)
  · 같은 원인으로 계속  → 로그만. CRAWL_FAIL_REMIND_SEC 마다 '아직 실패 중' 한 번
  · 원인이 바뀜        → 알림 (다른 고장일 수 있으므로)
  · 성공으로 돌아옴     → '복구됨' 한 번
상태는 app_meta('crawl_fail:<dept_id>')에 JSON으로 둔다 → 크롤러가 재시작해도 이어진다.
'같은 원인' = core.errors.signature (원인 예외 타입 + 우리 코드의 발생 위치). 메시지는 숫자 등이
매번 달라질 수 있어 비교에서 뺀다.
"""
import json
import time

import config
from core.errors import describe, full, signature

PREFIX = "crawl_fail:"


def _dur(sec):
    sec = int(sec)
    d, h, m = sec // 86400, sec % 86400 // 3600, sec % 3600 // 60
    return f"{d}일 {h}시간" if d else (f"{h}시간 {m}분" if h else f"{m}분")


class CrawlHealth:
    def __init__(self, store, notifier, log, remind_sec=None):
        self.store, self.notifier, self.log = store, notifier, log
        self.remind = config.CRAWL_FAIL_REMIND_SEC if remind_sec is None else remind_sec
        # 실패 중인 학과만 메모리에 들고 있어, 정상 학과의 ok()는 DB를 건드리지 않는다.
        self._failing = {k[len(PREFIX):] for k in store.meta_with_prefix(PREFIX)}

    def _alert(self, text):
        try:
            self.notifier.debug(text)
        except Exception as e:                       # 알림 실패가 크롤을 막으면 안 된다
            self.log(f"[debug 전송 실패] {e}")

    def failed(self, dept_id, label, err, hint=""):
        key, now = PREFIX + dept_id, time.time()
        sig, desc = signature(err), describe(err)
        raw = self.store.get_meta(key)
        st = json.loads(raw) if raw else None
        body = f"{hint}\n{desc}" if hint else desc
        if st is None:
            st = {"since": now, "count": 1, "sig": sig, "last_alert": now}
            self.log(f"[크롤 실패 시작] {label}\n{full(err)}")      # 처음엔 전체 트레이스백
            self._alert(f"**크롤 실패** · {label}\n{body}")
        else:
            st["count"] += 1
            if st["sig"] != sig:
                st["sig"], st["last_alert"] = sig, now
                self.log(f"[크롤 실패 원인 변경] {label} ({st['count']}회째)\n{full(err)}")
                self._alert(f"**크롤 실패 — 원인 바뀜** · {label} · {st['count']}회째 "
                            f"({_dur(now - st['since'])} 전부터)\n{body}")
            elif now - st["last_alert"] >= self.remind:
                st["last_alert"] = now
                self.log(f"[크롤 실패 지속] {label} · {st['count']}회째 · {sig}")
                self._alert(f"**여전히 크롤 실패** · {label} · {st['count']}회째 · "
                            f"{_dur(now - st['since'])}째\n{body}")
            else:
                self.log(f"[크롤 실패] {label} · {st['count']}회째 · {sig}")   # 반복은 한 줄만
        self.store.set_meta(key, json.dumps(st))
        self._failing.add(dept_id)

    def ok(self, dept_id, label):
        if dept_id not in self._failing:
            return
        key = PREFIX + dept_id
        raw = self.store.get_meta(key)
        self._failing.discard(dept_id)
        if not raw:
            return
        st = json.loads(raw)
        self.store.delete_meta(key)
        self.log(f"[크롤 복구] {label} · {st['count']}회 실패 · {_dur(time.time() - st['since'])} 만에")
        self._alert(f"**크롤 복구** · {label} · {st['count']}회 실패 후 "
                    f"{_dur(time.time() - st['since'])} 만에 정상화")

    def failing(self):
        """현재 실패 중인 학과 → 상태(dict). /상태 표시 등에 쓸 수 있다."""
        return {k[len(PREFIX):]: json.loads(v) for k, v in self.store.meta_with_prefix(PREFIX).items()}
