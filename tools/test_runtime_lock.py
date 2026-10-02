"""Guard for code updates (see codeboot.py): a code update only runs on the exe it was made for. When the libraries or the
packaging change (requirements.txt, the .spec files), codeboot.RUNTIME must be bumped, otherwise old programs would
download code that needs libraries they do not contain.

    py -3.12 tools/test_runtime_lock.py            checks runtime.lock
    py -3.12 tools/test_runtime_lock.py --update   after bumping RUNTIME on purpose: rewrites runtime.lock
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import codeboot  # noqa: E402

FILES = ("requirements.txt", "photag.spec", "photag_backup.spec", "codeboot.py_signature")


def fingerprint() -> str:
    h = hashlib.sha256()
    for name in FILES[:3]:
        h.update(name.encode())
        h.update((ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


lock_file = ROOT / "runtime.lock"
now = {"runtime": codeboot.RUNTIME, "fingerprint": fingerprint()}
if "--update" in sys.argv:
    lock_file.write_text(json.dumps(now, indent=1) + "\n", "utf-8")
    print("runtime.lock updated:", now)
    sys.exit(0)
lock = json.loads(lock_file.read_text("utf-8"))
ok_fp = lock["fingerprint"] == now["fingerprint"]
ok_rt = lock["runtime"] == now["runtime"]
print(("PASS " if ok_fp else "FAIL ") + "requirements.txt and the .spec files are unchanged since runtime.lock was written")
print(("PASS " if ok_rt else "FAIL ") + f"codeboot.RUNTIME ({now['runtime']}) matches runtime.lock ({lock['runtime']})")
if not (ok_fp and ok_rt):
    print("\nThe libraries or packaging changed: bump RUNTIME in codeboot.py, then run  py -3.12 tools/test_runtime_lock.py --update\n"
          "(or, if only the lock is stale because RUNTIME was bumped already, just run --update).")
    sys.exit(1)
sys.exit(0)
