"""系统托盘图标（pystray）：立即检查 / 暂停 / 打开日志 / 退出。"""

from __future__ import annotations

import threading

import pystray
from PIL import Image, ImageDraw


def make_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((5, 16, 59, 52), radius=9, fill=(30, 40, 58, 255), outline=(126, 231, 135, 255), width=3)
    d.line((9, 21, 32, 38, 55, 21), fill=(126, 231, 135, 255), width=3, joint="curve")
    d.ellipse((43, 3, 61, 21), fill=(255, 106, 91, 255), outline=(18, 22, 31, 255), width=2)
    return img


def _scan_label(app) -> str:
    if getattr(app, "manual_mode", False):
        return "开始监听（5 分钟）"
    return "立即检查"


class TrayIcon:
    def __init__(self, app):
        self.app = app
        menu = pystray.Menu(
            pystray.MenuItem(lambda item: _scan_label(app), lambda: app.request_scan(), default=True),
            pystray.MenuItem(
                "停止监听",
                lambda: app.disarm_manual("手动停止"),
                visible=lambda item: bool(getattr(app, "_armed", False)),
            ),
            pystray.MenuItem(
                lambda item: "继续检查" if getattr(app, "paused", False) else "暂停检查",
                lambda: app.toggle_pause(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("设置…", lambda: app.request_settings()),
            pystray.MenuItem("打开日志", lambda: app.open_log()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda: app.quit()),
        )
        self.icon = pystray.Icon("mail_otp", make_image(), "邮箱验证码助手", menu)

    def start(self) -> None:
        threading.Thread(target=self._run, name="tray", daemon=True).start()

    def _run(self) -> None:
        try:
            self.icon.run()
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass

    def set_status(self, text: str) -> None:
        try:
            self.icon.title = f"邮箱验证码助手 · {text}"
            self.icon.update_menu()
        except Exception:
            pass
