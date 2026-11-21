"""开机自启（快捷方式 → 启动文件夹）。

源码运行时用 run_hidden.vbs + pythonw 启动；打包成 exe 后直接指向 exe。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000

APP_FOLDER = "邮箱验证码助手"
SHORTCUT_NAME = "邮箱验证码助手.lnk"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """源码目录（打包后是 exe 所在目录）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def startup_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def is_installed() -> bool:
    return shortcut_path().exists()


def _run_ps(script: str) -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=CREATE_NO_WINDOW,
    )


def _write_vbs() -> Path:
    root = app_root()
    vbs = root / "run_hidden.vbs"
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pyw = Path(sys.executable)
    main_py = root / "main.py"
    content = (
        "' 由邮箱验证码助手生成：无窗口启动\n"
        "Option Explicit\n"
        "Dim sh, fso\n"
        'Set sh = CreateObject("WScript.Shell")\n'
        'Set fso = CreateObject("Scripting.FileSystemObject")\n'
        f'sh.CurrentDirectory = "{root}"\n'
        f'If Not fso.FileExists("{pyw}") Then WScript.Quit 1\n'
        f'sh.Run """{pyw}"" ""{main_py}""", 0, False\n'
    )
    vbs.write_text(content, encoding="utf-8")
    return vbs


def install() -> None:
    """创建开机自启快捷方式（打包 exe 直接指 exe，源码指 wscript+vbs）。"""
    startup = startup_dir()
    startup.mkdir(parents=True, exist_ok=True)
    link = startup / SHORTCUT_NAME
    wscript = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "wscript.exe"

    if is_frozen():
        target = Path(sys.executable).resolve()
        arguments = ""
        workdir = target.parent
    else:
        vbs = _write_vbs()
        target = wscript
        arguments = f'"{vbs}"'
        workdir = app_root()

    # 注意：Arguments 必须显式写成空串——否则已存在的 .lnk 会保留旧的 vbs 参数
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{link}'); "
        f"$s.TargetPath = '{target}'; "
        f"$s.Arguments = '{arguments}'; "
        f"$s.WorkingDirectory = '{workdir}'; "
        "$s.WindowStyle = 7; "
        "$s.Description = '邮箱验证码助手：邮箱验证码弹窗'; "
        "$s.Save()"
    )
    _run_ps(ps)
    if not link.exists():
        raise OSError("快捷方式没创建成功（检查 Startup 目录权限）")


def uninstall() -> bool:
    link = shortcut_path()
    if not link.exists():
        return False
    link.unlink()
    return True


def start_now() -> None:
    """后台启动（源码用 pythonw，打包直接跑 exe）。"""
    root = app_root()
    if is_frozen():
        exe = Path(sys.executable).resolve()
        subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=CREATE_NO_WINDOW)
        return
    _write_vbs()
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pyw = Path(sys.executable)
    subprocess.Popen(
        [str(pyw), str(root / "main.py")],
        cwd=str(root),
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )


def desktop_shortcut() -> Path:
    """桌面快捷方式（打包后才有意义）。"""
    desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
    return desktop / SHORTCUT_NAME


def create_desktop_shortcut() -> Path:
    target = Path(sys.executable).resolve()
    link = desktop_shortcut()
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{link}'); "
        f"$s.TargetPath = '{target}'; "
        f"$s.Arguments = ''; "
        f"$s.WorkingDirectory = '{target.parent}'; "
        "$s.Description = '邮箱验证码助手：邮箱验证码弹窗'; "
        "$s.Save()"
    )
    _run_ps(ps)
    return link
