"""Windows DPAPI 加解密（ctypes 直调 crypt32，无需第三方库）。

加密结果只对「当前 Windows 用户 + 本机」可解；把 config.json 拷到别的机器/别的
用户下都解不开 —— 授权码明文不落盘。
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes

CRYPTPROTECT_UI_FORBIDDEN = 0x1

# 附加熵：让密文与「本程序」绑定，别的程序即使同用户也解不出来
_ENTROPY = b"mail_otp.assistant.v1"

_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _Blob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(_Blob),
    wintypes.LPCWSTR,
    ctypes.POINTER(_Blob),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_Blob),
]
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(_Blob),
    ctypes.POINTER(wintypes.LPWSTR),
    ctypes.POINTER(_Blob),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_Blob),
]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p


def _make_blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    # create_string_buffer 保活，直到调用结束
    buf = ctypes.create_string_buffer(data, max(len(data), 1))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _take_blob(blob: _Blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        if blob.pbData:
            _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))


def protect(plain: str) -> str:
    """明文 -> base64(DPAPI 密文)。"""
    if not isinstance(plain, str):
        raise TypeError("protect() 需要 str")
    data = plain.encode("utf-8")
    blob_in, _b1 = _make_blob(data)
    entropy, _b2 = _make_blob(_ENTROPY)
    blob_out = _Blob()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "mail_otp",
        ctypes.byref(entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData 失败")
    return base64.b64encode(_take_blob(blob_out)).decode("ascii")


def unprotect(token: str) -> str:
    """base64(DPAPI 密文) -> 明文。"""
    raw = base64.b64decode(token.encode("ascii"), validate=False)
    blob_in, _b1 = _make_blob(raw)
    entropy, _b2 = _make_blob(_ENTROPY)
    blob_out = _Blob()
    desc = wintypes.LPWSTR()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        ctypes.byref(desc),
        ctypes.byref(entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if desc:
        _kernel32.LocalFree(ctypes.cast(desc, ctypes.c_void_p))
    if not ok:
        raise OSError(
            ctypes.get_last_error(),
            "CryptUnprotectData 失败（换了用户/机器，或密文被改过）",
        )
    return _take_blob(blob_out).decode("utf-8")
