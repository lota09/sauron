# -*- coding: utf-8 -*-
"""
notify/discord_bot.py — 구독 봇 (C안). discord.py 2.x.

`/구독` → (1단계) 단과대 Select → (2단계) 그 단과대 학과 다중 Select(현재 구독분 기본선택)
 → 선택 확정 시 학과 역할 부여/회수 + subscriptions DB 갱신. 응답은 전부 ephemeral.

Discord Select 25개 한계를 단과대 그룹핑으로 우회. 역할이 아직 없는 학과(role_id 없음)는 목록에서 제외.
실행: python -m notify.discord_bot   (secrets/discord-api-info.json 의 bot_token 필요)
권한: 봇에 Manage Roles + 대상 역할들보다 봇 역할이 상위. (members 인텐트는 슬래시 상호작용에선 불필요)
"""
import json
import os
import sys

import config
from db.store import Store
from notify.subscribe_logic import group_by_college, dept_select_options, diff_for_subset

try:
    import discord
    from discord import app_commands
    _DISCORD = True
except ImportError:
    _DISCORD = False


def _load_token():
    path = config.DISCORD_TOKEN_FILE
    if not os.path.exists(path):
        raise RuntimeError(f"봇 토큰 파일 없음: {path}")
    with open(path, encoding="utf-8") as f:
        tok = json.load(f).get("bot_token")
    if not tok:
        raise RuntimeError("bot_token 비어있음")
    return tok


if _DISCORD:

    class DeptSelect(discord.ui.Select):
        """한 단과대의 학과 다중선택."""
        def __init__(self, store, college, depts, subscribed):
            self.store = store
            self.dept_ids = [d["dept_id"] for d in depts]
            opts, dropped = dept_select_options(depts, subscribed)
            options = [discord.SelectOption(label=o["label"], value=o["value"], default=o["default"]) for o in opts]
            super().__init__(placeholder=f"{college} 학과 선택(복수 가능)",
                             min_values=0, max_values=len(options), options=options)

        async def callback(self, interaction: discord.Interaction):
            uid = str(interaction.user.id)
            current = self.store.user_subscriptions(uid)
            diff = diff_for_subset(self.dept_ids, self.values, current)
            guild = interaction.guild
            done_add, done_rm, missing = [], [], []
            for dept_id in diff["add"]:
                d = self.store.get_dept(dept_id)
                role = guild.get_role(int(d["discord_role_id"])) if d and d.get("discord_role_id") else None
                if role:
                    await interaction.user.add_roles(role, reason="구독")
                    self.store.add_subscription(uid, dept_id)
                    done_add.append(d.get("name_ko") or dept_id)
                else:
                    missing.append(dept_id)
            for dept_id in diff["remove"]:
                d = self.store.get_dept(dept_id)
                role = guild.get_role(int(d["discord_role_id"])) if d and d.get("discord_role_id") else None
                if role:
                    await interaction.user.remove_roles(role, reason="구독 해제")
                self.store.remove_subscription(uid, dept_id)
                done_rm.append((d.get("name_ko") if d else dept_id) or dept_id)
            msg = "✅ 구독 반영됨"
            if done_add:
                msg += f"\n＋ 구독: {', '.join(done_add)}"
            if done_rm:
                msg += f"\n－ 해제: {', '.join(done_rm)}"
            if not done_add and not done_rm:
                msg = "변경 사항이 없어요."
            if missing:
                msg += f"\n⚠️ 역할 미생성 학과 제외: {', '.join(missing)}"
            await interaction.response.edit_message(content=msg, view=None)

    class DeptView(discord.ui.View):
        def __init__(self, store, college, depts, subscribed):
            super().__init__(timeout=180)
            self.add_item(DeptSelect(store, college, depts, subscribed))

    class CollegeSelect(discord.ui.Select):
        def __init__(self, store, groups, subscribed, uid):
            self.store, self.groups, self.subscribed = store, groups, subscribed
            options = [discord.SelectOption(label=col[:100], value=col) for col in list(groups)[:25]]
            super().__init__(placeholder="단과대를 고르세요", min_values=1, max_values=1, options=options)

        async def callback(self, interaction: discord.Interaction):
            college = self.values[0]
            depts = self.groups[college]
            current = self.store.user_subscriptions(str(interaction.user.id))
            await interaction.response.edit_message(
                content=f"**{college}** 에서 구독할 학과를 고르세요(현재 구독은 미리 선택됨):",
                view=DeptView(self.store, college, depts, current))

    class CollegeView(discord.ui.View):
        def __init__(self, store, groups, subscribed, uid):
            super().__init__(timeout=180)
            self.add_item(CollegeSelect(store, groups, subscribed, uid))

    def run_bot():
        store = Store(config.DB_PATH)
        debug = config.debug_from_argv(sys.argv)
        gid = config.active_guild_id(debug)
        intents = discord.Intents.default()
        client = discord.Client(intents=intents)
        tree = app_commands.CommandTree(client)
        guild_obj = discord.Object(id=int(gid)) if gid else None

        @client.event
        async def on_ready():
            await (tree.sync(guild=guild_obj) if guild_obj else tree.sync())
            print(f"[bot] 로그인: {client.user} | {'디버깅' if debug else '실서비스'} 서버({gid or '전역'})")

        @tree.command(name="구독", description="학과 공지 구독을 설정합니다", guild=guild_obj)
        async def subscribe(interaction: discord.Interaction):
            depts = [d for d in store.active_depts() if d.get("discord_role_id")]
            if not depts:
                await interaction.response.send_message(
                    "아직 구독 가능한 학과(역할)가 없어요. 관리자가 setup_guild를 먼저 실행해야 합니다.", ephemeral=True)
                return
            groups = group_by_college(depts)
            current = store.user_subscriptions(str(interaction.user.id))
            await interaction.response.send_message(
                "구독할 단과대를 고르세요:", view=CollegeView(store, groups, current, interaction.user.id),
                ephemeral=True)

        client.run(_load_token())


def main():
    if not _DISCORD:
        raise SystemExit("discord.py 미설치: pip install -U discord.py")
    run_bot()


if __name__ == "__main__":
    main()
