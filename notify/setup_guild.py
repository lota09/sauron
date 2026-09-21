# -*- coding: utf-8 -*-
"""
notify/setup_guild.py — 학과별 역할 + 비공개 채널 자동 생성 (워크플로 step 3).

역할 + 채널 생성/갱신(idempotent, 소급 적용):
  - 역할(role): 학과명. 구독 = 이 역할 보유.
  - 학과 채널: @everyone 숨김 + 해당 역할만 '열람'. 전송은 봇/관리자만(역할 보유자도 전송 불가).
    단과대(college) 카테고리 아래. 기존 채널도 매 실행 시 권한·카테고리를 desired로 소급 갱신.
  - developers 역할·카테고리(sauron 관리, 없으면 생성): mono/debug 채널을 developers 카테고리에 넣고
    'developers만 열람·봇/관리자만 전송'으로 소급. developers 역할ID는 app_meta에 저장(디버그 멘션용).
  - 통합공지(mono)·감시(debug) 채널ID도 app_meta에 저장 → 런타임(main)이 읽어 Notifier에 주입.
  - 생성/확인한 학과 역할·채널 ID를 depts 테이블에 동기화.
멱등: DB ID가 아니라 '길드에 같은 이름의 역할/채널이 실제 있는지'로 판단(수동 삭제·재생성과 desync 방지).
소급: 학과 채널은 권한/카테고리(단과대), mono/debug는 권한/카테고리(developers)를 desired와 다를 때만 edit.
merge: 소급 시 sauron '관리' overwrite(@everyone·해당 역할·developers·봇)만 덮고, 그 외 역할의
  overwrite는 보존. 관리자 전송은 Administrator가 overwrite를 무시하므로 자동 성립.

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
import traceback

import config
from db.store import Store

try:
    import discord
except ImportError:
    discord = None

DRY = "--dry" in sys.argv[1:] and "--check" not in sys.argv[1:]


def _norm_ch(name):
    """디스코드 채널명 정규화 근사(소문자·공백→하이픈·연속하이픈 축약). 존재 조회 매칭용."""
    n = (name or "").strip().lower().replace(" ", "-")
    return re.sub(r"-{2,}", "-", n)


def _role_name(d):
    """역할 이름 = name_ko에서 앞의 단과대(college)를 뗀 형태. 예: 'IT대학 AI융합학부' → 'AI융합학부'.
    (역할 매칭/생성에만 사용. 채널 이름은 name_ko 그대로 둔다.)"""
    full = (d.get("name_ko") or d["dept_id"]).strip()
    col = (d.get("college") or "").strip()
    if col and full.startswith(col):
        return full[len(col):].strip() or full
    return full


def _token():
    with open(config.DISCORD_TOKEN_FILE, encoding="utf-8") as f:
        return json.load(f)["bot_token"]


def _category_name(d):
    """학과가 들어갈 카테고리 이름. college가 아니라 kind로 정한다(college 컬럼은 '단과대'로 순수 유지)."""
    kind = d.get("kind") or "major"
    if kind == "general":
        return config.GENERAL_CATEGORY_NAME
    if kind == "etc":
        return config.ETC_CATEGORY_NAME
    return (d.get("college") or "").strip() or config.ETC_CATEGORY_NAME


async def _rename_categories(depts, all_channels, find, cache):
    """설정에서 카테고리 이름을 바꿨을 때, 새로 만들지 않고 기존 카테고리의 이름을 바꾼다(권한·위치 유지).
    '기존 카테고리' = 그 종류 학과 채널들이 지금 가장 많이 들어 있는 카테고리.
    원하는 이름이 이미 있으면 아무것도 안 한다 → 두 번째 실행부터는 no-op(멱등).
    DB에 카테고리 ID를 따로 두지 않고 길드의 실제 상태로 판단한다(이 파일의 원칙과 같음)."""
    cats = {c.id: c for c in all_channels if isinstance(c, discord.CategoryChannel)}
    names = {c.name for c in cats.values()}
    for kind, want in (("general", config.GENERAL_CATEGORY_NAME), ("etc", config.ETC_CATEGORY_NAME)):
        if want in names:
            continue
        count = {}
        for d in depts:
            if (d.get("kind") or "major") != kind:
                continue
            ch = find(config.DISCORD_CHANNEL_PREFIX + (d.get("name_ko") or d["dept_id"]))
            cid = getattr(ch, "category_id", None)
            if cid in cats:
                count[cid] = count.get(cid, 0) + 1
        if not count:
            continue                                   # 채널이 아직 없음 → 생성 단계가 새 이름으로 만든다
        cid = max(count, key=count.get)
        old = cats[cid]
        print(f"[카테고리 이름 변경] {old.name} → {want} (그 안의 {kind} 채널 {count[cid]}개 기준)"
              + (" (dry)" if DRY else ""))
        if not DRY:
            old = await old.edit(name=want, reason="sauron 카테고리 이름 설정 변경") or old
        cache[want] = old                              # 바로 뒤 _ensure_category가 새로 만들지 않게
        names.add(want)


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


def _dev_overwrites(guild, me, dev_role):
    """통합/감시 채널: developers 역할만 열람(@everyone 숨김) · 전송은 봇/관리자만."""
    ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False)}
    if dev_role:
        ow[dev_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
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


# setup_guild가 app_meta에 남겨야 하는 키 — 끝에서 '진짜 저장됐는지' 되읽어 확인한다.
META_KEYS = ("developers_role_id", "mono_channel_id", "debug_channel_id")


async def run(gid):
    store = Store(config.DB_PATH)
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    result = {"ok": False}          # on_ready 안의 성패를 바깥(main)으로 전달

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
            await _rename_categories(depts, all_channels, _find, cat_cache)
            created_r = created_c = reused_r = synced_c = 0   # synced_c: 이미 있어 소급 처리한 채널(갱신+유지 통합)
            for d in depts:
                did, name = d["dept_id"], (d.get("name_ko") or d["dept_id"])
                college = _category_name(d)

                # ── 역할: name_ko에서 단과대 뗀 이름으로 존재 확인 → 없으면 생성, 있으면 재사용 ──
                rname = _role_name(d)      # 예: 'IT대학 AI융합학부' → 'AI융합학부'
                role = roles_by_name.get(rname)
                if role is None:
                    print(f"[역할 생성] {rname}" + (" (dry)" if DRY else ""))
                    if not DRY:
                        role = await guild.create_role(name=rname, mentionable=False, reason="sauron 학과 역할")
                        roles_by_name[rname] = role
                        created_r += 1
                else:
                    reused_r += 1
                    print(f"[역할 존재·재사용] {rname} (id={role.id})")
                if role and str(d.get("discord_role_id") or "") != str(role.id):
                    store.set_dept_discord(did, role_id=str(role.id))    # DB를 실제 ID로 동기화

                # ── 채널: 없으면 생성, 있으면 권한·카테고리 소급 갱신(열람=역할, 전송=관리자만) ──
                #   채널 이름은 name_ko 그대로(단과대 카테고리로 그룹핑되므로).
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
                elif DRY:
                    # dry는 역할을 실제로 안 만들어 ow가 None일 수 있음 → 존재 사실만 보고(권한 소급은 실행 때).
                    synced_c += 1
                    cur = next((c.name for c in all_channels if c.id == getattr(ch, "category_id", None)), None)
                    target = cat_cache[college].name if college in cat_cache else college
                    if cur != target:
                        print(f"[채널 이동(dry)] {cur} → {target} / {ch.name}")
                    else:
                        print(f"[채널 존재·소급예정(dry)] {college} / {ch.name}")
                elif ow:
                    cat = await _ensure_category(guild, college, cat_cache)
                    synced_c += 1
                    changed = await _sync_perms(ch, ow, cat)
                    print(f"[채널 소급] {college} / {ch.name}" + (" (변경됨)" if changed else " (변경없음)"))
                else:
                    synced_c += 1
                    print(f"[채널 소급·역할미비] {college} / {ch.name}")
                if ch and str(d.get("discord_channel_id") or "") != str(ch.id):
                    store.set_dept_discord(did, channel_id=str(ch.id))   # DB 동기화

            # ── developers 역할·카테고리(sauron 관리): 없으면 생성. 역할ID는 app_meta에 저장(디버그 멘션용) ──
            dev_role = roles_by_name.get(config.DEV_ROLE_NAME)
            if dev_role is None:
                print(f"[역할 생성] {config.DEV_ROLE_NAME}" + (" (dry)" if DRY else ""))
                if not DRY:
                    dev_role = await guild.create_role(name=config.DEV_ROLE_NAME, mentionable=False,
                                                       reason="sauron developers 역할")
                    roles_by_name[config.DEV_ROLE_NAME] = dev_role
            else:
                print(f"[역할 존재·재사용] {config.DEV_ROLE_NAME} (id={dev_role.id})")
            if dev_role and not DRY:
                store.set_meta("developers_role_id", str(dev_role.id))
            dev_cat = await _ensure_category(guild, config.DEV_CATEGORY_NAME, cat_cache)  # 없으면 생성
            dev_ow = _dev_overwrites(guild, me, dev_role)

            # ── 통합공지(mono)·감시(디버그) 채널: developers 카테고리·역할 소급, 전송은 봇/관리자만 ──
            mono_name = config.MONO_CHANNEL_NAME
            mono = _find(mono_name)
            if mono is None:
                print(f"[통합채널 생성] {mono_name}" + (" (dry)" if DRY else ""))
                if not DRY:
                    mono = await guild.create_text_channel(mono_name, category=dev_cat, overwrites=dev_ow,
                                                           reason="sauron 통합공지 채널")
            elif DRY:
                print(f"[통합채널 점검·소급(dry)] {mono.name} → {config.DEV_CATEGORY_NAME}")
            elif await _sync_perms(mono, dev_ow, dev_cat):
                print(f"[통합채널 갱신] {mono.name} (역할/카테고리)")
            else:
                print(f"[통합채널 유지] {mono.name} (id={mono.id})")
            if mono and not DRY:
                store.set_meta("mono_channel_id", str(mono.id))

            dbg_name = config.DEBUG_CHANNEL_NAME
            dbg = _find(dbg_name)
            if dbg is None:
                print(f"[감시채널 생성] {dbg_name}" + (" (dry)" if DRY else ""))
                if not DRY:
                    dbg = await guild.create_text_channel(dbg_name, category=dev_cat, overwrites=dev_ow,
                                                          reason="sauron 감시/디버그 채널")
            elif DRY:
                print(f"[감시채널 점검·소급(dry)] {dbg.name} → {config.DEV_CATEGORY_NAME}")
            elif await _sync_perms(dbg, dev_ow, dev_cat):
                print(f"[감시채널 갱신] {dbg.name} (역할/카테고리)")
            else:
                print(f"[감시채널 유지] {dbg.name} (id={dbg.id})")
            if dbg and not DRY:
                store.set_meta("debug_channel_id", str(dbg.id))   # main이 여기서 읽어 Notifier에 주입

            store.checkpoint()
            print(f"[완료] 역할 생성 {created_r}·재사용 {reused_r}"
                  + f" / 채널 생성 {created_c}·기존 {synced_c}"
                  + f" / 통합채널 {'준비됨' if mono else '-'} / 감시채널 {'준비됨' if dbg else '-'}"
                  + (" (dry-run: 실제 생성 없음)" if DRY else ""))
            # 위 '준비됨'은 디스코드 객체를 잡았다는 뜻일 뿐 DB에 들어갔다는 뜻이 아니다.
            # 런타임이 실제로 읽는 값을 되읽어 그대로 보여주고, 빠진 게 있으면 실패로 끝낸다.
            #   (과거에 mono/debug 키가 비어 디버그가 통째로 안 나가는데도 아무도 몰랐다.)
            result["ok"] = _verify_meta(store)
        except Exception:
            traceback.print_exc()          # 사유만이 아니라 어디서 끊겼는지까지 남긴다
            result["ok"] = False
        finally:
            await client.close()

    await client.start(_token())
    return result["ok"]


def _verify_meta(store):
    """app_meta를 되읽어 저장 결과를 출력. 전부 있으면 True."""
    if DRY:
        print("[검증 생략] dry-run — DB에 쓰지 않았습니다")
        return True
    vals = {k: store.get_meta(k) for k in META_KEYS}
    for k, v in vals.items():
        print(f"[app_meta] {k} = {v or '없음 ← 저장 안 됨'}")
    missing = [k for k, v in vals.items() if not v]
    if missing:
        print(f"[실패] app_meta에 저장되지 않은 키: {', '.join(missing)}")
        return False
    print("[검증 OK] 런타임(main)이 읽을 값이 모두 저장됨 "
          "— 크롤러가 이미 떠 있다면 재시작해야 반영됩니다(시작 시 1회만 읽음)")
    return True


def main():
    # --check: 디스코드에 붙지 않고 'DB가 동기화돼 있나'만 본다. 길드에 채널이 다 있어도
    #   app_meta가 비어 있을 수 있으므로(그러면 런타임이 감시채널을 못 찾는다) 따로 확인이 필요하다.
    if "--check" in sys.argv[1:]:
        raise SystemExit(0 if _verify_meta(Store(config.DB_PATH)) else 1)
    if discord is None:
        raise SystemExit("discord.py 미설치: pip install -U discord.py")
    debug = config.debug_from_argv(sys.argv)
    gid = config.active_guild_id(debug)
    if not gid:
        raise SystemExit("대상 길드 ID 없음: DEBUG_GUILD_ID/PROD_GUILD_ID 확인 (또는 --debug/--prod)")
    print(f"[setup] {'디버깅' if debug else '실서비스'} 서버({gid})" + (" [dry]" if DRY else ""))
    if not asyncio.run(run(gid)):
        raise SystemExit(1)      # 반쯤 끝난 실행이 성공(exit 0)으로 보이지 않게 한다


if __name__ == "__main__":
    main()
