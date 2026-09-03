# -*- coding: utf-8 -*-
"""
notify/discord_bot.py — 구독 봇 (C안, 임베드+3단계). discord.py 2.x.

`/구독` → ① 공통(scatch 전교공지, 한 화면) → ② 전공(단과대→학과, '다른 단과대'로 반복) → ③ 기타
각 단계의 Select 선택은 즉시 역할 부여/회수 + subscriptions DB 반영(부분드롭 방지). 전부 ephemeral.

- kind('general'|'major'|'etc')로 단계를 나눠, 서로 다른 단과대 다전공도 /구독 한 번으로 처리.
- 전공은 Discord Select 25개 한계를 단과대 그룹핑으로 우회. '← 다른 단과대'로 여러 단과대 반복 선택.
- 임베드 스타일: 갤러리 H(공통)·I(전공)·J(완료)·K(현황). 색상으로 단계 구분.

`/상태` → 크롤러(생존·하트비트·직전 신규) + 요약 LLM 백엔드(모델·응답시간·가동) + 요약 대기 건수.

⚠ 인터랙션 3초 규칙: 디스코드는 인터랙션 생성 후 3초 안에 첫 응답이 없으면 토큰을 폐기한다
  (10062 Unknown interaction → 사용자에겐 '앱이 응답하지 않음'. 봇은 온라인이라 더 헷갈린다).
  그래서 모든 핸들러는 DB·LLM·역할부여 전에 _ack()로 먼저 defer 하고 followup/edit_original_response 로 답한다.

실행: python -m notify.discord_bot   (secrets/discord-api-info.json 의 bot_token 필요)
권한: 봇에 Manage Roles + 대상 역할들보다 봇 역할이 상위.
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

import config
from core import runstatus
from db.store import Store
from notify.subscribe_logic import group_by_college, dept_select_options, diff_for_subset
from summarize.llm import probe_backend

try:
    import discord
    from discord import app_commands
    _DISCORD = True
except ImportError:
    _DISCORD = False

log = logging.getLogger("sauron.bot")

# 단계별 색상(임베드 왼쪽 막대)
C_GENERAL = 0xF23F43   # 공통(학사 계열) 빨강
C_MAJOR   = 0x5865F2   # 전공 블러플
C_ETC     = 0x949BA4   # 기타 회색
C_DONE    = 0x23A55A   # 완료 초록
C_INFO    = 0x1ABC9C   # 현황 청록

STEP_GENERAL, STEP_MAJOR, STEP_ETC = "general", "major", "etc"


def _load_token():
    path = config.DISCORD_TOKEN_FILE
    if not os.path.exists(path):
        raise RuntimeError(f"봇 토큰 파일 없음: {path}")
    with open(path, encoding="utf-8") as f:
        tok = json.load(f).get("bot_token")
    if not tok:
        raise RuntimeError("bot_token 비어있음")
    return tok


def _setup_logging():
    # 콘솔 + logs/bot.log(일자별 회전). 크롤러(sauron.log)와 파일 분리(프로세스별 회전 충돌 방지).
    from core.log import setup_logger
    lg = setup_logger("sauron.bot", "bot.log")
    # discord.py 자체 로거도 같은 파일로. client.run(log_handler=None)이라 우리가 붙이지 않으면
    # 게이트웨이 재연결·하트비트 지연 경고가 아무데도 안 남는다(sauron.sh가 stderr를 /dev/null로 보냄).
    # '봇은 온라인인데 명령에 무응답'의 원인은 대부분 여기 찍힌다.
    dlog = logging.getLogger("discord")
    if not dlog.handlers:
        for h in lg.handlers:
            dlog.addHandler(h)
        dlog.setLevel(logging.WARNING)
        dlog.propagate = False
    return lg


if _DISCORD:

    # ── 공통 유틸 ──────────────────────────────────────
    def _name(d):
        return d.get("name_ko") or d["dept_id"]

    def _lag_ms(interaction):
        """인터랙션이 디스코드에서 만들어진 뒤 우리 손에 들어오기까지 걸린 시간(ms).
        토큰 유효기간은 '생성 후 3초'이므로, 이 값이 이미 3000에 가까우면 우리 코드가 아니라
        게이트웨이 수신 지연이 원인이다(봇은 온라인인데 무응답 → 10062)."""
        try:
            return int((discord.utils.utcnow() - interaction.created_at).total_seconds() * 1000)
        except Exception:
            return -1

    async def _ack(interaction, ephemeral=True, thinking=True):
        """무엇을 하기 전에 먼저 '접수'(defer). 디스코드는 인터랙션 생성 후 3초 안에 첫 응답이
        없으면 토큰을 버리고(10062 Unknown interaction) 사용자에겐 '앱이 응답하지 않음'만 남는다.
        DB·LLM·역할부여는 3초를 넘길 수 있으므로 전부 defer 뒤에 한다.
        반환 False = 이미 만료된 인터랙션(수신 지연) → 호출부는 조용히 포기."""
        try:
            await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
            return True
        except discord.NotFound:      # 10062 — 우리가 늦은 게 아니라 이벤트가 늦게 왔다
            log.warning("만료된 인터랙션(defer 실패) type=%s lag=%dms",
                        interaction.type, _lag_ms(interaction))
            return False
        except discord.HTTPException as e:
            log.warning("defer 실패: %s", e)
            return False

    def _current_summary(store, uid):
        """현재 구독을 kind별로 정리. 반환: {'general':[name], 'major':OrderedDict{col:[name]}, 'etc':[name]}"""
        subs = set(store.user_subscriptions(uid))
        out = {"general": [], "major": group_by_college([]).__class__(), "etc": []}
        for d in store.active_depts():
            if d["dept_id"] not in subs:
                continue
            k = d.get("kind") or "major"
            if k == "major":
                col = (d.get("college") or "기타").strip() or "기타"
                out["major"].setdefault(col, []).append(_name(d))
            elif k == "general":
                out["general"].append(_name(d))
            else:
                out["etc"].append(_name(d))
        return out

    async def _apply_selection(interaction, store, subset_ids, selected_ids):
        """subset 안에서 선택=구독/미선택=해제. 역할 부여·회수 + DB. 반환: (added, removed, failed)."""
        uid = str(interaction.user.id)
        current = store.user_subscriptions(uid)
        diff = diff_for_subset(subset_ids, selected_ids, current)
        guild = interaction.guild
        added, removed, failed = [], [], []
        for dept_id in diff["add"]:
            d = store.get_dept(dept_id)
            role = guild.get_role(int(d["discord_role_id"])) if d and d.get("discord_role_id") else None
            try:
                if role:
                    await interaction.user.add_roles(role, reason="공지 구독")
                store.add_subscription(uid, dept_id)
                added.append(_name(d) if d else dept_id)
                log.info("구독+ uid=%s dept=%s", uid, dept_id)
            except discord.Forbidden:
                failed.append(_name(d) if d else dept_id)
                log.warning("역할부여 실패(권한) uid=%s dept=%s role=%s", uid, dept_id, d.get("discord_role_id"))
        for dept_id in diff["remove"]:
            d = store.get_dept(dept_id)
            role = guild.get_role(int(d["discord_role_id"])) if d and d.get("discord_role_id") else None
            try:
                if role:
                    await interaction.user.remove_roles(role, reason="구독 해제")
            except discord.Forbidden:
                log.warning("역할회수 실패(권한) uid=%s dept=%s", uid, dept_id)
            store.remove_subscription(uid, dept_id)
            removed.append(_name(d) if d else dept_id)
            log.info("구독- uid=%s dept=%s", uid, dept_id)
        return added, removed, failed

    def _saved_note(store, uid, failed):
        # 변경사항(+/-) 대신 '현재 전체 개수'로 표시(우리 DB 기준, 외부 역할 조회 불필요).
        n = len(store.user_subscriptions(uid))
        line = f"현재 {n}개 구독 중"
        if failed:
            line += f"\n⚠️ 역할 권한 부족(봇 역할 위치 확인): {', '.join(failed)}"
        return line

    # ── 상태(/상태) ────────────────────────────────────
    #   크롤러(main.py run) 생존/하트비트는 core.runstatus, 요약 LLM 백엔드는 summarize.llm.probe_backend
    #   로 조회한다. presence(봇 상태메시지)는 BOT_PRESENCE_INTERVAL_SEC 주기로 갱신.
    _STATE = {
        "running": ("🟢 정상 작동 중", 0x23A55A, "공지 감시 중"),
        "stale":   ("🟡 응답 없음(멈춤·붕괴 의심)", 0xF0B232, "크롤러 멈춤 의심"),
        "stopped": ("🔴 정지", 0xF23F43, "크롤러 정지"),
    }

    def _fmt_dur(sec):
        if sec is None:
            return "-"
        sec = int(sec)
        h, m = sec // 3600, (sec % 3600) // 60
        if h:
            return f"{h}시간 {m}분"
        if m:
            return f"{m}분"
        return f"{sec}초"

    # 백엔드 status 문자열이 이 중 하나면 '정상'. 그 외(loading/degraded 등)는 노랑으로 구분.
    _LLM_OK_STATUS = ("ok", "ready", "healthy", "up", "running")

    def _llm_value(llm):
        """LLM 백엔드 필드 값(2줄): 모델 + (호스트·응답시간·가동시간) 또는 실패 사유."""
        if not llm:
            return "⚪ 조회 실패"
        host = (llm.get("url") or "").split("//")[-1].split("/")[0] or "-"
        if not llm.get("ok"):
            return f"🔴 응답 없음 · {llm.get('error') or '원인 불명'}\n`{host}`"
        status = (llm.get("status") or "ok").lower()
        icon = "🟢" if status in _LLM_OK_STATUS else "🟡"
        tail = [f"`{host}`"]
        if llm.get("latency_ms") is not None:
            tail.append(f"응답 {llm['latency_ms']}ms")
        if llm.get("uptime"):
            tail.append(f"가동 {_fmt_dur(llm['uptime'])}")
        if icon == "🟡":
            tail.append(f"status={llm.get('status')}")
        return f"{icon} {llm.get('model') or '모델 미상'}\n" + " · ".join(tail)

    def _status_embed(st, llm=None, pending=None):
        title, color, _ = _STATE.get(st["state"], ("⚪ 상태 미상", 0x949BA4, "상태 미상"))
        e = discord.Embed(title=title, color=color)
        if st.get("since_beat") is None:      # heartbeat 기록 자체가 없음
            e.description = "크롤러 실행 이력이 없습니다(한 번도 안 돌았거나 DB 초기화됨)."
        else:
            if st.get("alive"):
                e.add_field(name="가동 시간", value=_fmt_dur(st.get("uptime")), inline=True)
            e.add_field(name="마지막 활동", value=f"{_fmt_dur(st.get('since_beat'))} 전", inline=True)
            if st.get("pid"):
                e.add_field(name="PID", value=str(st["pid"]), inline=True)
            if st.get("last_new") is not None:
                e.add_field(name="직전 크롤 신규", value=f"{st['last_new']}건", inline=True)
        e.add_field(name="요약 LLM", value=_llm_value(llm), inline=False)
        if pending is not None:
            e.add_field(name="요약 대기", value=f"{pending}건", inline=True)
        return e

    def _collect_status(store):
        """/상태 표시용 3종(크롤러·LLM·대기건수)을 한 번의 스레드 홉에서 수집. 예외는 부분 실패로 흡수."""
        st = runstatus.read_status(store, config.RUN_STALE_SEC)
        llm = probe_backend(timeout=config.LLM_STATUS_TIMEOUT)   # 예외 없음(상태 dict 반환)
        try:
            pending = len(store.pending_summary_ids())
        except Exception as e:
            log.warning("요약 대기 건수 조회 실패: %s", e)
            pending = None
        return st, llm, pending

    # ── Select 컴포넌트 ────────────────────────────────
    class DeptMultiSelect(discord.ui.Select):
        """한 묶음(공통/한 단과대/기타)의 학과 다중선택. 선택 즉시 저장 후 같은 단계 재렌더."""
        def __init__(self, store, depts, subscribed, placeholder, step, college=None):
            self.store, self.step, self.college = store, step, college
            self.dept_ids = [d["dept_id"] for d in depts]
            opts, _ = dept_select_options(depts, subscribed)
            options = [discord.SelectOption(label=o["label"], value=o["value"], default=o["default"])
                       for o in opts]
            super().__init__(placeholder=placeholder, min_values=0,
                             max_values=len(options), options=options)

        async def callback(self, interaction: discord.Interaction):
            # 선택 학과가 많으면 add_roles/remove_roles 왕복이 3초를 쉽게 넘긴다 → 먼저 defer.
            if not await _ack(interaction, thinking=False):
                return
            uid = str(interaction.user.id)
            _, _, failed = await _apply_selection(interaction, self.store, self.dept_ids, self.values)
            note = _saved_note(self.store, uid, failed)
            log.info("단계=%s 저장 uid=%s :: %s", self.step, interaction.user.id, note.replace("\n", " / "))
            # 같은 단계 재렌더(체크 반영) + 저장 결과 표시
            if self.step == STEP_GENERAL:
                embed, view = _render_general(self.store, str(interaction.user.id), note)
            elif self.step == STEP_ETC:
                embed, view = _render_etc(self.store, str(interaction.user.id), note)
            else:  # major dept
                embed, view = _render_major_dept(self.store, str(interaction.user.id), self.college, note)
            await interaction.edit_original_response(embed=embed, view=view)

    class CollegeSelect(discord.ui.Select):
        def __init__(self, store, colleges):
            self.store = store
            options = [discord.SelectOption(label=c[:100], value=c) for c in colleges[:25]]
            super().__init__(placeholder="단과대를 고르세요", min_values=1, max_values=1, options=options)

        async def callback(self, interaction: discord.Interaction):
            if not await _ack(interaction, thinking=False):
                return
            college = self.values[0]
            log.info("전공 단과대선택 uid=%s college=%s", interaction.user.id, college)
            embed, view = _render_major_dept(self.store, str(interaction.user.id), college)
            await interaction.edit_original_response(embed=embed, view=view)

    # ── 단계 네비 버튼 ─────────────────────────────────
    class NavButton(discord.ui.Button):
        def __init__(self, label, target, store, style=discord.ButtonStyle.secondary, college=None):
            super().__init__(label=label, style=style)
            self.target, self.store, self.college = target, store, college

        async def callback(self, interaction: discord.Interaction):
            if not await _ack(interaction, thinking=False):
                return
            uid = str(interaction.user.id)
            log.info("네비 uid=%s → %s", interaction.user.id, self.target)
            if self.target == "general":
                embed, view = _render_general(self.store, uid)
            elif self.target == "major_college":
                embed, view = _render_major_college(self.store, uid)
            elif self.target == "etc":
                embed, view = _render_etc(self.store, uid)
            else:  # done
                embed, view = _render_done(self.store, uid)
            await interaction.edit_original_response(embed=embed, view=view)

    # ── 단계별 (embed, view) 렌더 ──────────────────────
    def _render_general(store, uid, note=None):
        depts = store.depts_by_kind(STEP_GENERAL, with_role=True)
        subs = store.user_subscriptions(uid)
        desc = ("전교 공통 · 학사·장학·채용 등\n**학사 구독 권장** · 항목 선택 후 **다음**"
                if depts else "준비된 공통 채널 없음 (관리자 `setup_guild` 필요)")
        if note:
            desc += f"\n\n✅ {note}"
        embed = discord.Embed(title="① 공통 공지", description=desc, color=C_GENERAL)
        embed.set_footer(text="1/3 · 버튼으로 언제든 재변경")
        view = discord.ui.View(timeout=180)
        if depts:
            view.add_item(DeptMultiSelect(store, depts, subs, "공통 공지 고르기(복수 가능)", STEP_GENERAL))
        view.add_item(NavButton("다음 (전공) →", "major_college", store, discord.ButtonStyle.primary))
        return embed, view

    def _render_major_college(store, uid):
        depts = store.depts_by_kind(STEP_MAJOR, with_role=True)
        colleges = list(group_by_college(depts).keys())
        desc = ("**단과대 선택** → 학과 선택\n여러 단과대는 **← 다른 단과대**로 반복"
                if colleges else "준비된 전공 채널 없음")
        embed = discord.Embed(title="② 전공 · 단과대", description=desc, color=C_MAJOR)
        embed.set_footer(text="2/3")
        view = discord.ui.View(timeout=180)
        if colleges:
            view.add_item(CollegeSelect(store, colleges))
        view.add_item(NavButton("← 이전 (공통)", "general", store))
        view.add_item(NavButton("건너뛰기 (기타) →", "etc", store, discord.ButtonStyle.primary))
        return embed, view

    def _render_major_dept(store, uid, college, note=None):
        allmajor = store.depts_by_kind(STEP_MAJOR, with_role=True)
        depts = group_by_college(allmajor).get(college, [])
        subs = store.user_subscriptions(uid)
        desc = f"**{college}** · 학과 선택 (현재 구독 미리 체크)"
        if note:
            desc += f"\n\n✅ {note}"
        embed = discord.Embed(title=f"② 전공 · {college}", description=desc, color=C_MAJOR)
        embed.set_footer(text="2/3 · ← 다른 단과대 반복 가능")
        view = discord.ui.View(timeout=180)
        if depts:
            view.add_item(DeptMultiSelect(store, depts, subs, f"{college} 학과 선택(복수 가능)",
                                          STEP_MAJOR, college=college))
        view.add_item(NavButton("← 다른 단과대", "major_college", store))
        view.add_item(NavButton("다음 (기타) →", "etc", store, discord.ButtonStyle.primary))
        return embed, view

    def _render_etc(store, uid, note=None):
        depts = store.depts_by_kind(STEP_ETC, with_role=True)
        subs = store.user_subscriptions(uid)
        desc = "창업 등 기타 공지 · 필요 시 선택" if depts else "기타 항목 없음 · 바로 완료 가능"
        if note:
            desc += f"\n\n✅ {note}"
        embed = discord.Embed(title="③ 기타 공지", description=desc, color=C_ETC)
        embed.set_footer(text="3/3")
        view = discord.ui.View(timeout=180)
        if depts:
            view.add_item(DeptMultiSelect(store, depts, subs, "기타 공지 고르기", STEP_ETC))
        view.add_item(NavButton("← 이전 (전공)", "major_college", store))
        view.add_item(NavButton("완료 ✓", "done", store, discord.ButtonStyle.success))
        return embed, view

    def _render_done(store, uid):
        s = _current_summary(store, uid)
        major_names = [n for names in s["major"].values() for n in names]  # 단과대 그룹 무시, name_ko 평면화
        total = len(s["general"]) + len(major_names) + len(s["etc"])
        lines = []
        if s["general"]:
            lines.append("- **공통**")
            lines += [f"  - {n}" for n in s["general"]]
        if major_names:
            lines.append("- **학과별 공지**")
            lines += [f"  - {n}" for n in major_names]
        if s["etc"]:
            lines.append("- **기타**")
            lines += [f"  - {n}" for n in s["etc"]]
        body = f"현재 **{total}개** 구독 중"
        body += ("\n" + "\n".join(lines)) if lines else "\n구독한 항목 없음 · 버튼으로 다시 선택 가능"
        embed = discord.Embed(title="✅ 구독 완료", color=C_DONE, description=body)
        view = discord.ui.View(timeout=180)
        view.add_item(NavButton("구독 다시 편집", "general", store, discord.ButtonStyle.secondary))
        return embed, view

    # ── 진입(공개 버튼 A) ──────────────────────────────
    async def _open_flow(interaction, store):
        """/구독 및 공개 버튼의 공통 진입: 구독 가능 항목 확인 후 ①공통 단계를 ephemeral로 연다.
        thinking=True 로 defer → followup 은 '새 ephemeral 메시지'라 공개 버튼 메시지는 그대로 남는다."""
        if not await _ack(interaction):
            return
        log.info("구독 진입 uid=%s(%s) lag=%dms", interaction.user.id, interaction.user, _lag_ms(interaction))
        any_role = any(store.depts_by_kind(k, with_role=True) for k in (STEP_GENERAL, STEP_MAJOR, STEP_ETC))
        if not any_role:
            await interaction.followup.send(
                "아직 구독 가능한 항목(역할)이 없어요. 관리자가 `setup_guild` 를 먼저 실행해야 합니다.", ephemeral=True)
            return
        embed, view = _render_general(store, str(interaction.user.id))
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    def _entry_embed():
        """공개 채널에 상주하는 안내 임베드 A(간결·마크다운). 사용자는 버튼만 누르면 됨."""
        e = discord.Embed(
            title="🔔 공지 구독",
            description=("아래 **버튼**을 눌러 받고 싶은 공지 선택\n"
                         "· **공통** — 학사·장학·채용 등 전교 공지\n"
                         "· **전공** — 내 학과\n"
                         "· **기타** — 창업 등\n\n"
                         "구독하면 해당 **전용 채널**로 새 공지 자동 전달\n\n"
                         "> 버튼은 여러 번 눌러 언제든 변경 가능"),
            color=C_GENERAL)
        return e

    class SubscribeEntryView(discord.ui.View):
        """공개 채널 상주용 영구 View(timeout=None). 버튼 custom_id로 재시작 후에도 동작."""
        def __init__(self, store):
            super().__init__(timeout=None)
            self.store = store

        @discord.ui.button(label="공지 구독하기 🔔", style=discord.ButtonStyle.primary,
                           custom_id="sauron:sub:open")
        async def open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            await _open_flow(interaction, self.store)

    # ── 봇 실행 ────────────────────────────────────────
    def run_bot():
        _setup_logging()
        store = Store(config.DB_PATH)
        debug = config.debug_from_argv(sys.argv)
        gid = config.active_guild_id(debug)
        intents = discord.Intents.default()
        client = discord.Client(intents=intents)
        tree = app_commands.CommandTree(client)
        guild_obj = discord.Object(id=int(gid)) if gid else None
        _persist = {"added": False, "presence": False}   # 영구 View·presence 루프 중복시작 방지

        async def _presence_loop():
            # 크롤러 상태를 봇 presence로 표시(최대 1주기 늦음 → 실시간은 /상태).
            # 겸 워밍업: 주기적으로 DB·pgrep 경로를 밟아 프로세스가 스왑으로 밀려나지 않게 한다
            # (밀려나면 첫 슬래시 명령이 3초 안에 응답 못 해 10062로 무응답이 된다).
            while True:
                try:
                    st = await asyncio.to_thread(runstatus.read_status, store, config.RUN_STALE_SEC)
                    emoji = {"running": "🟢", "stale": "🟡", "stopped": "🔴"}.get(st["state"], "⚪")
                    label = _STATE.get(st["state"], ("", 0, "상태 미상"))[2]
                    await client.change_presence(activity=discord.CustomActivity(name=f"{emoji} {label}"))
                except Exception as e:
                    log.warning("presence 갱신 실패: %s", e)
                await asyncio.sleep(config.BOT_PRESENCE_INTERVAL_SEC)

        @client.event
        async def on_ready():
            # 재시작 후에도 공개 버튼이 동작하도록, 로그인 이후 1회 영구 View 등록(custom_id 매칭).
            #   run() 이전에 add_view 하면 게이트웨이 연결 초기화로 등록이 날아감 → 반드시 on_ready에서.
            if not _persist["added"]:
                client.add_view(SubscribeEntryView(store))
                _persist["added"] = True
            if not _persist["presence"]:
                _persist["presence"] = True
                asyncio.create_task(_presence_loop())
            await (tree.sync(guild=guild_obj) if guild_obj else tree.sync())
            log.info("로그인: %s | %s 서버(%s)", client.user,
                     "디버깅" if debug else "실서비스", gid or "전역")
            n = {k: len(store.depts_by_kind(k, with_role=True)) for k in (STEP_GENERAL, STEP_MAJOR, STEP_ETC)}
            log.info("구독가능(역할보유) 학과: 공통 %d · 전공 %d · 기타 %d", n["general"], n["major"], n["etc"])

        @tree.command(name="상태", description="크롤러·요약 LLM의 현재 작동 상태", guild=guild_obj)
        async def status_cmd(interaction: discord.Interaction):
            # DB + pgrep + LLM HTTP 점검이라 3초를 넘길 수 있다 → 먼저 defer, 뒤에 followup.
            if not await _ack(interaction):
                return
            log.info("/상태 uid=%s lag=%dms", interaction.user.id, _lag_ms(interaction))
            st, llm, pending = await asyncio.to_thread(_collect_status, store)
            log.info("/상태 결과 크롤러=%s llm=%s(%s)", st["state"],
                     "ok" if llm.get("ok") else f"fail:{llm.get('error')}", llm.get("model"))
            await interaction.followup.send(embed=_status_embed(st, llm, pending), ephemeral=True)

        @tree.command(name="구독", description="학과·공통 공지 구독을 설정합니다", guild=guild_obj)
        async def subscribe(interaction: discord.Interaction):
            await _open_flow(interaction, store)

        @tree.command(name="구독버튼생성", description="[관리자] 이 채널에 '공지 구독' 안내+버튼을 올립니다",
                      guild=guild_obj)
        @app_commands.default_permissions(manage_guild=True)   # 관리자/서버관리 권한자만 노출
        async def make_entry(interaction: discord.Interaction):
            if not await _ack(interaction):
                return
            log.info("/구독버튼생성 by uid=%s ch=%s", interaction.user.id, interaction.channel_id)
            await interaction.channel.send(embed=_entry_embed(), view=SubscribeEntryView(store))
            await interaction.followup.send("이 채널에 구독 버튼을 올렸어요.", ephemeral=True)

        @tree.error
        async def on_app_error(interaction: discord.Interaction, error):
            orig = getattr(error, "original", error)
            # 10062 = 토큰 만료(3초 초과 또는 이벤트 수신 지연). 안내를 보낼 대상이 이미 없으므로
            # 트레이스백 대신 한 줄만 남긴다(lag 로 우리 지연인지 게이트웨이 지연인지 판별).
            if isinstance(orig, discord.NotFound) and getattr(orig, "code", None) == 10062:
                log.warning("만료된 인터랙션(10062) cmd=%s lag=%dms",
                            getattr(interaction.command, "name", "?"), _lag_ms(interaction))
                return
            log.exception("슬래시 명령 오류: %s", error)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("처리 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.", ephemeral=True)
                else:
                    await interaction.response.send_message("처리 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.", ephemeral=True)
            except Exception:
                pass

        client.run(_load_token(), log_handler=None)  # 우리 로거 사용


def main():
    if not _DISCORD:
        raise SystemExit("discord.py 미설치: pip install -U discord.py")
    run_bot()


if __name__ == "__main__":
    main()
