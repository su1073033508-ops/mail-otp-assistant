"""让进程感知真实 DPI，并给 Tk 设好缩放（125% 屏上字号和坐标才不糊/不小）。"""

from __future__ import annotations

import ctypes

_applied = False


def enable() -> None:
    """必须在创建 Tk 之前调用。"""
    global _applied
    if _applied:
        return
    _applied = True
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def system_dpi() -> int:
    """系统 DPI：96 = 100%，120 = 125%，144 = 150%。"""
    try:
        dpi = int(ctypes.windll.user32.GetDpiForSystem())
        if dpi > 0:
            return dpi
    except Exception:
        pass
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        hdc = user32.GetDC(0)
        try:
            dpi = int(gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
        finally:
            user32.ReleaseDC(0, hdc)
        if dpi > 0:
            return dpi
    except Exception:
        pass
    return 96


def apply(root) -> float:
    """给 Tk 设置 DPI 缩放，返回缩放倍数（相对 96）。"""
    dpi_value = system_dpi()
    try:
        root.tk.call("tk", "scaling", dpi_value / 72.0)
    except Exception:
        pass
    return dpi_value / 96.0


def scale_for(root=None) -> float:
    return system_dpi() / 96.0
