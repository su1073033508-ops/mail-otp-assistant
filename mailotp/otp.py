"""从邮件里认出验证码，并猜出站点名。

思路：先找关键词（验证码 / verification code …），再看关键词附近的「像码的」token，
按距离、长度、上下文打分，排除年份、金额、订单号、电话、URL 里的数字。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape

# ---------------------------------------------------------------- 关键词

_CJK_KEYWORDS = [
    "验证码",
    "校验码",
    "动态码",
    "动态密码",
    "动态口令",
    "确认码",
    "安全码",
    "安全代码",
    "验证代码",
    "登录码",
    "登入码",
    "登录代码",
    "登入代码",
    "双重登录代码",
    "双重登入代码",
    "两步验证码",
    "两步验证",
    "二步验证",
    "二次验证码",
    "双重验证码",
    "双重认证",
    "认证码",
    "认证代码",
    "一次性密码",
    "一次性验证码",
]

_EN_KEYWORD_PATTERNS = [
    r"verification\s+code",
    r"verify\s+code",
    r"security\s+code",
    r"auth(?:entication)?\s+code",
    r"confirmation\s+code",
    r"one[-\s]?time\s+(?:code|password|passcode|pin)",
    r"passcode",
    r"\botp\b",
    r"your\s+code\s*(?:is|:)",
    r"code\s*(?:is|:)\s*",
    r"login\s+code",
    r"sign[-\s]?in\s+code",
    r"two[-\s]?factor\s+code",
    r"\b2fa\b",
    r"login\s+code",
]

KEYWORD_RE = re.compile(
    "|".join([re.escape(k) for k in _CJK_KEYWORDS] + _EN_KEYWORD_PATTERNS),
    re.IGNORECASE,
)

# ---------------------------------------------------------------- token

# 纯数字 4–8 位，或 5–8 位字母数字混合（如 A1B2C3 / 7G5K9Q）
TOKEN_RE = re.compile(r"(?<![0-9A-Za-z])([0-9]{4,8}|[0-9A-Za-z]{5,8})(?![0-9A-Za-z])")

# 出现在 token 前面就说明这数字不是验证码
EXCLUDE_BEFORE = re.compile(
    r"(订单|单号|编号|工单|运单|快递|物流|流水号|金额|价格|运费|合计|总额|余额|积分|"
    r"尾号|卡号|手机号|手机|电话|座机|客服|版本|版本号|"
    r"优惠券|券码|邀请码|推荐码|兑换码|活动码|红包|折扣|"
    r"order\s*(?:no|number|id)|invoice|tracking|amount|total|price|balance|coupon|promo|"
    r"version|copyright|©|tel|phone|fax|qq\s*号)",
    re.IGNORECASE,
)

# 这些词紧跟在 token 后面，说明是金额/年份/时间
EXCLUDE_AFTER = re.compile(
    r"\s*(?:元|块钱|美元|美金|usd|rmb|cny|%/|年|月|日|点|分|秒|岁|公里|km|℃)",
    re.IGNORECASE,
)

YEAR_RE = re.compile(r"^(19|20)\d{2}$")

_URL_TAIL = re.compile(r"(https?://|www\.)\S*$", re.IGNORECASE)

# 数字紧贴在汉字后面时，只有这些「连接词」才算验证码引导词
_CONNECTORS = {"", "是", "为", "是：", "是:", "为：", "为:", "：", ":", "＝", "=", "是 ="}


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"

# ---------------------------------------------------------------- 文本处理

# 繁体常用字 → 简体（只用 OTP 邮件里真正会碰到的那批字，避免误伤）
_TRAD_TO_SIMP = {
    "驗": "验", "證": "证", "碼": "码", "電": "电", "郵": "邮", "這": "这", "組": "组",
    "請": "请", "將": "将", "後": "后", "鐘": "钟", "時": "时", "間": "间", "開": "开",
    "啟": "启", "帳": "账", "號": "号", "認": "认", "確": "确", "動": "动", "態": "态",
    "檢": "检", "資": "资", "訊": "讯", "註": "注", "冊": "册", "錯": "错", "誤": "误",
    "過": "过", "於": "于", "與": "与", "傳": "传", "輸": "输", "簡": "简", "單": "单",
    "為": "为", "無": "无", "複": "复", "製": "制", "們": "们", "個": "个", "應": "应",
    "該": "该", "聯": "联", "絡": "络", "臺": "台", "灣": "湾", "體": "体", "點": "点",
    "選": "选", "擇": "择", "圖": "图", "檔": "档", "連": "连", "結": "结", "頁": "页",
    "網": "网", "費": "费", "額": "额", "幣": "币", "處": "处", "條": "条", "約": "约",
    "訂": "订", "購": "购", "買": "买", "賣": "卖", "銀": "银", "儲": "储", "轉": "转",
    "匯": "汇", "戶": "户", "權": "权", "員": "员", "務": "务", "業": "业", "遊": "游",
    "戲": "戏", "擊": "击", "還": "还", "遞": "递", "籤": "签", "憑": "凭", "紀": "纪",
    "錄": "录", "項": "项", "螢": "萤", "爾": "尔", "總": "总", "價": "价", "幣": "币",
}

# 全角数字/字母 → 半角
_FULLWIDTH = {0xFF10 + i: ord(str(i)) for i in range(10)}
_FULLWIDTH.update({0xFF21 + i: ord(chr(65 + i)) for i in range(26)})
_FULLWIDTH.update({0xFF41 + i: ord(chr(97 + i)) for i in range(26)})
_FULLWIDTH.update({0x3000: 0x20, 0xFF0D: ord("-"), 0xFF1A: ord(":")})


def normalize_chars(text: str) -> str:
    """繁体转简体 + 全角转半角，让「驗證碼」「１２３４５６」也能被同一套规则命中。"""
    if not text:
        return ""
    text = text.translate({ord(k): v for k, v in _TRAD_TO_SIMP.items()})
    return text.translate(_FULLWIDTH)


_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")  # HTML 注释里常有 CSS 片段
_TAG_RE = re.compile(r"(?is)<(script|style|head)\b.*?</\1\s*>")
_ANY_TAG_RE = re.compile(r"(?s)<[^>]{1,400}>")
_SPACE_RE = re.compile(r"[ \t\r\f\v\u00a0\u200b]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def html_to_text(html: str) -> str:
    text = _COMMENT_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = _ANY_TAG_RE.sub(" ", text)
    text = unescape(text)
    return text


def normalize(text: str) -> str:
    text = _SPACE_RE.sub(" ", text or "")
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def clean_bodies(plain: str, html: str) -> str:
    parts = []
    if plain and plain.strip():
        parts.append(normalize(plain))
    if html and html.strip():
        parts.append(normalize(html_to_text(html)))
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------- 结果

@dataclass
class OtpResult:
    code: str
    score: int
    keyword: str = ""
    site: str = ""


def _looks_like_year(tok: str) -> bool:
    return bool(YEAR_RE.match(tok))


def _is_url_inside(text: str, start: int) -> bool:
    """token 是否落在 URL 里（前面同段落有 http/https/www）。"""
    line_start = max(text.rfind("\n", 0, start), text.rfind(" ", 0, start))
    chunk = text[line_start + 1 : start]
    return bool(_URL_TAIL.search(chunk))


def _valid_token(tok: str) -> bool:
    if tok.isdigit():
        return 4 <= len(tok) <= 8 and not _looks_like_year(tok)
    # 字母数字混合：必须 5–8 位、至少 2 个数字、不能是普通单词
    if not (5 <= len(tok) <= 8):
        return False
    digits = sum(ch.isdigit() for ch in tok)
    if digits < 2:
        return False
    if tok.isalpha():
        return False
    return True


def _score(tok: str, distance: int, after: bool = True) -> int:
    if tok.isdigit():
        score = 55
        if len(tok) == 6:
            score += 40
        elif len(tok) in (4, 5):
            score += 10
        else:
            score += 20
    else:
        score = 40
    # 越靠近关键词越可信
    score += max(0, 35 - min(distance, 35))
    # 验证码通常在关键词「后面」
    if after:
        score += 8
    return score


def _candidates(text: str, kw_start: int, kw_end: int) -> list[tuple[int, str, int]]:
    """返回 (分数, token, 距离)。只在关键词 ±窗口 内找。"""
    lo = max(0, kw_start - 40)
    hi = min(len(text), kw_end + 70)
    window = text[lo:hi]
    out = []
    for m in TOKEN_RE.finditer(window):
        tok = m.group(1)
        if not _valid_token(tok):
            continue
        abs_start = lo + m.start(1)
        abs_end = lo + m.end(1)
        # 只看紧贴 token 的那一小段（在最近的标点/换行之后），避免把上一句的关键词算进来
        prefix = text[max(0, abs_start - 14) : abs_start]
        prefix = re.split(r"[，,。;；:：\n|、]", prefix)[-1]
        after = text[abs_end : abs_end + 6]
        if EXCLUDE_BEFORE.search(prefix):
            continue
        if EXCLUDE_AFTER.match(after):
            continue
        # 紧贴汉字的数字（如「重测0907」「订单123456」）通常是编号/标签，不是验证码；
        # 只有「验证码是123456」「验证码：123456」这种引导词之后才收。
        prev_ch = text[abs_start - 1] if abs_start > 0 else " "
        if _is_cjk(prev_ch):
            if abs_start < kw_end:
                continue
            between = text[kw_end:abs_start].strip()
            if between not in _CONNECTORS:
                continue
        if _is_url_inside(text, abs_start):
            continue
        distance = abs_start - kw_end if abs_start >= kw_end else kw_start - abs_end
        out.append((_score(tok, max(0, distance), abs_start >= kw_end), tok, max(0, distance)))
    return out


def extract(text: str) -> OtpResult | None:
    """从整封邮件文本里挑出最像验证码的 token。"""
    if not text:
        return None
    text = normalize_chars(text)
    best: tuple[int, str, str] | None = None  # (score, token, keyword)
    for kw in KEYWORD_RE.finditer(text):
        for score, tok, _dist in _candidates(text, kw.start(), kw.end()):
            if best is None or score > best[0]:
                best = (score, tok, kw.group(0))
    if best is None or best[0] <= 0:
        return None
    return OtpResult(code=best[1], score=best[0], keyword=best[2])


def extract_all(text: str, limit: int = 3) -> list[OtpResult]:
    """一封邮件里可能有多个验证码（把几封邮件打包转发），按得分返回前几个不同的码。

    只保留和最高分相差不大的码，避免把正文里偶遇的编号也当成验证码。
    """
    if not text:
        return []
    text = normalize_chars(text)
    best: dict[str, tuple[int, str]] = {}
    for kw in KEYWORD_RE.finditer(text):
        for score, tok, _dist in _candidates(text, kw.start(), kw.end()):
            prev = best.get(tok)
            if prev is None or score > prev[0]:
                best[tok] = (score, kw.group(0))
    if not best:
        return []
    ordered = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0]))
    top = ordered[0][1][0]
    out: list[OtpResult] = []
    for tok, (score, kw) in ordered:
        if score <= 0 or score < top - 25:
            continue
        out.append(OtpResult(code=tok, score=score, keyword=kw))
        if len(out) >= max(1, limit):
            break
    return out


# ---------------------------------------------------------------- 站点名

_BRACKET_RE = re.compile(r"^\s*[【\[（(]\s*([^】\]）)]{1,24}?)\s*[】\]）)]")
_NOISE_SUFFIX = re.compile(
    r"(验证码|校验码|动态码|安全码|通知|提醒|官方|团队|客服|支持|service|team|support|"
    r"noreply|no-reply)$",
    re.IGNORECASE,
)
_DOMAIN_SUFFIX = re.compile(r"\.(com|cn|net|org|io|co|me|cc|top|xyz|app|dev)$", re.IGNORECASE)


def site_from(subject: str, from_name: str, from_addr: str) -> str:
    subject = normalize_chars((subject or "").strip())
    m = _BRACKET_RE.match(subject)
    if m:
        name = m.group(1).strip()
        if name:
            return name[:40]

    name = (from_name or "").strip().strip('"“”\'')
    name = re.sub(r"\s+", " ", name)
    if name and "@" not in name and len(name) <= 40:
        return name

    addr = (from_addr or "").strip().lower()
    if "@" in addr:
        dom = addr.split("@", 1)[1]
        core = dom.split(".")[0] if "." in dom else dom
        if core and core not in ("mail", "email", "noreply", "no", "info", "service"):
            return core
        parts = [p for p in dom.split(".") if p]
        if len(parts) >= 2:
            return parts[-2]
    return "未知站点"


# ---------------------------------------------------------------- 转发邮件里的站点

FORWARD_HINT = re.compile(r"(原始邮件|原始郵件|转发的邮件|轉寄的郵件|转发邮件|Original Message|Forwarded message|发件人\s*[:：]|寄件者\s*[:：])", re.IGNORECASE)

_SENDER_LINE = re.compile(
    r"(?:发件人|寄件者|寄件人|来件人|from|sender)\s*[:：]\s*([^\n<>\"]{2,40})", re.IGNORECASE
)


def site_from_body(body: str) -> str:
    """转发邮件（正文里带「原始邮件」）时，从正文里的发件人行猜站点。"""
    if not body:
        return ""
    head = normalize_chars(body[:3000])
    m = _SENDER_LINE.search(head)
    if m:
        name = m.group(1).strip(" \t:：-")
        name = re.sub(r"\s+", " ", name)
        if name and "@" not in name:
            return name[:40]
    m = re.search(r"@([a-z0-9-]+(?:\.[a-z0-9-]+)+)", head, re.IGNORECASE)
    if m:
        parts = [x for x in m.group(1).split(".") if x]
        for skip in ("mail", "email", "acct", "auth", "noreply", "no", "reply", "send", "smtp"):
            parts = [x for x in parts if x.lower() != skip]
        if parts:
            return parts[0][:40]
    return ""
