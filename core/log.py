# -*- coding: utf-8 -*-
"""core/log.py — 콘솔 + 일자별 회전 파일 로거.

파일은 매일 자정에 끊긴다(logs/sauron.log → sauron.log.2026-08-12 …), backupCount일치 보관.
크롤러와 봇은 다른 프로세스이므로 파일을 분리해서 쓴다(회전 충돌 방지):
  · main.py run       → setup_logger("sauron")            → logs/sauron.log
  · notify.discord_bot→ setup_logger("sauron.bot", "bot.log") → logs/bot.log
장시간 실행(run)이면 자정마다 자동으로 새 파일로 넘어간다. 콘솔 출력도 유지(대화형 실행 대비).
"""
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler


def setup_logger(name="sauron", filename=None):
    """(name) 로거를 콘솔+회전파일로 1회 구성해 반환. 같은 name 재호출은 그대로 반환(중복 핸들러 방지)."""
    logger = logging.getLogger(name)
    if getattr(logger, "_sauron_configured", False):
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False   # 부모(root/'sauron')로 전파해 중복 출력되는 것 방지

    # 콘솔(시:분:초)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    logger.addHandler(sh)

    # 파일(전체 타임스탬프, 자정 회전)
    try:
        import config
        logdir = getattr(config, "LOG_DIR", "logs")
        backup = getattr(config, "LOG_BACKUP_DAYS", 14)
    except Exception:
        logdir, backup = "logs", 14
    try:
        os.makedirs(logdir, exist_ok=True)
        fh = TimedRotatingFileHandler(
            os.path.join(logdir, filename or f"{name}.log"),
            when="midnight", backupCount=backup, encoding="utf-8")
        fh.suffix = "%Y-%m-%d"                       # 회전 파일명: sauron.log.YYYY-MM-DD
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(fh)
    except Exception as e:                           # 파일 못 열어도 콘솔은 살린다
        logger.warning("파일 로깅 비활성(%s) — 콘솔만", e)

    logger._sauron_configured = True
    return logger
