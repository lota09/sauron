# -*- coding: utf-8 -*-
"""core/log.py — 간단 로거."""
import logging
import sys

_done = False


def setup_logger(name="sauron"):
    global _done
    logger = logging.getLogger(name)
    if not _done:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        _done = True
    return logger
