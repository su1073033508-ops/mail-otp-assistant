"""日志：写 logs/app.log，绝不写验证码本身。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FMT = "%(asctime)s %(levelname)-7s %(message)s"
_configured = False


def setup(path: Path, level: int = logging.INFO) -> logging.Logger:
    global _configured
    root = logging.getLogger()
    root.setLevel(level)
    if _configured:
        return logging.getLogger("mail_otp")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FMT, datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(fh)
    # 控制台只在有终端时加（pythonw 下 stderr 是 None）
    import sys

    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(sh)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    _configured = True
    return logging.getLogger("mail_otp")


def get() -> logging.Logger:
    return logging.getLogger("mail_otp")
