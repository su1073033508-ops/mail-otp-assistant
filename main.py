#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证码邮箱助手 — 入口。

  python main.py            后台常驻（带托盘，开机自启用这个）
  python main.py --check    体检：连接各账号、列出所有文件夹（不弹窗）
  python main.py --once     只扫一轮，把识别结果打到控制台（调试用）
  python main.py --demo     弹两个假验证码，验证界面样式
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# 打包成 --noconsole 的 exe 后 sys.stdout/stderr 是 None，print 会直接炸
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mailotp import APP_NAME, __version__, conf, logsetup  # noqa: E402


def _accounts(cfg: dict) -> list[dict]:
    return [a for a in cfg.get("accounts", []) if a.get("enabled", True)]


def cmd_check(cfg: dict, log) -> int:
    from mailotp.imap_scan import AccountSession

    accounts = _accounts(cfg)
    print(f"{APP_NAME} v{__version__} 体检")
    print(f"配置：{conf.CONFIG_PATH}")
    print(f"账号：{len(accounts)} 个\n")
    if not accounts:
        print("还没有账号。运行  填写授权码.bat  或  python set_password.py --add")
        return 1

    failed = 0
    for acct in accounts:
        label = conf.account_label(acct)
        print(f"=== {label}  <{acct.get('user')}>  [{acct.get('host')}:{acct.get('port')}]")
        if not conf.has_secret(acct):
            print("  ✗ 授权码未设置 → 运行  填写授权码.bat")
            failed += 1
            continue
        session = AccountSession(acct, cfg, log)
        try:
            session.ensure()
            folders = session.list_folders(force=True)
        except Exception as exc:
            print(f"  ✗ 连接失败：{exc}")
            failed += 1
            session.close()
            continue
        print(f"  ✓ 连接正常，可扫描文件夹 {len(folders)} 个：")
        conn = session.conn
        for raw, pretty in folders:
            quoted = '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'
            line = f"    {pretty:<14}"
            try:
                typ, data = conn.status(quoted, "(MESSAGES)")
                if typ == "OK" and data:
                    text = " ".join(x.decode("utf-8", "replace") for x in data if isinstance(x, bytes))
                    line += text
                else:
                    line += "（STATUS 失败）"
            except Exception as exc:
                line += f"（STATUS 异常：{exc}）"
            print(line)
        if getattr(session, "last_skipped", None):
            print("    （按 skip_folders 跳过：" + "、".join(session.last_skipped) + "）")
        session.close()
        print()
    if failed:
        print(f"有 {failed} 个账号需要处理。")
        return 1
    print("全部账号正常。")
    return 0


def cmd_once(cfg: dict, log) -> int:
    from mailotp.imap_scan import AccountSession
    from mailotp.store import Store

    store = Store(conf.STATE_PATH)
    total = 0
    for acct in _accounts(cfg):
        label = conf.account_label(acct)
        if not conf.has_secret(acct):
            print(f"[{label}] 跳过：授权码未设置")
            continue
        session = AccountSession(acct, cfg, log)
        try:
            events = session.poll(store)
        except Exception as exc:
            print(f"[{label}] 扫描失败：{exc}")
            session.close()
            continue
        for ev in events:
            total += 1
            print(f"[{label}] {ev.folder} | {ev.site} | 验证码 {ev.code} | {ev.subject[:40]}")
        session.close()
    store.save()
    print(f"本轮识别到 {total} 个验证码（首次运行会先建立基线、不报旧邮件）")
    return 0


def cmd_gui(tray: bool) -> int:
    from mailotp import ipc
    from mailotp.app import App, acquire_single_instance

    handle = acquire_single_instance()
    if handle is None:
        if ipc.signal_settings():
            print("已经在运行了：已让后台那个实例打开「设置」窗口。")
        else:
            print("已经在运行了（托盘里看得到图标）。")
        return 0
    app = App(tray=tray)
    return app.run()


def cmd_demo(seconds: float) -> int:
    from mailotp.app import App

    App(tray=False).demo(seconds=seconds)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} v{__version__}")
    parser.add_argument("--check", action="store_true", help="体检：连接并列出所有文件夹")
    parser.add_argument("--once", action="store_true", help="只扫一轮并打印结果")
    parser.add_argument("--demo", action="store_true", help="弹假验证码看样式")
    parser.add_argument("--demo-seconds", type=float, default=8.0)
    parser.add_argument("--no-tray", action="store_true", help="不带托盘启动（调试）")
    parser.add_argument("--arm", action="store_true", help="让已在运行的实例开始手动监听（不用点托盘）")
    parser.add_argument("--autostart", choices=["on", "off", "status"], help="开机自启：on / off / status")
    parser.add_argument("--desktop-shortcut", action="store_true", help="在桌面创建快捷方式")
    args = parser.parse_args(argv)

    cfg = conf.load()
    log = logsetup.setup(conf.LOG_PATH)

    if args.check:
        return cmd_check(cfg, log)
    if args.once:
        return cmd_once(cfg, log)
    if args.demo:
        return cmd_demo(args.demo_seconds)
    if args.autostart:
        from mailotp import autostart

        if args.autostart == "on":
            autostart.install()
            print(f"✓ 已设置开机自启：{autostart.shortcut_path()}")
        elif args.autostart == "off":
            print("已取消开机自启。" if autostart.uninstall() else "本来就没装开机自启。")
        else:
            print(f"开机自启：{'已安装' if autostart.is_installed() else '未安装'} → {autostart.shortcut_path()}")
        return 0
    if args.desktop_shortcut:
        from mailotp import autostart

        print(f"✓ 桌面快捷方式：{autostart.create_desktop_shortcut()}")
        return 0
    if args.arm:
        from mailotp import ipc

        if ipc.signal_arm():
            print("已通知后台实例开始监听（手动模式：每 10 秒扫一次，最多 5 分钟）。")
            return 0
        print("没有在运行的实例；先启动 main.py。")
        return 1
    return cmd_gui(tray=not args.no_tray)


if __name__ == "__main__":
    sys.exit(main())
