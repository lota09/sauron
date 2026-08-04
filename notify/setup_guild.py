# -*- coding: utf-8 -*-
"""
notify/setup_guild.py — 학과별 역할 + 비공개 채널 자동 생성 (워크플로 step 3).

활성 학과 중 role_id/channel_id 가 없는 것만 생성(idempotent):
  - 역할(role): 학과명. 구독 = 이 역할 보유.
  - 비공개 채널: @everyone 숨김 + 해당 역할만 보기 허용. 단과대(college) 카테고리 아래.
  - 생성한 ID를 depts 테이블에 저장.

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

            depts = store.active_depts()
            cat_cache = {}
            created_r = created_c = reused_r = reused_c = 0
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

                # ── 채널: 정규화 이름으로 실제 존재 확인 → 없으면 생성, 있으면 재사용 ──
                chname = (config.DISCORD_CHANNEL_PREFIX + name)
                ch = chs_by_name.get(_norm_ch(chname)) or chs_by_name.get(chname)
                if ch is None:
                    print(f"[채널 생성] {college} / {chname}" + (" (dry)" if DRY else ""))
                    if not DRY and role_id:
                        cat = await _ensure_category(guild, college, cat_cache)
                        overwrites = {
                            guild.default_role: discord.PermissionOverwrite(view_channel=False),
                            guild.get_role(int(role_id)): discord.PermissionOverwrite(view_channel=True),
                        }
                        ch = await guild.create_text_channel(chname, category=cat, overwrites=overwrites,
                                                             reason="sauron 학과 채널")
                        chs_by_name[ch.name] = ch
                        created_c += 1
                else:
                    reused_c += 1
                    print(f"[채널 존재·재사용] {college} / {ch.name} (id={ch.id})")
                if ch and str(d.get("discord_channel_id") or "") != str(ch.id):
                    store.set_dept_discord(did, channel_id=str(ch.id))   # DB 동기화

            store.checkpoint()
            print(f"[완료] 역할 생성 {created_r}·재사용 {reused_r} / 채널 생성 {created_c}·재사용 {reused_c}"
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
