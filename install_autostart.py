#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""命令行安装/取消开机自启（逻辑在 mailotp/autostart.py）。

  python install_autostart.py            安装
  python install_autostart.py --remove   取消
  python install_autostart.py --status   查看
  python install_autostart.py --start    现在后台启动
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mailotp import autostart  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="邮箱验证码助手开机自启")
    parser.add_argument("--remove", action="store_true", help="取消开机自启")
    parser.add_argument("--status", action="store_true", help="看是否已装自启")
    parser.add_argument("--start", action="store_true", help="立即后台启动一次")
    args = parser.parse_args(argv)

    if args.status:
        print(f"快捷方式：{autostart.shortcut_path()} → {'已安装' if autostart.is_installed() else '未安装'}")
        return 0
    if args.remove:
        print("已删除开机自启。" if autostart.uninstall() else "本来就没有装开机自启。")
        return 0
    autostart.install()
    print(f"✓ 已创建开机自启：{autostart.shortcut_path()}")
    if args.start:
        autostart.start_now()
        print("✓ 已在后台启动。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
