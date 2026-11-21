"""持久化状态：每个文件夹的 last_uid、已处理的 Message-ID、最近弹过的验证码。

- 已处理邮件按 Message-ID 记 48 小时，之后清理；
- 同一个验证码 5 分钟内不重复弹。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data: dict = {"folders": {}, "seen": {}, "codes": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            try:
                self.path.replace(self.path.with_suffix(".broken"))
            except OSError:
                pass
            return
        if isinstance(raw, dict):
            for key in ("folders", "seen", "codes"):
                if isinstance(raw.get(key), dict):
                    self.data[key] = raw[key]

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix="state.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass

    # -- 文件夹游标

    def folder(self, key: str) -> dict | None:
        with self.lock:
            return self.data["folders"].get(key)

    def set_folder(self, key: str, value: dict) -> None:
        with self.lock:
            self.data["folders"][key] = value

    # -- 邮件去重

    def seen(self, msgid: str) -> bool:
        with self.lock:
            return msgid in self.data["seen"]

    def mark_seen(self, msgid: str) -> None:
        with self.lock:
            self.data["seen"][msgid] = time.time()

    # -- 同码短时去重

    def code_recent(self, code: str, seconds: float = 300) -> bool:
        with self.lock:
            ts = self.data["codes"].get(code)
            if not ts:
                return False
            return time.time() - float(ts) < seconds

    def mark_code(self, code: str) -> None:
        with self.lock:
            self.data["codes"][code] = time.time()

    # -- 清理

    def purge(self, seen_hours: float = 48, code_hours: float = 2) -> int:
        now = time.time()
        removed = 0
        with self.lock:
            for bucket, hours in (("seen", seen_hours), ("codes", code_hours)):
                cutoff = now - hours * 3600
                dead = [k for k, ts in self.data[bucket].items() if float(ts) < cutoff]
                for k in dead:
                    del self.data[bucket][k]
                removed += len(dead)
        return removed
