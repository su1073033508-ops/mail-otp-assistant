"""配置文件读写 + 授权码（DPAPI）存取。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import dpapi

APP_DATA_DIR_NAME = "邮箱验证码助手"


def data_dir() -> Path:
    """数据目录：源码运行时用项目目录；打包后（或设了 MAIL_OTP_DATA_DIR）用 %APPDATA%。"""
    override = os.environ.get("MAIL_OTP_DATA_DIR")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home()) / APP_DATA_DIR_NAME
    return Path(__file__).resolve().parent.parent


ROOT = data_dir()
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
LOG_DIR = ROOT / "logs"
LOG_PATH = LOG_DIR / "app.log"

PROVIDERS = {
    "qq": {"host": "imap.qq.com", "port": 993, "needs_id": False, "help": "QQ 邮箱"},
    "163": {"host": "imap.163.com", "port": 993, "needs_id": True, "help": "网易 163"},
    "126": {"host": "imap.126.com", "port": 993, "needs_id": True, "help": "网易 126"},
    "yeah": {"host": "imap.yeah.net", "port": 993, "needs_id": True, "help": "网易 yeah.net"},
}

DEFAULT_CONFIG = {
    "version": 1,
    "poll_seconds": 60,
    "initial_baseline": True,
    "max_body_bytes": 400000,
    "max_mails_per_folder": 25,
    "max_codes_per_mail": 3,
    "folder_ttl_seconds": 300,
    "popup_seconds": 40,
    "clipboard_clear_seconds": 60,
    "same_code_quiet_seconds": 300,
    "seen_ttl_hours": 48,
    "hide_tray": False,
    "mode": "auto",
    "manual_window_seconds": 300,
    "manual_poll_seconds": 10,
    "skip_folders": ["已发送", "已删除", "草稿箱", "Sent", "Sent Messages", "Drafts", "Deleted Messages"],
    "accounts": [],
}

_lock = threading.RLock()


class ConfigError(RuntimeError):
    pass


class SecretMissing(ConfigError):
    pass


def default_config() -> dict:
    return json.loads(json.dumps(DEFAULT_CONFIG))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def load(path: Path | None = None) -> dict:
    """读配置；不存在就写默认值。坏文件会被备份后重置。"""
    path = Path(path or CONFIG_PATH)
    with _lock:
        if not path.exists():
            cfg = default_config()
            save(cfg, path)
            return cfg
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            backup = path.with_suffix(path.suffix + f".broken-{int(time.time())}")
            try:
                path.replace(backup)
            except OSError:
                pass
            cfg = default_config()
            save(cfg, path)
            raise ConfigError(f"config.json 解析失败，已备份到 {backup.name}：{exc}") from exc
    cfg = default_config()
    cfg.update(raw if isinstance(raw, dict) else {})
    if not isinstance(cfg.get("accounts"), list):
        cfg["accounts"] = []
    for acct in cfg["accounts"]:
        acct.setdefault("enabled", True)
        acct.setdefault("folders", "auto")
        acct.setdefault("ssl", True)
        acct.setdefault("password_dpapi", "")
    return cfg


def save(cfg: dict, path: Path | None = None) -> None:
    path = Path(path or CONFIG_PATH)
    with _lock:
        _atomic_write(path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")


def find_account(cfg: dict, key: str) -> dict | None:
    """按 id / label / user 模糊找账号。"""
    key = (key or "").strip()
    if not key:
        return None
    low = key.lower()
    for acct in cfg.get("accounts", []):
        for field in ("id", "label", "user"):
            if str(acct.get(field, "")).lower() == low:
                return acct
    for acct in cfg.get("accounts", []):
        for field in ("label", "user"):
            if low in str(acct.get(field, "")).lower():
                return acct
    return None


def account_label(acct: dict) -> str:
    return str(acct.get("label") or acct.get("id") or acct.get("user") or "账号")


def set_secret(acct: dict, plain: str) -> None:
    if not plain:
        raise ValueError("授权码为空")
    acct["password_dpapi"] = dpapi.protect(plain)
    # 顺手清掉可能残留的明文字段
    for bad in ("password", "passwd", "pw", "auth_code", "code"):
        acct.pop(bad, None)


def get_secret(acct: dict) -> str:
    token = acct.get("password_dpapi") or ""
    if not token:
        raise SecretMissing(f"账号「{account_label(acct)}」还没填授权码")
    try:
        return dpapi.unprotect(token)
    except OSError as exc:
        raise SecretMissing(
            f"账号「{account_label(acct)}」的授权码解不开（{exc}）。"
            "若换过 Windows 用户或重装过系统，请重新运行 set_password.py"
        ) from exc


def has_secret(acct: dict) -> bool:
    try:
        return bool(get_secret(acct))
    except SecretMissing:
        return False


def new_account(provider: str, user: str, label: str = "") -> dict:
    spec = PROVIDERS.get(provider)
    if not spec:
        raise ValueError(f"不认识的邮箱类型：{provider}")
    user = user.strip()
    if not label:
        label = f"{spec['help']} {user.split('@')[0]}"
    return {
        "id": f"{provider}-{user.split('@')[0]}",
        "label": label,
        "provider": provider,
        "host": spec["host"],
        "port": spec["port"],
        "ssl": True,
        "needs_id": spec["needs_id"],
        "user": user,
        "password_dpapi": "",
        "folders": "auto",
        "enabled": True,
    }


def unique_account_id(cfg: dict, base: str) -> str:
    existing = {str(a.get("id", "")) for a in cfg.get("accounts", [])}
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"
