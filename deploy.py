#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deploy.py — 대상 기기(Note20 등)에서 git clone '이후' 실행하는 세팅 마법사 (멱등).

배포 모델: 대상에서 `git clone <repo>` → `cd sauron_rb2` → `python3 deploy.py`.
전송은 git이 담당(추적 파일만 = secrets 실값·notice.db 제외). 이 스크립트는 그 위에서
가상환경·의존성·secrets·DB·디스코드 채널·시딩까지 한 번에 세팅한다. 여러 번 돌려도 안전.
각 단계는 실패하면 즉시 중단(다음 단계로 진행 안 함). 단, 의도적 스킵(토큰 미입력,
setup_guild 진행 '아니오')은 실패가 아니라 건너뛰기로 계속 진행한다.

단계:
  1) 가상환경 생성 여부(Y) + 이름(기본 .venv)   — 이미 있으면 재사용
  2) 활성 venv로 pip install -r requirements.txt (discord.py 포함, 설치 한 방)
  3) secrets 입력: 봇 토큰 / DISCORD_GUILD_ID / LLM_BASE_URL
       기본값 = 기존 파일 값(있으면), LLM_BASE_URL 없으면 http://localhost:8000/v1
  4) DB 초기화: init/seed_db.py (schema + depts_seed.csv의 학과 upsert)
       ★ setup_guild가 depts 테이블을 읽어 학과 채널을 만들므로 반드시 이 단계가 먼저.
  5) setup_guild --dry → 만들 게 있으면 확인 후 실제 생성(통합·감시·학과 채널/역할)
  6) 시딩: main.py once --dst null --nosummary (현재 공지를 '본 것'으로 기록, 무발송)
  7) `main.py run --dst poly` 는 실행하지 않고 안내만.

표준 라이브러리만 사용. 대상(POSIX) 기준.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(ROOT, "secrets")
CONFIG_JSON = os.path.join(SECRETS, "config.json")
TOKEN_JSON = os.path.join(SECRETS, "discord-api-info.json")
DEFAULT_LLM = "http://localhost:8000/v1"


# ── 입출력 헬퍼 ───────────────────────────────────────
def ask(label, default="", secret=False):
    if secret and default:
        v = input(f"{label} [Enter=기존값 유지]: ").strip()
        return v or default
    suffix = f" [{default}]" if default else ""
    v = input(f"{label}{suffix}: ").strip()
    return v or default


def yn(label, default_yes=True):
    d = "Y/n" if default_yes else "y/N"
    v = input(f"{label} [{d}]: ").strip().lower()
    if not v:
        return default_yes
    return v in ("y", "yes")


def run(cmd, **kw):
    print("  $ " + " ".join(cmd))
    return subprocess.run(cmd, **kw)


def die(msg):
    """단계 실패 → 즉시 중단(다음 단계로 넘어가지 않음)."""
    print(f"    ✗ {msg}")
    print("    → 중단합니다. 원인 해결 후 deploy.py 를 다시 실행하세요(멱등).")
    sys.exit(1)


def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ── 단계 ─────────────────────────────────────────────
def step_venv():
    """(venv_py, using_venv). venv 없이 진행하면 시스템 python."""
    if not yn("\n[1] 가상환경을 만들까요?", True):
        print("    → 시스템 파이썬 사용")
        return sys.executable, False
    name = ask("    가상환경 이름", ".venv")
    vdir = os.path.join(ROOT, name)
    py = os.path.join(vdir, "bin", "python")
    if os.path.exists(py):
        print(f"    → 이미 존재, 재사용: {name}")
    else:
        r = run([sys.executable, "-m", "venv", vdir])
        if r.returncode != 0 or not os.path.exists(py):
            die("venv 생성 실패 (python3-venv 설치 필요할 수 있음: apt install python3.12-venv)")
        print(f"    → 생성됨: {name}")
    return py, True


def step_pip(py):
    print("\n[2] 의존성 설치 (pip install -r requirements.txt)")
    run([py, "-m", "pip", "install", "-U", "pip"])
    r = run([py, "-m", "pip", "install", "-r", os.path.join(ROOT, "requirements.txt")])
    if r.returncode != 0:
        die("pip 설치 실패 — 로그 확인 후 재실행하세요.")


def step_secrets():
    print("\n[3] secrets 입력 (Enter=기본값)")
    cfg = load_json(CONFIG_JSON)
    tok = load_json(TOKEN_JSON)

    bot_token = ask("    디스코드 봇 토큰", tok.get("bot_token", ""), secret=True)
    guild_id = ask("    DISCORD_GUILD_ID(서버 ID)", str(cfg.get("DISCORD_GUILD_ID", "")))
    llm_url = ask("    LLM_BASE_URL", str(cfg.get("LLM_BASE_URL", "") or DEFAULT_LLM))

    tok["bot_token"] = bot_token
    cfg["DISCORD_GUILD_ID"] = guild_id
    cfg["LLM_BASE_URL"] = llm_url
    write_json(TOKEN_JSON, tok)
    write_json(CONFIG_JSON, cfg)
    print("    → secrets/config.json, secrets/discord-api-info.json 저장")
    if not guild_id:
        print("    ⚠ DISCORD_GUILD_ID가 비어있음 → 4단계(setup_guild)는 건너뜁니다.")
    return bool(guild_id and bot_token)


def step_db(py):
    print("\n[4] DB 초기화 (schema + 학과 시드) — setup_guild가 depts를 읽으므로 먼저")
    print("    학과 60여 개 정보의 출처: init/depts_seed.csv (git 추적) → seed_db.py가 depts 테이블로 upsert.")
    r = run([py, os.path.join("init", "seed_db.py")], cwd=ROOT)
    if r.returncode != 0:
        die("seed_db 실패.")


def step_setup_guild(py, can_run):
    print("\n[5] 디스코드 채널/역할 (setup_guild)")
    if not can_run:
        print("    → 토큰/서버ID 미비로 건너뜀(실패 아님). 나중에: python -m notify.setup_guild")
        return
    r = run([py, "-m", "notify.setup_guild", "--dry"], cwd=ROOT,
            capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    print(out.rstrip())
    if r.returncode != 0 or "[오류]" in out or "길드 ID 없음" in out:
        die("setup_guild --dry 실패(토큰/서버ID/봇 초대/네트워크 확인).")
    # 생성 예정 항목이 있는가: dry에서 '...생성] ... (dry)' 라인 존재 여부
    need = any(("(dry)" in ln and "생성]" in ln) for ln in out.splitlines())
    if not need:
        print("    → 이미 모두 세팅됨(생성할 채널/역할 없음).")
        return
    if yn("    위 항목을 이대로 생성하며 진행하시겠습니까?", False):
        r2 = run([py, "-m", "notify.setup_guild"], cwd=ROOT)
        if r2.returncode != 0:
            die("setup_guild 실제 생성 실패.")
    else:
        print("    → 사용자 선택으로 건너뜀(실패 아님). 나중에: python -m notify.setup_guild")


def step_seed_notices(py):
    print("\n[6] 시딩 (main.py once --dst null --nosummary) — 현재 공지를 '본 것'으로 기록, 무발송")
    r = run([py, "main.py", "once", "--dst", "null", "--nosummary"], cwd=ROOT)
    if r.returncode != 0:
        die("시딩(once) 실패(네트워크/사이트 확인).")


def step_done(py, using_venv):
    act = "source .venv/bin/activate && " if using_venv else ""
    print("\n✅ 세팅 완료. 상시 운영은 아래를 직접 실행하세요(자동 실행 안 함):")
    print(f"    {act}python main.py run --dst poly")
    print("    (통합채널로만 보내려면 --dst mono, 각 학과 채널은 --dst poly)")


def main():
    os.chdir(ROOT)
    print("=== sauron_rb2 세팅 (deploy.py) — 멱등, 여러 번 실행 가능 ===")
    py, using_venv = step_venv()
    step_pip(py)
    can_guild = step_secrets()
    step_db(py)                 # setup_guild가 depts 테이블을 읽으므로 먼저
    step_setup_guild(py, can_guild)
    step_seed_notices(py)
    step_done(py, using_venv)


if __name__ == "__main__":
    main()
