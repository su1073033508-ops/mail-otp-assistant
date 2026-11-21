"""程序主体：Tk 主线程跑 UI，后台线程扫描邮箱，托盘常驻。"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
import tkinter as tk

from . import APP_NAME, __version__, conf, dpi, ipc, logsetup
from .clipboard_guard import ClipboardGuard
from .imap_scan import AccountSession, OtpEvent
from .popup import PopupManager
from .settings_ui import SettingsWindow
from .store import Store
from .tray_icon import TrayIcon

MUTEX_NAME = "Local\\mail_otp_assistant_singleton"


def acquire_single_instance() -> int | None:
    """返回互斥体句柄；已有实例在跑则返回 None。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    handle = k32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return None
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle


class App:
    def __init__(self, tray: bool = True):
        dpi.enable()  # 必须在 Tk 之前
        self.cfg = conf.load()
        self.log = logsetup.setup(conf.LOG_PATH)
        self.store = Store(conf.STATE_PATH)
        self.paused = False
        self.mode = "manual" if str(self.cfg.get("mode", "auto")).lower() == "manual" else "auto"
        self._armed = False
        self._armed_until = 0.0
        self.stop_evt = threading.Event()
        self.scan_evt = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.sessions: dict[str, AccountSession] = {}
        self._reload_needed = False
        self._last_purge = 0.0

        self.root = tk.Tk()
        dpi.apply(self.root)
        self.root.withdraw()
        self.root.title(APP_NAME)

        self.clip = ClipboardGuard(
            self.root,
            clear_after=int(self.cfg.get("clipboard_clear_seconds", 60)),
            log=self.log.info,
        )
        self.popups = PopupManager(
            self.root,
            self.clip,
            popup_seconds=int(self.cfg.get("popup_seconds", 40)),
            log=self.log.info,
        )

        self.tray_hidden = bool(self.cfg.get("hide_tray", False))
        if self.mode == "manual" and self.tray_hidden:
            self.log.info("手动模式要用托盘图标触发，已自动取消「隐藏系统托盘图标」")
            self.tray_hidden = False
            self.cfg["hide_tray"] = False
            try:
                conf.save(self.cfg)
            except Exception:
                pass
        self.tray: TrayIcon | None = None
        if tray and not self.tray_hidden:
            self.tray = TrayIcon(self)
            self.tray.start()
        self.settings: SettingsWindow | None = None

        self._ipc_handles = [ipc.create(ipc.SETTINGS_EVENT), ipc.create(ipc.ARM_EVENT)]
        threading.Thread(target=self._ipc_loop, name="ipc", daemon=True).start()

        self.log.info(f"{APP_NAME} v{__version__} 启动，账号 {len(self.cfg.get('accounts', []))} 个")
        if self.tray_hidden:
            self.log.info("托盘图标处于隐藏状态（再运行一次 main.py 可打开设置）")
        self._warn_missing_secrets()
        self._update_tray_status()
        if self.manual_mode:
            self.log.info("运行模式：手动（双击托盘图标开始 5 分钟监听）")

        threading.Thread(target=self._scheduler, name="scanner", daemon=True).start()
        self.root.after(200, self._pump)
        if not self.cfg.get("accounts"):
            # 第一次用：直接把设置窗弹出来
            self.root.after(600, self.open_settings)

    # -- 启动检查

    def _warn_missing_secrets(self) -> None:
        missing = []
        for acct in self.cfg.get("accounts", []):
            if not acct.get("enabled", True):
                continue
            if not conf.has_secret(acct):
                missing.append(conf.account_label(acct))
        if not missing:
            return
        self.log.warning("以下账号还没填授权码：" + "、".join(missing) + "（运行 set_password.py）")
        if self.tray:
            self.tray.set_status(f"{len(missing)} 个账号待填授权码")

    # -- 对外操作（托盘线程调用）

    def request_scan(self) -> None:
        """托盘双击 / 菜单「立即检查」：自动模式扫一轮，手动模式开 5 分钟监听窗。"""
        if self.manual_mode:
            self.arm_manual()
        else:
            self.scan_evt.set()

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.log.info("已暂停检查" if self.paused else "已恢复检查")
        if self.tray:
            self.tray.set_status("已暂停" if self.paused else "运行中")
        if not self.paused:
            self.scan_evt.set()

    # -- 运行模式（自动 / 手动）

    @property
    def manual_mode(self) -> bool:
        return self.mode == "manual"

    @property
    def manual_poll_seconds(self) -> int:
        return max(5, int(self.cfg.get("manual_poll_seconds", 10)))

    @property
    def auto_poll_seconds(self) -> int:
        return max(15, int(self.cfg.get("poll_seconds", 60)))

    def set_intervals(self, auto_seconds: int | None = None, manual_seconds: int | None = None,
                      manual_window: int | None = None) -> bool:
        """设置窗里改间隔／监听时长，立即保存并生效。"""
        changes = []
        if auto_seconds is not None:
            value = max(15, min(3600, int(auto_seconds)))
            self.cfg["poll_seconds"] = value
            changes.append(f"自动间隔 {value} 秒")
        if manual_seconds is not None:
            value = max(5, min(600, int(manual_seconds)))
            self.cfg["manual_poll_seconds"] = value
            changes.append(f"手动间隔 {value} 秒")
        if manual_window is not None:
            value = max(30, min(3600, int(manual_window)))
            self.cfg["manual_window_seconds"] = value
            changes.append(f"监听时长 {value} 秒")
        if not changes:
            return False
        try:
            conf.save(self.cfg)
        except Exception as exc:
            self.log.warning(f"保存间隔设置失败：{exc}")
            return False
        self.log.info("已更新：" + "，".join(changes))
        self.scan_evt.set()
        return True

    def arm_manual(self, seconds: int | None = None, reason: str = "") -> None:
        window = int(seconds or self.cfg.get("manual_window_seconds", 300))
        self._armed = True
        self._armed_until = time.time() + window
        span = f"{window // 60} 分钟" if window >= 60 else f"{window} 秒"
        tail = f" · {reason}" if reason else ""
        self.log.info(f"手动模式：开始监听 {span}（每 {self.manual_poll_seconds} 秒扫一次）{tail}")
        self._update_tray_status()
        self.scan_evt.set()

    def disarm_manual(self, reason: str = "") -> None:
        if not self._armed:
            return
        self._armed = False
        self._armed_until = 0.0
        self.log.info("手动模式：停止监听" + (f"（{reason}）" if reason else ""))
        self._update_tray_status()

    def set_mode(self, mode: str) -> bool:
        mode = "manual" if str(mode).lower() == "manual" else "auto"
        if mode == self.mode:
            return True
        self.mode = mode
        self._armed = False
        self._armed_until = 0.0
        self.cfg["mode"] = mode
        if mode == "manual" and self.tray_hidden:
            self.set_tray_hidden(False)
        try:
            conf.save(self.cfg)
        except Exception as exc:
            self.log.warning(f"保存运行模式失败：{exc}")
            return False
        if self.manual_mode:
            self.log.info("已切换到手动模式：不自动检查，双击托盘图标开始 5 分钟监听")
        else:
            self.log.info("已切换到自动模式：后台按间隔自动检查")
            self.scan_evt.set()
        self._update_tray_status()
        return True

    def _update_tray_status(self) -> None:
        if not self.tray:
            return
        if self.paused:
            self.tray.set_status("已暂停")
        elif not self.manual_mode:
            self.tray.set_status("运行中")
        elif self._armed:
            left = max(0, int(self._armed_until - time.time()))
            self.tray.set_status(f"监听中 {left // 60}:{left % 60:02d}（双击可重新计时）")
        else:
            self.tray.set_status("手动模式（双击托盘开始监听）")

    def open_log(self) -> None:
        try:
            os.startfile(conf.LOG_PATH)  # noqa: S606  (Windows)
        except Exception as exc:
            self.log.warning(f"打开日志失败：{exc}")

    def request_settings(self) -> None:
        """托盘线程调用：真正开窗交给 Tk 主线程。"""
        self.events.put(("settings", None))

    def open_settings(self) -> None:
        if self.settings is None:
            self.settings = SettingsWindow(self)
        self.settings.open()

    def _ipc_loop(self) -> None:
        """外部信号：打开设置窗 / 开始手动监听（main.py --arm）。"""
        try:
            while not self.stop_evt.is_set():
                idx = ipc.wait_any(self._ipc_handles, 500)
                if idx == 0:
                    self.request_settings()
                elif idx == 1:
                    self.arm_manual(reason="main.py --arm")
        except Exception as exc:
            self.log.warning(f"IPC 线程异常退出：{exc}")

    # -- 选项：托盘图标 / 开机自启

    def set_tray_hidden(self, hidden: bool) -> bool:
        hidden = bool(hidden)
        if hidden == self.tray_hidden:
            return True
        if hidden and self.manual_mode:
            self.log.warning("手动模式必须显示托盘图标（双击它才开始监听），不能隐藏")
            return False
        if hidden:
            if self.tray:
                self.tray.stop()
                self.tray = None
            self.log.info("托盘图标已隐藏；再运行一次 main.py（或双击启动快捷方式）会弹出设置窗")
        else:
            if self.tray is None:
                self.tray = TrayIcon(self)
                self.tray.start()
            self.log.info("托盘图标已显示")
        self.tray_hidden = hidden
        self.cfg["hide_tray"] = hidden
        try:
            conf.save(self.cfg)
        except Exception as exc:
            self.log.warning(f"保存 hide_tray 失败：{exc}")
            return False
        return True

    def set_autostart(self, enabled: bool) -> bool:
        from . import autostart

        try:
            if enabled:
                autostart.install()
            else:
                autostart.uninstall()
        except Exception as exc:
            self.log.warning(f"{'开启' if enabled else '关闭'}开机自启失败：{exc}")
            return False
        self.log.info("开机自启已" + ("开启" if enabled else "关闭"))
        return True

    def autostart_enabled(self) -> bool:
        from . import autostart

        try:
            return autostart.is_installed()
        except Exception:
            return False

    def on_settings_saved(self) -> None:
        """设置改完后：重读配置，让扫描线程自己重建连接（避免跨线程关连接）。"""
        self.cfg = conf.load()
        self._reload_needed = True
        accounts = [a for a in self.cfg.get("accounts", []) if a.get("enabled", True)]
        self.log.info(f"配置已更新，启用账号 {len(accounts)} 个")
        if self.tray:
            self.tray.set_status("运行中")
        self.scan_evt.set()

    def quit(self) -> None:
        """可从任意线程调用：只置标志，真正销毁交给 Tk 主线程。"""
        if self.stop_evt.is_set():
            return
        self.stop_evt.set()
        self.scan_evt.set()
        self.log.info(f"{APP_NAME} 退出")
        try:
            self.store.save()
        except Exception:
            pass
        for handle in getattr(self, "_ipc_handles", []):
            ipc.close(handle)
        if self.tray:
            self.tray.stop()

    # -- 扫描

    def _scheduler(self) -> None:
        while not self.stop_evt.is_set():
            if getattr(self, "_reload_needed", False):
                self._reload_needed = False
                self._drop_sessions()
            if self.paused:
                self.scan_evt.wait(1.0)
                self.scan_evt.clear()
                continue
            if self.manual_mode:
                now = time.time()
                if not self._armed or now >= self._armed_until:
                    if self._armed:
                        self._armed = False
                        self._armed_until = 0.0
                        self.log.info("手动模式：监听时间到，没收到验证码，停止检查")
                        self._update_tray_status()
                    self.scan_evt.wait(1.0)
                    self.scan_evt.clear()
                    continue
                try:
                    self._scan_all()
                except Exception as exc:
                    self.log.warning(f"本轮扫描异常：{exc}")
                self._update_tray_status()
                remaining = self._armed_until - time.time()
                self.scan_evt.wait(max(0.5, min(self.manual_poll_seconds, remaining)))
                self.scan_evt.clear()
                continue
            try:
                self._scan_all()
            except Exception as exc:
                self.log.warning(f"本轮扫描异常：{exc}")
            self.scan_evt.wait(self.auto_poll_seconds)
            self.scan_evt.clear()

    def _drop_sessions(self) -> None:
        for session in list(self.sessions.values()):
            try:
                session.close()
            except Exception:
                pass
        self.sessions.clear()

    def _scan_all(self) -> None:
        accounts = [a for a in self.cfg.get("accounts", []) if a.get("enabled", True)]
        if not accounts:
            return
        for acct in accounts:
            if self.stop_evt.is_set():
                return
            label = conf.account_label(acct)
            try:
                secret = conf.get_secret(acct)
            except conf.SecretMissing as exc:
                self.log.warning(str(exc))
                continue
            session = self.sessions.get(label)
            if session is None or session.conn is None:
                session = AccountSession(acct, self.cfg, self.log)
                self.sessions[label] = session
            try:
                events = session.poll(self.store)
            except conf.SecretMissing as exc:
                self.log.warning(str(exc))
                session.close()
                continue
            except Exception as exc:
                self.log.warning(f"[{label}] 扫描失败：{exc}")
                session.close()
                continue
            if self.paused:
                return
            for ev in events:
                self.events.put(("otp", ev))

        now = time.time()
        if now - self._last_purge > 3600:
            self._last_purge = now
            removed = self.store.purge(seen_hours=float(self.cfg.get("seen_ttl_hours", 48)))
            if removed:
                self.log.info(f"清理过期去重记录 {removed} 条")
            self.store.save()

    # -- UI 线程

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "otp":
                    self._on_otp(payload)
                elif kind == "settings":
                    self.open_settings()
        except queue.Empty:
            pass
        if self.stop_evt.is_set():
            self.popups.destroy_all()
            try:
                self.root.destroy()
            except Exception:
                pass
            return
        self.root.after(200, self._pump)

    def _on_otp(self, ev: OtpEvent) -> None:
        quiet = float(self.cfg.get("same_code_quiet_seconds", 300))
        if self.store.code_recent(ev.code, quiet):
            self.log.info(f"[{ev.account}] {ev.folder} 验证码重复（{quiet:.0f} 秒内已弹过），跳过")
            return
        self.store.mark_code(ev.code)
        # 日志只记「谁、哪个文件夹、哪个站点」，不记验证码
        self.log.info(f"[{ev.account}] {ev.folder} 收到验证码（站点：{ev.site}）")
        self.popups.show(ev)
        if self.manual_mode:
            self.disarm_manual("已收到验证码")

    def run(self) -> int:
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        try:
            self.root.mainloop()
        finally:
            self.stop_evt.set()
        return 0

    # -- 调试用：假弹窗

    def demo(self, seconds: float = 8.0) -> None:
        fake = [
            OtpEvent("163 主邮箱", "广告邮件", "482913", "GitHub", "GitHub <noreply@github.com>", "Your verification code", "demo-1", 1, time.time()),
            OtpEvent("QQ 邮箱", "垃圾箱", "A1B2C3", "Steam", "Steam Support <noreply@steampowered.com>", "Steam 登录验证码", "demo-2", 2, time.time()),
        ]
        for i, ev in enumerate(fake):
            self.root.after(400 + i * 500, lambda e=ev: self.popups.show(e))
        self.root.after(int(seconds * 1000), self.quit)
        self.root.mainloop()
