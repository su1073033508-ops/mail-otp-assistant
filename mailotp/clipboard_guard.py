"""剪贴板：复制验证码后 N 秒自动清空（仅当剪贴板里仍是这个码）。

注意：Tk 的 clipboard_clear() 在 Windows 上并不会真的清空系统剪贴板，
必须再调用 Win32 的 OpenClipboard + EmptyClipboard。
"""

from __future__ import annotations

import ctypes
import tkinter as tk
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL


def empty_clipboard() -> bool:
    """真正清空系统剪贴板。"""
    if not _user32.OpenClipboard(None):
        return False
    try:
        return bool(_user32.EmptyClipboard())
    finally:
        _user32.CloseClipboard()


class ClipboardGuard:
    def __init__(self, root: tk.Tk, clear_after: int = 60, log=None):
        self.root = root
        self.clear_after = max(5, int(clear_after))
        self.log = log or (lambda *a, **k: None)
        self._token = object()

    def copy(self, text: str) -> bool:
        """写剪贴板并安排自动清空。返回是否成功。"""
        text = (text or "").strip()
        if not text:
            return False
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
        except tk.TclError as exc:
            self.log(f"写剪贴板失败：{exc}")
            return False
        token = object()
        self._token = token
        self.root.after(int(self.clear_after * 1000), lambda: self._clear_if_same(text, token))
        return True

    def _clear_if_same(self, text: str, token: object) -> None:
        if token is not self._token:
            return  # 期间又复制了别的东西，交给新的任务处理
        try:
            current = self.root.clipboard_get()
        except tk.TclError:
            current = ""
        if str(current).strip() != text:
            return  # 用户已经换成别的内容，别动
        try:
            self.root.clipboard_clear()  # 让 Tk 忘掉自己那份
        except tk.TclError:
            pass
        if empty_clipboard():
            self.log(f"剪贴板已自动清空（{self.clear_after} 秒）")
        else:
            self.log("剪贴板自动清空失败（被别的程序占用）")
