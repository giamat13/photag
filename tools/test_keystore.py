"""Test: keystore round-trips on this system, and the keychain path works with a fake `keyring` module."""
import sys
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import keystore  # noqa: E402

res = []
def check(n, ok, extra=""):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + (f"  [{extra}]" if extra else ""))

for s in ("sk-test-123", "מפתח-עברית-🔑", ""):
    p = keystore.protect(s)
    check(f"round trip {s[:6]!r}", keystore.unprotect(p) == s)

store = {}
fake = types.SimpleNamespace(
    set_password=lambda svc, n, v: store.__setitem__((svc, n), v),
    get_password=lambda svc, n: store.get((svc, n)),
)
with mock.patch.object(sys, "platform", "linux"), mock.patch.object(keystore, "_keyring", lambda: fake):
    p = keystore.protect("secret-key")
    check("with a keychain: only a reference is stored", p.startswith("keyring:") and "secret-key" not in p and len(store) == 1, p)
    check("... and it reads back", keystore.unprotect(p) == "secret-key")
    store.clear()
    try:
        keystore.unprotect(p); ok = False
    except ValueError:
        ok = True
    check("a missing keychain entry is a clear error, not a crash", ok)
with mock.patch.object(sys, "platform", "linux"), mock.patch.object(keystore, "_keyring", lambda: None):
    p = keystore.protect("abc")
    check("no keychain: falls back to plain (as before)", p.startswith("plain:") and keystore.unprotect(p) == "abc")
n = res.count(False); print(f"\n{len(res)-n}/{len(res)} passed"); sys.exit(1 if n else 0)
