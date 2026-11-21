"""跨进程信号（命名事件）：

1. 打开设置窗：托盘图标被隐藏时，再运行一次 main.py 也能喊出设置窗；
2. 开始手动监听：`python main.py --arm`，不用鼠标点托盘。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

SETTINGS_EVENT = "Local\\mail_otp_show_settings"
ARM_EVENT = "Local\\mail_otp_arm"

EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateEventW.restype = ctypes.c_void_p
_k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_k32.OpenEventW.restype = ctypes.c_void_p
_k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k32.SetEvent.argtypes = [ctypes.c_void_p]
_k32.SetEvent.restype = wintypes.BOOL
_k32.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), wintypes.BOOL, wintypes.DWORD]
_k32.WaitForMultipleObjects.restype = wintypes.DWORD
_k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_k32.WaitForSingleObject.restype = wintypes.DWORD
_k32.CloseHandle.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.restype = wintypes.BOOL


def create(name: str) -> int | None:
    """创建（或打开）自动重置事件，供本进程等待。"""
    handle = _k32.CreateEventW(None, False, False, name)
    return handle or None


def signal(name: str) -> bool:
    """通知已在运行的实例。"""
    handle = _k32.OpenEventW(EVENT_MODIFY_STATE, False, name)
    if not handle:
        return False
    try:
        return bool(_k32.SetEvent(handle))
    finally:
        _k32.CloseHandle(handle)


def signal_settings() -> bool:
    return signal(SETTINGS_EVENT)


def signal_arm() -> bool:
    return signal(ARM_EVENT)


def wait_any(handles: list[int | None], timeout_ms: int = 500) -> int | None:
    """等任意一个事件触发，返回下标；超时返回 None。"""
    valid = [h for h in handles if h]
    if not valid:
        return None
    n = len(valid)
    arr = (ctypes.c_void_p * n)(*valid)
    rc = _k32.WaitForMultipleObjects(n, arr, False, int(timeout_ms))
    if WAIT_OBJECT_0 <= rc < WAIT_OBJECT_0 + n:
        return int(rc)
    return None


def wait_signal(handle: int | None, timeout_ms: int = 500) -> bool:
    if not handle:
        return False
    try:
        return _k32.WaitForSingleObject(handle, timeout_ms) == WAIT_OBJECT_0
    except Exception:
        return False


def close(handle: int | None) -> None:
    if handle:
        try:
            _k32.CloseHandle(handle)
        except Exception:
            pass
