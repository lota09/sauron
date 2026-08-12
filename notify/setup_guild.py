# -*- coding: utf-8 -*-
"""
notify/setup_guild.py — 학과별 역할 + 비공개 채널 자동 생성 (워크플로 step 3).

역할 + 채널 생성/갱신(idempotent, 소급 적용):
  - 역할(role): 학과명. 구독 = 이 역할 보유.
  - 학과 채널: @everyone 숨김 + 해당 역할만 '열람'. 전송은 봇/관리자만(역할 보유자도 전송 불가).
    단과대(college) 카테고리 아래. 기존 채널도 매 실행 시 권한·카테고리를 desired로 소급 갱신.
  - 통합공지(mono)·감시(debug) 채널: 전송은 봇/관리자만. 없을 때만 생성(카테고리 미지정=top-level),
    카테고리는 지정/변경하지 않음(관리자가 옮긴 위치 보존). app_meta에 저장 → 런타임이 여기서 읽음.
  - 생성/확인한 역할·채널 ID를 depts 테이블에 동기화.
멱등: DB ID가 아니라 '길드에 같은 이름의 역할/채널이 실제 있는지'로 판단(수동 삭제·재생성과 desync 방지).
소급: 학과 채널은 권한/카테고리(단과대)를 desired와 다를 때만 edit. mono/debug는 권한만, 카테고리 불변.
merge: 소급 시 sauron '관리' overwrite(@everyone·해당 역할·봇)만 덮고, 그 외(관리자가 붙인
  developers 등)의 overwrite는 보존. 관리자 전송은 Administrator가 overwrite를 무시하므로 자동 성립.

실행: python -m notify.setup_guild [--dry]
  --dry : 실제 생성 없이 무엇을 만들지 출력만.
필요: bot_token(secrets), DISCORD_GUILD_ID(config), 봇에 Manage Roles/Manage Channels 권한.
⚠ 채널/역할 수십 개를 실제로 만드므로 신중히. 레이트리밋으로 다소 느릴 수 있음.
"""
import asyncio
import json
import os
import re
import sys

import config
from db.store import Store

try:
    import discord
except ImportError:
    discord = None

DRY = "--dry" in sys.argv[1:]


def _norm_ch(name):
    """디스코드 채널명 정규화 근사(소문자·공백→하이픈·연속하이픈 축약). 존재 조회 매칭용."""
    n = (name or "").strip().lower().replace(" ", "-")
    return re.sub(r"-{2,}", "-", n)


def _token():
    with open(config.DISCORD_TOKEN_FILE, encoding="utf-8") as f:
        return json.load(f)["bot_token"]


async def _ensure_category(guild, name, cache):
    if name in cache:
        return cache[name]
    existing = discord.utils.get(guild.categories, name=name)
    cat = existing or (None if DRY else await guild.create_category(name))
    cache[name] = cat
    return cat


_KEEP = object()   # _sync_perms에서 '카테고리 건드리지 않음' 표식


def _dept_overwrites(guild, role, me):
    """학과 채널: @everyone 숨김 · 해당 역할만 열람(전송 불가) · 봇만 전송.
    관리자(Administrator 권한)는 overwrite를 무시하고 전송 가능하므로 별도 허용 불필요."""
    ow = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
        role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
    }
    if me:
        ow[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    return ow


def _public_overwrites(guild, me):
    """통합/감시 채널: 전체 열람 · 전송은 봇/관리자만."""
    ow = {guild.default_role: discord.PermissionOverwrite(send_messages=False)}
    if me:
        ow[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    return ow


async def _sync_perms(ch, managed, cat=_KEEP):
    """sauron이 '관리하는' overwrite(managed: {target:PO})만 소급 적용하고, 그 외 역할
    (예: 관리자가 붙인 developers)의 overwrite는 보존(merge). 카테고리는 cat 지정 시에만.
    실제로 달라질 때만 edit. 반환: 변경했는지."""
    current = dict(ch.overwrites)            # 기존 전체(타 역할 overwrite 포함)
    desired = dict(current)
    for tgt, po in managed.items():          # sauron 관리 대상만 덮어씀 → 나머지는 그대로
        desired[tgt] = po
    kwargs = {}
    if cat is not _KEEP:
        cur = ch.category.id if ch.category else None
        if cur != (cat.id if cat else None):
            kwargs["category"] = cat
    if desired != current:
        kwargs["overwrites"] = desired
    if kwargs and not DRY:
        await ch.edit(reason="sauron 권한/카테고리 소급(타 역할 overwrite 보존)", **kwargs)
        return True
    return False


async def run(gid):
    store = Store(config.DB_PATH)
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            guild = client.get_guild(int(gid)) or await client.fetch_guild(int(gid))
            # DB ID가 아니라 '길드에 같은 이름이 실제 있는지'로 판단(수동 삭제/재생성과의 desync 방지).
            roles_by_name = {r.name: r for r in guild.roles}
            try:
                all_channels = await guild.fetch_channels()      # 실제 채널 목록 조회
            except Exception:
                all_channels = list(getattr(guild, "channels", []))
            chs_by_name = {c.name: c for c in all_channels}      # c.name은 디스코드 정규화된 이름

            def _find(cname):
                return chs_by_name.get(_norm_ch(cname)) or chs_by_name.get(cname)

            me = guild.me or guild.get_member(client.user.id)     # 봇 멤버(전송 허용 overwrite용)
            depts = store.active_depts()
            cat_cache = {}
            created_r = created_c = reused_r = reused_c = updated_c = 0
            for d in depts:
                did, name = d["dept_id"], (d.get("name_ko") or d["dept_id"])
                # 카테고리는 college가 아니라 kind로 결정(college 컬럼은 '단과대'로 순수 유지).
                kind = d.get("kind") or "major"
                if kind == "general":
                    college = "공통 공지"
                elif kind == "etc":
                    college = "기타"
                else:
                    college = (d.get("college") or "").strip() or "기타"

                # ── 역할: 이름으로 실제 존재 확인 → 없으면 생성, 있으면 재사용 ──
                role = roles_by_name.get(name)
                if role is None:
                    print(f"[역할 생성] {name}" + (" (dry)" if DRY else ""))
                    if not DRY:
                        role = await guild.create_role(name=name, mentionable=False, reason="sauron 학과 역할")
                        roles_by_name[name] = role
                        created_r += 1
                else:
                    reused_r += 1
                    print(f"[역할 존재·재사용] {name} (id={role.id})")
                if role and str(d.get("discord_role_id") or "") != str(role.id):
                    store.set_dept_discord(did, role_id=str(role.id))    # DB를 실제 ID로 동기화
                role_id = str(role.id) if role else None

                # ── 채널: 없으면 생성, 있으면 권한·카테고리 소급 갱신(열람=역할, 전송=관리자만) ──
                chname = (config.DISCORD_CHANNEL_PREFIX + name)
                ch = _find(chname)
                ow = _dept_overwrites(guild, role, me) if role else None
                if ch is None:
                    print(f"[채널 생성] {college} / {chname}" + (" (dry)" if DRY else ""))
                    if not DRY and role and ow:
                        cat = await _ensure_category(guild, college, cat_cache)
                        ch = await guild.create_text_channel(chname, category=cat, overwrites=ow,
                                                             reason="sauron 학과 채널")
                        chs_by_name[ch.name] = ch
                        created_c += 1
                elif ow:
                    cat = await _ensure_category(guild, college, cat_cache)
                    if DRY:
                        print(f"[채널 점검·소급(dry)] {college} / {ch.name}")
                    elif await _sync_perms(ch, ow, cat):
                        updated_c += 1
                        print(f"[채널 갱신] {college} / {ch.name} (권한/카테고리)")
                    else:
                        reused_c += 1
                        print(f"[채널 유지] {college} / {ch.name} (id={ch.id})")
                if ch and str(d.get("discord_channel_id") or "") != str(ch.id):
                    store.set_dept_discord(did, channel_id=str(ch.id))   # DB 동기화

            # ── 통합공지(mono)·감시(디버그) 채널: 이름으로 생성/소급갱신 → app_meta에 ID 저장 ──
            #    전체 열람 · 전송은 봇/관리자만(전송 잠금). 런타임(main)이 DB에서 읽어 Notifier에 주입.
            pub_ow = _public_overwrites(guild, me)
            # mono/debug: 없을 때만 생성(카테고리 미지정 → top-level). 카테고리는 sauron이 지정/변경하지
            #   않는다 — 관리자가 어느 카테고리(예: developers)로 옮겨도 그대로 둔다(_sync_perms cat 미전달).
            #   권한은 merge라 관리자가 붙인 타 역할 overwrite도 보존.
            mono_name = config.MONO_CHANNEL_NAME
            mono = _find(mono_name)
            if mono is None:
                print(f"[통합채널 생성] {mono_name}" + (" (dry)" if DRY else ""))
                if not DRY:
                    mono = await guild.create_text_channel(mono_name, overwrites=pub_ow, reason="sauron 통합공지 채널")
            elif DRY:
                print(f"[통합채널 점검·소급(dry)] {mono.name}")
            elif await _sync_perms(mono, pub_ow):
                print(f"[통합채널 갱신] {mono.name} (전송 권한)")
            else:
                print(f"[통합채널 유지] {mono.name} (id={mono.id})")
            if mono and not DRY:
                store.set_meta("mono_channel_id", str(mono.id))

            dbg_name = config.DEBUG_CHANNEL_NAME
            dbg = _find(dbg_name)
            if dbg is None:
                print(f"[감시채널 생성] {dbg_name}" + (" (dry)" if DRY else ""))
                if not DRY:
                    dbg = await guild.create_text_channel(dbg_name, overwrites=pub_ow, reason="sauron 감시/디버그 채널")
            elif DRY:
                print(f"[감시채널 점검·소급(dry)] {dbg.name}")
            elif await _sync_perms(dbg, pub_ow):
                print(f"[감시채널 갱신] {dbg.name} (전송 권한)")
            else:
                print(f"[감시채널 유지] {dbg.name} (id={dbg.id})")
            if dbg and not DRY:
                store.set_meta("debug_channel_id", str(dbg.id))   # main이 여기서 읽어 Notifier에 주입

            store.checkpoint()
            print(f"[완료] 역할 생성 {created_r}·재사용 {reused_r}"
                  + f" / 채널 생성 {created_c}·갱신 {updated_c}·유지 {reused_c}"
                  + f" / 통합채널 {'준비됨' if mono else '-'} / 감시채널 {'준비됨' if dbg else '-'}"
                  + (" (dry-run: 실제 생성 없음)" if DRY else ""))
        except Exception as e:
            print(f"[오류] {e}")
        finally:
            await client.close()

    await client.start(_token())


def main():
    if discord is None:
        raise SystemExit("discord.py 미설치: pip install -U discord.py")
    debug = config.debug_from_argv(sys.argv)
    gid = config.active_guild_id(debug)
    if not gid:
        raise SystemExit("대상 길드 ID 없음: DEBUG_GUILD_ID/PROD_GUILD_ID 확인 (또는 --debug/--prod)")
    print(f"[setup] {'디버깅' if debug else '실서비스'} 서버({gid})" + (" [dry]" if DRY else ""))
    asyncio.run(run(gid))


if __name__ == "__main__":
    main()
