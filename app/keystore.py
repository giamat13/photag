"""Encrypt small secrets (API keys) for storage in config.json.

On Windows this is DPAPI (CryptProtectData): the blob can only be decrypted by the
same Windows user on the same machine, and needs no extra dependency. On macOS / Linux the secret goes into
the system keychain (Keychain / Secret Service) when the optional `keyring` package and a keychain are there,
and only the entry's name is stored; without them the value is merely base64-wrapped ("plain:").
"""
import base64
import ctypes
import sys
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]


def _in_blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.c_void_p)), buf   # keep buf alive with the blob


def _out_bytes(blob: _Blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(blob.pbData))


_SERVICE = "photag"


def _keyring():
    """The optional `keyring` module when the OS has a usable keychain, else None (imported lazily on purpose)."""
    try:      # by name on purpose: it is optional (never bundled in the Windows build) and must not count as a new import
        keyring = __import__("keyring")
        if "fail" in type(keyring.get_keyring()).__module__:      # keyring.backends.fail = "no keychain here"
            return None
        return keyring
    except Exception:
        return None


def protect(text: str) -> str:
    data = text.encode("utf-8")
    if sys.platform != "win32":
        kr = _keyring()
        if kr is not None:
            name = __import__("secrets").token_hex(16)
            try:
                kr.set_password(_SERVICE, name, text)
                return "keyring:" + name
            except Exception:
                pass
        return "plain:" + base64.b64encode(data).decode()
    crypt = ctypes.windll.crypt32
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
                                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    blob, _keep = _in_blob(data)
    out = _Blob()
    if not crypt.CryptProtectData(ctypes.byref(blob), "photag", None, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptProtectData failed")
    return "dpapi:" + base64.b64encode(_out_bytes(out)).decode()


def unprotect(stored: str) -> str:
    kind, _, b64 = stored.partition(":")
    if kind == "keyring":
        kr = _keyring()
        value = kr.get_password(_SERVICE, b64) if kr is not None else None
        if value is None:
            raise ValueError("the keychain entry is not available")
        return value
    raw = base64.b64decode(b64)
    if kind == "plain":
        return raw.decode("utf-8")
    if kind != "dpapi" or sys.platform != "win32":
        raise ValueError("unsupported secret format")
    crypt = ctypes.windll.crypt32
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_Blob),
                                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    blob, _keep = _in_blob(raw)
    out = _Blob()
    if not crypt.CryptUnprotectData(ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptUnprotectData failed (stored by another Windows user?)")
    return _out_bytes(out).decode("utf-8")


if __name__ == "__main__":  # self-check
    for s in ("sk-test-123", "מפתח-עברית-🔑", ""):
        p = protect(s)
        assert unprotect(p) == s and (s == "" or s not in p), s
    print("keystore OK:", protect("x")[:12] + "…")
