#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""现场填写邮箱授权码：输入不回显 → 试登录验证 → DPAPI 加密写入 config.json。

用法：
  python set_password.py                 交互式（列出账号 / 新增账号）
  python set_password.py --add           新增一个账号
  python set_password.py --account 163   给已有账号重填授权码
  python set_password.py --list          只看状态
  python set_password.py --test          验证已保存的授权码还能不能登录
  python set_password.py --remove 163    删除账号

明文授权码只存在于内存里，加密后立刻丢弃；不写日志、不回显、不进聊天记录。
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mailotp import conf  # noqa: E402
from mailotp.imap_scan import test_login  # noqa: E402

LINE = "-" * 62


def say(text: str = "") -> None:
    print(text, flush=True)


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}：").strip()
    except (EOFError, KeyboardInterrupt):
        say("\n已取消。")
        raise SystemExit(1)
    return value or default


def ask_code(acct: dict, cfg: dict) -> str | None:
    """要授权码并试登录；成功返授权码，放弃返 None。"""
    label = conf.account_label(acct)
    say("")
    say(f"账号：{label}  <{acct['user']}>")
    say(f"服务器：{acct['host']}:{acct['port']}（SSL）")
    say("请粘贴/输入 IMAP 授权码。输入时不显示任何字符，这是正常的；回车确认。")
    for attempt in range(1, 4):
        try:
            code = getpass.getpass("授权码：").strip()
        except (EOFError, KeyboardInterrupt):
            say("\n已取消。")
            return None
        code = code.replace(" ", "").replace("\t", "")
        if not code:
            say("没输入内容，再试一次。")
            continue
        say(f"已读取 {len(code)} 位，正在试登录…")
        ok, msg = test_login(acct, code)
        if ok:
            say("✓ 登录成功，授权码有效。")
            return code
        say(f"✗ 登录失败（第 {attempt}/3 次）：{msg}")
    say("连续失败 3 次，没有保存。检查授权码是否复制完整、IMAP 服务是否已开启。")
    return None


def show_status(cfg: dict) -> None:
    accounts = cfg.get("accounts", [])
    if not accounts:
        say("（还没有账号）")
        return
    for i, acct in enumerate(accounts, 1):
        state = "已设置" if conf.has_secret(acct) else "未设置"
        flag = "" if acct.get("enabled", True) else "  [已停用]"
        say(f"  {i}) {conf.account_label(acct):<18} {acct['user']:<32} 授权码：{state}{flag}")


def pick(cfg: dict, key: str = "") -> dict | None:
    if key:
        return conf.find_account(cfg, key)
    accounts = cfg.get("accounts", [])
    if not accounts:
        return None
    say("")
    say("当前账号：")
    show_status(cfg)
    say("  0) 添加新账号")
    while True:
        choice = ask("选择编号", "1")
        if choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(accounts):
            return accounts[int(choice) - 1]
        say("编号不对，重来。")


def add_account(cfg: dict, provider: str = "", user: str = "", label: str = "") -> dict:
    say("")
    say("支持：" + "、".join(f"{k}={v['help']}" for k, v in conf.PROVIDERS.items()))
    if not provider:
        provider = ask("邮箱类型（qq / 163 / 126 / yeah）", "qq").lower()
    if provider not in conf.PROVIDERS:
        say(f"不支持的类型：{provider}")
        raise SystemExit(1)
    if not user:
        user = ask("邮箱地址（例如 123456@qq.com）")
    if "@" not in user:
        suffix = {"qq": "@qq.com", "163": "@163.com", "126": "@126.com", "yeah": "@yeah.net"}[provider]
        user = user + suffix
        say(f"补全为：{user}")
    if not label:
        label = ask("显示名（弹窗和托盘里显示）", f"{conf.PROVIDERS[provider]['help']} {user.split('@')[0]}")
    acct = conf.new_account(provider, user, label)
    acct["id"] = conf.unique_account_id(cfg, acct["id"])
    return acct


def save_account(cfg: dict, acct: dict, code: str) -> None:
    conf.set_secret(acct, code)
    if acct not in cfg["accounts"]:
        cfg["accounts"].append(acct)
    conf.save(cfg)
    say("")
    say(f"✓ 已保存到 {conf.CONFIG_PATH.name}（DPAPI 加密，只对当前 Windows 用户可解）。")
    say("  授权码明文没有写入磁盘，也没有写日志。")


def do_add(cfg: dict, args) -> int:
    acct = add_account(cfg, args.provider or "", args.user or "", args.label or "")
    code = ask_code(acct, cfg)
    if code is None:
        return 1
    save_account(cfg, acct, code)
    return 0


def do_update(cfg: dict, key: str = "") -> int:
    acct = pick(cfg, key)
    if acct is None:
        say("没有这个账号，改走新增流程。")
        return do_add(cfg, argparse.Namespace(provider="", user="", label=""))
    code = ask_code(acct, cfg)
    if code is None:
        return 1
    save_account(cfg, acct, code)
    return 0


def do_test(cfg: dict) -> int:
    bad = 0
    for acct in cfg.get("accounts", []):
        label = conf.account_label(acct)
        if not conf.has_secret(acct):
            say(f"  ✗ {label}：授权码未设置")
            bad += 1
            continue
        ok, msg = test_login(acct, conf.get_secret(acct))
        say(f"  {'✓' if ok else '✗'} {label}：{msg}")
        bad += 0 if ok else 1
    return 0 if bad == 0 else 1


def do_remove(cfg: dict, key: str) -> int:
    acct = conf.find_account(cfg, key)
    if acct is None:
        say("找不到这个账号。")
        return 1
    say(f"要删除账号「{conf.account_label(acct)}」<{acct['user']}>？")
    if ask("确认删除？(y/N)", "N").lower() != "y":
        say("已取消。")
        return 0
    cfg["accounts"] = [a for a in cfg["accounts"] if a is not acct]
    conf.save(cfg)
    say("已删除。")
    return 0


def interactive(cfg: dict) -> int:
    say("=" * 62)
    say("  邮箱验证码助手 — 填写邮箱授权码")
    say("=" * 62)
    say("提示：授权码不是邮箱登录密码，是在邮箱网页版「设置」里开启 IMAP 后生成的一串字符。")
    say("")
    accounts = cfg.get("accounts", [])
    if not accounts:
        say("还没有账号，先添加一个。")
        return do_add(cfg, argparse.Namespace(provider="", user="", label=""))
    acct = pick(cfg, "")
    if acct is None:
        return do_add(cfg, argparse.Namespace(provider="", user="", label=""))
    code = ask_code(acct, cfg)
    if code is None:
        return 1
    save_account(cfg, acct, code)
    say("")
    if ask("继续填写下一个账号？(y/N)", "N").lower() == "y":
        return interactive(cfg)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="填写 / 修改邮箱 IMAP 授权码（DPAPI 加密保存）")
    parser.add_argument("--add", action="store_true", help="新增账号")
    parser.add_argument("--account", metavar="KEY", help="给已有账号重填授权码（id/label/邮箱任一部分）")
    parser.add_argument("--list", action="store_true", help="列出账号和授权码状态")
    parser.add_argument("--test", action="store_true", help="验证已保存的授权码")
    parser.add_argument("--remove", metavar="KEY", help="删除账号")
    parser.add_argument("--provider", help="配合 --add：qq / 163 / 126 / yeah")
    parser.add_argument("--user", help="配合 --add：邮箱地址")
    parser.add_argument("--label", help="配合 --add：显示名")
    args = parser.parse_args(argv)

    cfg = conf.load()
    if args.list:
        show_status(cfg)
        return 0
    if args.test:
        return do_test(cfg)
    if args.remove:
        return do_remove(cfg, args.remove)
    if args.add:
        return do_add(cfg, args)
    if args.account:
        return do_update(cfg, args.account)
    return interactive(cfg)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\n已取消。")
        sys.exit(1)
