# -*- coding: utf-8 -*-
"""识别规则的回归测试：python tests/test_otp.py"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mailotp import otp  # noqa: E402

CASES = [
    # (说明, 文本, 期望码 或 None)
    ("中文标准", "【GitHub】您的验证码是 482913，5 分钟内有效。", "482913"),
    ("中文冒号", "您的动态密码：7351，请勿泄露。", "7351"),
    ("英文 verify", "Your verification code is 902144. It expires in 10 minutes.", "902144"),
    ("英文大写字", "SECURITY CODE: A1B2C3", "A1B2C3"),
    ("HTML 里", "<p>验证码 <b>631204</b></p><img src='https://x.com/p.png?id=888888'>", "631204"),
    ("年份不算", "© 2026 Example Inc. 您的验证码：558012", "558012"),
    ("订单号不算", "订单号 20231117001，您的验证码是 771288", "771288"),
    ("金额不算", "您的验证码 440055，订单金额 138 元", "440055"),
    ("电话不算", "客服电话 4008123123，您的验证码为 512390", "512390"),
    ("无关键词不猜", "本期推荐 123456 号商品，价格 99 元。", None),
    ("多个码取最近的", "优惠券 111111，动态码 660214 请尽快使用", "660214"),
    ("4 位验证码", "您的验证码 8842，请查收。", "8842"),
    ("8 位", "登录校验码 51203998", "51203998"),
    ("登录码 英文", "Use 447190 as your one-time password.", "447190"),
    ("7 位", "请填写确认码 1029384 完成注册", "1029384"),
    ("裸数字干扰", "2026 年 11 月 21 日 15:30 收到，您的验证码是 339001", "339001"),
    ("标签数字粘汉字", "重测0907 您的验证码已失效", None),
    ("紧贴验证码后面", "验证码123456", "123456"),
    ("是字引导无空格", "您的验证码是123456，请查收", "123456"),
    ("编号粘汉字", "订单123456 的验证码是 781205", "781205"),
    ("繁体验证码", "請使用這組代碼驗證您的電子信箱。\n\n735190\n\n驗證碼將在 30 分鐘後失效。", "735190"),
    ("繁体重双登入代码", "你的雙重登入代碼：\n\n410827\n\n請使用以上代碼完成登入。", "410827"),
    ("英文 two-factor", "Your two-factor code: 902144", "902144"),
    ("英文 login code", "Here is your login code 611208.", "611208"),
    ("繁体标题", "您的驗證碼是 604128", "604128"),
    ("全角数字", "验证码：１２３４５６", "123456"),

]


def main() -> int:
    failed = 0
    for desc, text, expect in CASES:
        got = otp.extract(text)
        code = got.code if got else None
        ok = code == expect
        if not ok:
            failed += 1
        print(f"{'✓' if ok else '✗'} {desc:<16} 期望={expect!s:<8} 实际={code!s:<8} 站点={otp.site_from(text if text.startswith('【') else '', '', '')}")
    print()
    # 站点名
    site_cases = [
        ("【GitHub】您的验证码是 482913", "GitHub Team <noreply@github.com>", "noreply@github.com", "GitHub"),
        ("您的验证码", "Steam Support", "noreply@steampowered.com", "Steam Support"),
        ("Your code", "", "no-reply@discord.com", "discord"),
    ]
    for subject, name, addr, expect in site_cases:
        got = otp.site_from(subject, name, addr)
        ok = got == expect
        if not ok:
            failed += 1
        print(f"{'✓' if ok else '✗'} 站点 {expect:<12} 实际={got}")

    bundle = ("你的双重登入代码：\n\n091949\n\n"
              "你的双重登入代码：\n\n119920\n\n"
              "你的双重登入代码：\n\n787764\n")
    many = otp.extract_all(bundle, limit=3)
    ok = [m.code for m in many] == ["091949", "119920", "787764"]
    if not ok:
        failed += 1
    print(f"{'✓' if ok else '✗'} 一封邮件多个码 {[m.code for m in many]}")

    print()
    print("全部通过" if failed == 0 else f"{failed} 条失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
