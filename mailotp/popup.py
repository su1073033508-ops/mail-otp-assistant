"""右下角验证码弹窗（tkinter）：置顶、紧凑排版、点一下复制、40 秒自动关。"""

from __future__ import annotations

import ctypes
import tkinter as tk
from ctypes import wintypes

from . import dpi

MIN_WIDTH = 238
MAX_WIDTH = 520
BASE_GAP = 8
BASE_MARGIN = 18
MAX_STACK = 5


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def work_area(root: tk.Tk) -> tuple[int, int, int, int]:
    """主屏工作区（排除任务栏）：(x, y, w, h)。失败时退回整屏。"""
    try:
        rect = _Rect()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class PopupManager:
    def __init__(self, root: tk.Tk, clipboard, popup_seconds: int = 40, log=None):
        self.root = root
        self.clip = clipboard
        self.popup_seconds = max(5, int(popup_seconds))
        self.log = log or (lambda *a, **k: None)
        self.scale = dpi.scale_for(root)
        self.gap = int(BASE_GAP * self.scale)
        self.margin = int(BASE_MARGIN * self.scale)
        self.items: list[dict] = []

    def _px(self, value: int) -> int:
        return int(value * self.scale)

    # -- 对外

    def show(self, ev) -> None:
        while len(self.items) >= MAX_STACK:
            self._destroy(self.items[0])

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#0b0f16")

        border = tk.Frame(win, bg="#2c3a52")
        border.pack(fill="both", expand=True)
        body = tk.Frame(border, bg="#12161f")
        body.pack(fill="both", expand=True, padx=1, pady=1)

        pad = self._px(12)

        top = tk.Frame(body, bg="#12161f")
        top.pack(fill="x", padx=pad, pady=(self._px(7), 0))

        site = tk.Label(
            top,
            text=_truncate(ev.site or "未知站点", 22),
            bg="#12161f",
            fg="#9db6d8",
            font=("Microsoft YaHei UI", 11, "bold"),
            anchor="w",
        )
        site.pack(side="left")

        close = tk.Label(
            top,
            text="✕",
            bg="#12161f",
            fg="#4d5c74",
            font=("Segoe UI", 11),
            cursor="hand2",
        )
        close.pack(side="right")

        sender = tk.Label(
            body,
            text=_truncate(f"{ev.account} · {ev.folder} · {ev.sender}", 40),
            bg="#12161f",
            fg="#63718a",
            font=("Microsoft YaHei UI", 8),
            anchor="w",
        )
        sender.pack(fill="x", padx=pad, pady=(self._px(1), 0))

        bottom = tk.Frame(body, bg="#12161f")
        bottom.pack(fill="x", padx=pad, pady=(self._px(1), self._px(6)))

        code = tk.Label(
            bottom,
            text=ev.code,
            bg="#12161f",
            fg="#7ee787",
            font=("Consolas", 24, "bold"),
            anchor="w",
        )
        code.pack(side="left")

        hint = tk.Label(
            bottom,
            text="点击复制",
            bg="#12161f",
            fg="#4c5a71",
            font=("Microsoft YaHei UI", 8),
            anchor="e",
        )
        hint.pack(side="right", padx=(self._px(12), 0))

        item = {"win": win, "after": None, "ev": ev, "hint": hint, "copied": False, "w": 0, "h": 0}

        close.bind("<Button-1>", lambda e: self._destroy(item))
        for widget in (win, border, body, top, site, sender, bottom, code, hint):
            widget.bind("<Button-1>", lambda e, it=item: self._copy(it))
            widget.bind("<Enter>", lambda e, it=item: self._hover(it, True))
            widget.bind("<Leave>", lambda e, it=item: self._hover(it, False))

        # 按内容量尺寸：不留空白
        win.update_idletasks()
        width = max(self._px(MIN_WIDTH), min(win.winfo_reqwidth(), self._px(MAX_WIDTH)))
        height = win.winfo_reqheight()
        item["w"], item["h"] = width, height

        self.items.append(item)
        self._relayout()
        win.deiconify()
        win.lift()
        item["after"] = win.after(int(self.popup_seconds * 1000), lambda: self._destroy(item))

    # -- 内部

    def _copy(self, item: dict) -> None:
        if item["copied"]:
            return
        ok = self.clip.copy(item["ev"].code)
        item["copied"] = True
        if item["after"]:
            try:
                item["win"].after_cancel(item["after"])
            except Exception:
                pass
        item["hint"].configure(
            text="已复制 ✓" if ok else "复制失败",
            fg="#7ee787" if ok else "#ff7b72",
        )
        item["win"].after(2200, lambda: self._destroy(item))

    def _hover(self, item: dict, inside: bool) -> None:
        if item["copied"]:
            return
        try:
            item["hint"].configure(text="点击复制验证码" if inside else "点击复制")
        except tk.TclError:
            pass

    def _relayout(self) -> None:
        if not self.items:
            return
        x0, y0, area_w, area_h = work_area(self.root)
        right = x0 + area_w - self.margin
        cursor = y0 + area_h - self._px(12)
        for item in reversed(self.items):  # 最新的在最下面
            h = item["h"]
            y = max(y0 + self._px(8), cursor - h)
            try:
                item["win"].geometry(f"{item['w']}x{h}+{right - item['w']}+{y}")
            except tk.TclError:
                pass
            cursor = y - self.gap

    def _destroy(self, item: dict) -> None:
        if item not in self.items:
            return
        if item["after"]:
            try:
                item["win"].after_cancel(item["after"])
            except Exception:
                pass
        try:
            item["win"].destroy()
        except tk.TclError:
            pass
        self.items.remove(item)
        self._relayout()

    def destroy_all(self) -> None:
        for item in list(self.items):
            self._destroy(item)
