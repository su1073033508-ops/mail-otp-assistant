"""IMAP 只读扫描：连接、文件夹发现、STATUS 比对、抓新邮件、识别验证码。

只读保证：只用 EXAMINE（imap.select(readonly=True)）和 BODY.PEEK，
不动 \\Seen 标记、不删邮件。
"""

from __future__ import annotations

import base64
import email
import imaplib
import re
import ssl
import time
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.utils import parseaddr

from . import conf, otp

imaplib.Commands.setdefault("ID", ("NONAUTH", "AUTH", "SELECTED"))

_STATUS_RE = re.compile(r"(MESSAGES|UIDNEXT|UIDVALIDITY|UNSEEN)\s+(\d+)", re.IGNORECASE)


@dataclass
class OtpEvent:
    account: str
    folder: str
    code: str
    site: str
    sender: str
    subject: str
    msgid: str
    uid: int
    received_at: float


# ---------------------------------------------------------------- 编码工具

def utf7_decode(name: str) -> str:
    """IMAP modified UTF-7 -> 可读文本（`&XfJT0ZAB-` -> `已发送`）。"""
    out: list[str] = []
    i = 0
    while i < len(name):
        if name[i] != "&":
            out.append(name[i])
            i += 1
            continue
        j = name.find("-", i)
        if j == -1:
            out.append(name[i:])
            break
        chunk = name[i + 1 : j]
        if not chunk:
            out.append("&")
        else:
            try:
                b = chunk.replace(",", "/").encode("ascii")
                raw = base64.b64decode(b + b"=" * (-len(b) % 4))
                out.append(raw.decode("utf-16-be"))
            except Exception:
                out.append(name[i : j + 1])
        i = j + 1
    return "".join(out)


def decode_mime(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def quote_mailbox(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_list_line(line: bytes) -> tuple[str, str]:
    """解析 `(\\HasNoChildren) "/" "&XfJT0ZAB-"`。"""
    text_line = line
    flags = ""
    m = re.match(rb"\(([^)]*)\)", text_line)
    if m:
        flags = m.group(1).decode("ascii", "replace")
        text_line = text_line[m.end():].strip()
    # 去掉分隔符（"..." 或 NIL）
    m = re.match(rb'"(?:[^"\\]|\\.)*"\s+', text_line)
    if m:
        text_line = text_line[m.end():]
    else:
        parts = text_line.split(b" ", 1)
        text_line = parts[1] if len(parts) > 1 else b""
    rest = text_line.strip()
    if rest.startswith(b'"') and rest.endswith(b'"') and len(rest) >= 2:
        name = rest[1:-1]
    else:
        name = rest
    raw = name.decode("utf-8", "surrogateescape")
    # 字面量尾部 {n} 形式：名字在下一行，交给上层忽略
    raw = re.sub(r"^\{\d+\}\r?\n", "", raw)
    return flags, raw


def parse_status(data: list) -> dict:
    out: dict[str, int] = {}
    if not data:
        return out
    blob = b" ".join(x for x in data if isinstance(x, bytes))
    for m in _STATUS_RE.finditer(blob.decode("utf-8", "replace")):
        out[m.group(1).lower()] = int(m.group(2))
    return out


def _fetch_item(data) -> tuple[str, bytes]:
    """从 FETCH 响应里取 (元数据文本, 内容字节)。

    兼容 imaplib.uid() 直接返回的 (typ, data) 二元组。
    """
    if isinstance(data, tuple) and len(data) == 2 and isinstance(data[0], str):
        data = data[1]
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2:
            meta = item[0].decode("utf-8", "replace") if isinstance(item[0], bytes) else str(item[0])
            return meta, item[1] or b""
    return "", b""


def _decode_part(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charsets = [part.get_content_charset(), "utf-8", "gb18030", "gbk", "big5", "latin-1"]
    for cs in charsets:
        if not cs:
            continue
        try:
            return payload.decode(cs, "strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", "replace")


def message_texts(msg: email.message.Message) -> tuple[str, str]:
    """拆出 (纯文本, HTML)，包括「以附件形式转发」的 .eml 里的正文。

    邮箱里常见的转发方式是把原邮件当 .eml 附件转发（QQ「作为附件转发」、
    Outlook 的 message/rfc822），正文是空的 —— 不递归进去就永远认不出验证码。
    """
    plain: list[str] = []
    html: list[str] = []
    _walk_parts(msg, plain, html, 0)
    return "\n".join(plain), "\n".join(html)


_MAX_EML_BYTES = 512 * 1024
_MAX_DEPTH = 3


def _walk_parts(part, plain: list[str], html: list[str], depth: int) -> None:
    if depth > _MAX_DEPTH:
        return
    ctype = part.get_content_type()

    if ctype == "message/rfc822":
        payload = part.get_payload()
        for sub in payload if isinstance(payload, list) else []:
            _walk_parts(sub, plain, html, depth + 1)
        return

    if part.is_multipart():
        for sub in part.get_payload() or []:
            _walk_parts(sub, plain, html, depth + 1)
        return

    if ctype == "text/plain":
        plain.append(_decode_part(part))
        return
    if ctype == "text/html":
        html.append(_decode_part(part))
        return

    filename = decode_mime(part.get_filename())
    low = (filename or "").lower()
    if not (low.endswith(".eml") or low.endswith(".msg")):
        return
    # 附件名里也可能带验证码（如「241302 是你的登录验证码.eml」）
    if filename:
        plain.append(filename)
    if low.endswith(".msg"):
        return  # Outlook 二进制格式，不解析内容
    data = part.get_payload(decode=True) or b""
    if not data or len(data) > _MAX_EML_BYTES:
        return
    try:
        inner = email.message_from_bytes(data)
    except Exception:
        return
    inner_from = decode_mime(inner.get("From"))
    if inner_from:
        # 让「转发邮件从正文猜站点」有干净的来源
        plain.append("发件人：" + inner_from)
    plain.append(decode_mime(inner.get("Subject")))
    _walk_parts(inner, plain, html, depth + 1)


# ---------------------------------------------------------------- 账号会话

class AccountSession:
    def __init__(self, acct: dict, cfg: dict, log):
        self.acct = acct
        self.cfg = cfg
        self.log = log
        self.label = conf.account_label(acct)
        self.conn: imaplib.IMAP4_SSL | None = None
        self._folders: list[tuple[str, str]] = []
        self._folders_ts = 0.0
        self.last_skipped: list[str] = []

    # -- 连接

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.logout()
            except Exception:
                pass
        self.conn = None

    def ensure(self) -> imaplib.IMAP4_SSL:
        if self.conn is not None:
            return self.conn
        host = self.acct["host"]
        port = int(self.acct.get("port", 993))
        code = conf.get_secret(self.acct)
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=45)
        conn.login(self.acct["user"], code)
        self.conn = conn
        if self.acct.get("needs_id", True):
            self._send_id(conn)
        self.log.info(f"[{self.label}] 已连接 {host}")
        return conn

    def _send_id(self, conn: imaplib.IMAP4_SSL) -> None:
        """163/126 不发 ID 会报 Unsafe Login。"""
        try:
            typ, dat = conn._simple_command(
                "ID", '("name" "mail_otp" "version" "1.0" "vendor" "local")'
            )
            conn._untagged_response(typ, dat, "ID")
        except Exception as exc:
            self.log.warning(f"[{self.label}] IMAP ID 发送失败：{exc}")

    # -- 文件夹

    def list_folders(self, force: bool = False) -> list[tuple[str, str]]:
        conn = self.ensure()
        ttl = int(self.cfg.get("folder_ttl_seconds", 300))
        if self._folders and not force and time.time() - self._folders_ts < ttl:
            return self._folders
        typ, data = conn.list()
        if typ != "OK":
            raise imaplib.IMAP4.error(f"LIST 失败：{data}")
        skipped = self._skip_terms()
        folders: list[tuple[str, str]] = []
        skipped_names: list[str] = []
        for line in data or []:
            if not line or not isinstance(line, bytes):
                continue
            flags, raw = parse_list_line(line)
            if "\\noselect" in flags.lower():
                continue
            if not raw:
                continue
            pretty = utf7_decode(raw)
            if self._is_skipped(raw, pretty, skipped):
                skipped_names.append(pretty)
                continue
            folders.append((raw, pretty))
        # 收件箱排最前
        folders.sort(key=lambda x: (0 if x[1].lower() in ("inbox", "收件箱") else 1, x[1]))
        self._folders = folders
        self._folders_ts = time.time()
        self.last_skipped = skipped_names
        if skipped_names:
            self.log.info(f"[{self.label}] 跳过文件夹：" + "、".join(skipped_names))
        return folders

    def _skip_terms(self) -> list[str]:
        terms = self.cfg.get("skip_folders")
        if terms is None:
            terms = conf.DEFAULT_CONFIG.get("skip_folders", [])
        if isinstance(terms, str):
            terms = [terms]
        return [str(t).strip().lower() for t in terms if str(t).strip()]

    @staticmethod
    def _is_skipped(raw: str, pretty: str, terms: list[str]) -> bool:
        """已发送 / 草稿 / 已删除 这类文件夹不扫：自己发出去的验证码不该弹窗。"""
        if not terms:
            return False
        haystack = [pretty.lower(), raw.lower()]
        haystack += [part.lower() for part in pretty.replace("\\", "/").split("/")]
        return any(term in item for item in haystack for term in terms)

    # -- 扫描

    def poll(self, store) -> list[OtpEvent]:
        cfg = self.cfg
        events: list[OtpEvent] = []
        self.ensure()
        for raw, pretty in self.list_folders():
            try:
                events.extend(self._poll_folder(raw, pretty, store, cfg))
            except (imaplib.IMAP4.abort, OSError) as exc:
                self.log.warning(f"[{self.label}] {pretty} 连接中断：{exc}，重连后继续")
                self.close()
                try:
                    self.ensure()
                except Exception as exc2:
                    self.log.warning(f"[{self.label}] 重连失败：{exc2}")
                    break
            except Exception as exc:
                self.log.warning(f"[{self.label}] {pretty} 扫描失败：{exc}")
        store.save()
        return events

    def _poll_folder(self, raw: str, pretty: str, store, cfg: dict) -> list[OtpEvent]:
        conn = self.ensure()
        quoted = quote_mailbox(raw)
        typ, data = conn.status(quoted, "(MESSAGES UIDNEXT)")
        if typ != "OK":
            raise imaplib.IMAP4.error(f"STATUS 失败：{data}")
        st = parse_status(data)
        key = f"{self.acct.get('user')}|{raw}"
        state = store.folder(key)

        if state is None:
            if cfg.get("initial_baseline", True):
                return self._baseline(conn, quoted, key, pretty, st, store)
            state = {"last_uid": 0, "messages": 0, "uidnext": 0}

        last_uid = int(state.get("last_uid", 0))
        # 服务器给了 UIDNEXT（QQ）就先比对，没变化直接跳过；
        # 163 的 STATUS 不回 UIDNEXT，这时退化成「EXAMINE + UID SEARCH」：
        # 仍然只读、不 FETCH 正文，扫不到新 UID 就返回，代价很小。
        if "uidnext" in st and (
            int(state.get("uidnext", -1)) == int(st["uidnext"])
            and int(state.get("messages", -1)) == int(st.get("messages", -2))
        ):
            return []

        store.set_folder(
            key,
            {
                "last_uid": last_uid,
                "messages": st.get("messages", 0),
                "uidnext": st.get("uidnext", 0),
                "checked_at": time.time(),
            },
        )

        typ, _ = conn.select(quoted, readonly=True)
        if typ != "OK":
            raise imaplib.IMAP4.error("EXAMINE 失败")
        typ, data = conn.uid("SEARCH", "UID", f"{last_uid + 1}:*")
        if typ != "OK":
            raise imaplib.IMAP4.error(f"SEARCH 失败：{data}")
        uids = sorted({int(x) for x in (data[0].split() if data and data[0] else []) if int(x) > last_uid})
        if not uids:
            store.set_folder(
                key,
                {
                    "last_uid": last_uid,
                    "messages": st.get("messages", 0),
                    "uidnext": st.get("uidnext", 0),
                    "checked_at": time.time(),
                },
            )
            return []

        cap = max(1, int(cfg.get("max_mails_per_folder", 25)))
        events: list[OtpEvent] = []
        for uid in uids[:cap]:
            try:
                found = self._handle_mail(conn, uid, key, pretty, store, cfg)
            except Exception as exc:
                self.log.warning(f"[{self.label}] {pretty} UID {uid} 读取失败：{exc}")
                found = []
            store.set_folder(
                key,
                {
                    "last_uid": uid,
                    "messages": st.get("messages", 0),
                    "uidnext": st.get("uidnext", 0),
                    "checked_at": time.time(),
                },
            )
            events.extend(found)
        return events

    def _baseline(self, conn, quoted: str, key: str, pretty: str, st: dict, store) -> list[OtpEvent]:
        """首次见到这个文件夹：只记下最大 UID，不弹旧邮件。"""
        max_uid = 0
        typ, _ = conn.select(quoted, readonly=True)
        if typ == "OK":
            typ, data = conn.uid("SEARCH", None, "ALL")
            if typ == "OK" and data and data[0]:
                uids = [int(x) for x in data[0].split() if x.isdigit()]
                max_uid = max(uids) if uids else 0
        store.set_folder(
            key,
            {
                "last_uid": max_uid,
                "messages": st.get("messages", 0),
                "uidnext": st.get("uidnext", 0),
                "initialized_at": time.time(),
            },
        )
        self.log.info(f"[{self.label}] {pretty} 建立基线（已有 {st.get('messages', '?')} 封，起始 UID {max_uid}）")
        return []

    def _handle_mail(self, conn, uid: int, key: str, pretty: str, store, cfg: dict) -> list[OtpEvent]:
        meta, header = _fetch_item(conn.uid("FETCH", str(uid), "(RFC822.SIZE BODY.PEEK[HEADER])"))
        size_m = re.search(r"RFC822\.SIZE (\d+)", meta)
        size = int(size_m.group(1)) if size_m else 0
        hdr_msg = email.message_from_bytes(header)
        msgid = (hdr_msg.get("Message-ID") or "").strip() or f"{key}#{uid}"
        if store.seen(msgid):
            return []

        subject = decode_mime(hdr_msg.get("Subject"))
        from_name_raw, from_addr = parseaddr(hdr_msg.get("From", "") or "")
        from_name = decode_mime(from_name_raw)

        max_body = int(cfg.get("max_body_bytes", 400000))
        plain = html = ""
        if 0 < size <= max_body:
            _m, raw = _fetch_item(conn.uid("FETCH", str(uid), "(BODY.PEEK[])"))
            if raw:
                msg = email.message_from_bytes(raw)
                plain, html = message_texts(msg)

        if not plain and not html:
            # 太大或没抓到正文：退一步只看标题
            texts = subject
        else:
            texts = subject + "\n" + otp.clean_bodies(plain, html)

        results = otp.extract_all(texts, limit=int(cfg.get("max_codes_per_mail", 3)))
        store.mark_seen(msgid)
        if not results:
            return []
        site = otp.site_from(subject, from_name, from_addr)
        # 转发来的邮件：发件人是你自己，站点名在正文的「原始邮件 / 发件人：」里
        forwarded = bool(otp.FORWARD_HINT.search(texts[:2000])) or subject.lower().startswith(
            ("fwd:", "fw:", "转发")
        )
        if forwarded or site in ("未知站点", ""):
            site = otp.site_from_body(texts) or site or "未知站点"
        return [
            OtpEvent(
                account=self.label,
                folder=pretty,
                code=res.code,
                site=site,
                sender=from_name or from_addr or "(未知发件人)",
                subject=subject,
                msgid=msgid,
                uid=uid,
                received_at=time.time(),
            )
            for res in results
        ]


# ---------------------------------------------------------------- 校验登录

def test_login(acct: dict, code: str, timeout: int = 30) -> tuple[bool, str]:
    """用给定授权码试登录，返回 (成功, 提示)。不落盘。"""
    host = acct["host"]
    port = int(acct.get("port", 993))
    try:
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=timeout)
    except Exception as exc:
        return False, f"连不上 {host}:{port}（{exc}）"
    try:
        typ, data = conn.login(acct["user"], code)
        if typ != "OK":
            return False, f"登录被拒：{_err_text(data)}"
        if acct.get("needs_id", True):
            try:
                conn._simple_command("ID", '("name" "mail_otp" "version" "1.0")')
            except Exception:
                pass
        typ, data = conn.status('"INBOX"', "(MESSAGES)")
        if typ != "OK":
            return False, f"登录成功但读收件箱失败：{_err_text(data)}"
        return True, "登录成功"
    except imaplib.IMAP4.error as exc:
        return False, f"登录失败：{_err_text(exc)}"
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _err_text(data) -> str:
    if isinstance(data, (list, tuple)):
        parts = []
        for item in data:
            if isinstance(item, bytes):
                parts.append(item.decode("utf-8", "replace"))
            elif item is not None:
                parts.append(str(item))
        return " ".join(parts) or "无响应"
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return str(data)
