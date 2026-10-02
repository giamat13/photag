"""Guard for code updates (see codeboot.py): a code update only runs on the exe it was made for. When the libraries or the
packaging change (requirements.txt, the .spec files), codeboot.RUNTIME must be bumped, otherwise old programs would
download code that needs libraries they do not contain. This also covers a NEW top-level import appearing anywhere in
app/*.py (stdlib or third-party): a module an old frozen exe never bundled can be just as missing as a pip package would
be, even though it needs no requirements.txt entry -- see app/_cf_fallback.py for a real case of this (concurrent.futures).

    py -3.12 tools/test_runtime_lock.py            checks runtime.lock
    py -3.12 tools/test_runtime_lock.py --update   after bumping RUNTIME on purpose: rewrites runtime.lock
"""
import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import codeboot  # noqa: E402

FILES = ("requirements.txt", "photag.spec", "photag_backup.spec", "codeboot.py_signature")


def _top_level_imports() -> list[str]:
    """Every module name imported anywhere in app/*.py (root package only: "concurrent" for
    "concurrent.futures"), excluding this package's own relative imports."""
    names = set()
    for f in sorted((ROOT / "app").glob("*.py")):
        tree = ast.parse(f.read_text("utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return sorted(names)


def fingerprint() -> str:
    h = hashlib.sha256()
    for name in FILES[:3]:
        h.update(name.encode())
        h.update((ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
    h.update(",".join(_top_level_imports()).encode())
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
print(("PASS " if ok_fp else "FAIL ") + "requirements.txt, the .spec files and app/*.py's top-level imports are unchanged since runtime.lock was written")
print(("PASS " if ok_rt else "FAIL ") + f"codeboot.RUNTIME ({now['runtime']}) matches runtime.lock ({lock['runtime']})")
if not (ok_fp and ok_rt):
    print("\nThe libraries or packaging changed: bump RUNTIME in codeboot.py, then run  py -3.12 tools/test_runtime_lock.py --update\n"
          "(or, if only the lock is stale because RUNTIME was bumped already, just run --update).")
    sys.exit(1)
sys.exit(0)
