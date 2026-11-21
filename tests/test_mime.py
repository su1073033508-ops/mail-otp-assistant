# -*- coding: utf-8 -*-
"""转发/附件相关的解析测试：python tests/test_mime.py"""

from __future__ import annotations

import email
import sys
from email.message import EmailMessage, Message
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mailotp import otp  # noqa: E402
from mailotp.imap_scan import message_texts  # noqa: E402


def _inner_eml(code: str, html: bool = False) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = f"【测试站点】注册账户验证码 {code}"
    msg["From"] = "Test Site <noreply@test.example>"
    msg["To"] = "me@example.com"
    if html:
        msg.set_content(f"<div>您正在进行 注册账户 操作，验证码如下：<div>{code}</div></div>", subtype="html")
    else:
        msg.set_content(f"您好，\n您的验证码是 {code}，10 分钟内有效。\n")
    return msg.as_bytes()


def _outer_with_eml(code: str, html: bool = False, filename: str = "") -> bytes:
    outer = EmailMessage()
    outer["Subject"] = "历史验证码（测试）"
    outer["From"] = "me@example.com"
    outer["To"] = "me@example.com"
    outer.set_content("")  # 正文空白，和 QQ「作为附件转发」一样
    outer.add_attachment(
        _inner_eml(code, html),
        maintype="application",
        subtype="octet-stream",
        filename=filename or "【测试站点】注册账户验证码.eml",
    )
    return outer.as_bytes()


def check(name: str, cond: bool) -> bool:
    print(f"{'✓' if cond else '✗'} {name}")
    return cond


def main() -> int:
    ok = True

    # 1. .eml 附件（纯文本）
    outer = email.message_from_bytes(_outer_with_eml("528044"))
    plain, html = message_texts(outer)
    text = otp.clean_bodies(plain, html)
    res = otp.extract("历史验证码（测试）\n" + text)
    ok &= check("空白正文 + .eml 附件（文本）能认出 528044", bool(res) and res.code == "528044")

    # 2. .eml 附件（HTML）
    outer = email.message_from_bytes(_outer_with_eml("731905", html=True))
    plain, html = message_texts(outer)
    res = otp.extract("历史验证码（测试）\n" + otp.clean_bodies(plain, html))
    ok &= check("空白正文 + .eml 附件（HTML）能认出 731905", bool(res) and res.code == "731905")

    # 3. 只有附件名带验证码、正文空白
    inner_blank = EmailMessage()
    inner_blank["Subject"] = "验证邮件"
    inner_blank.set_content("（正文是图片，没有文字）")
    outer = EmailMessage()
    outer["Subject"] = "转发"
    outer.set_content("")
    outer.add_attachment(
        inner_blank.as_bytes(),
        maintype="application",
        subtype="octet-stream",
        filename="241302 是你的登录验证码.eml",
    )
    plain, html = message_texts(email.message_from_bytes(outer.as_bytes()))
    res = otp.extract("转发\n" + otp.clean_bodies(plain, html))
    ok &= check("附件名带验证码时也能认出 241302", bool(res) and res.code == "241302")

    # 4. message/rfc822 类型
    wrapper = Message()
    wrapper["Subject"] = "转发（rfc822）"
    wrapper.set_type("multipart/mixed")
    sub = Message()
    sub["Content-Type"] = "message/rfc822"
    sub.set_payload([email.message_from_bytes(_inner_eml("668812"))])
    wrapper.attach(sub)
    plain, html = message_texts(email.message_from_bytes(wrapper.as_bytes()))
    res = otp.extract("转发（rfc822）\n" + otp.clean_bodies(plain, html))
    ok &= check("message/rfc822 内嵌邮件能认出 668812", bool(res) and res.code == "668812")

    # 5. 普通正文不受影响
    m = EmailMessage()
    m["Subject"] = "【GitHub】您的验证码是 611204"
    m.set_content("您的验证码是 611204。")
    plain, html = message_texts(email.message_from_bytes(m.as_bytes()))
    res = otp.extract("【GitHub】您的验证码是 611204\n" + otp.clean_bodies(plain, html))
    ok &= check("普通正文仍能认出 611204", bool(res) and res.code == "611204")

    print()
    print("全部通过" if ok else "有失败")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
