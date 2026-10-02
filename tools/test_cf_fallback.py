"""Test: app._cf_fallback, the sequential stand-in used by app.backup / app.aitag / app.analysis when
concurrent.futures isn't bundled into an old install's frozen exe (a code update can't add it there --
see the comment in _cf_fallback.py). Also checks the three real modules still import cleanly and pick
up the real concurrent.futures in the normal case.

    py -3.12 tools/test_cf_fallback.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import _cf_fallback as cff  # noqa: E402

with cff.ThreadPoolExecutor(2) as ex:
    fut = ex.submit(lambda x: x * 2, 21)
    check("submit() runs the work and result() returns it", fut.result() == 42, fut.result())

def boom():
    raise ValueError("nope")
with cff.ThreadPoolExecutor(2) as ex:
    bad = ex.submit(boom)
    try:
        bad.result()
        check("a failing submit() re-raises on result()", False)
    except ValueError as e:
        check("a failing submit() re-raises on result()", str(e) == "nope")

with cff.ThreadPoolExecutor() as ex:
    out = list(ex.map(lambda x: x + 1, [1, 2, 3]))
    check("map() runs every item in order", out == [2, 3, 4], out)

with cff.ThreadPoolExecutor() as ex:
    futs = [ex.submit(lambda x: x, i) for i in range(5)]
    done = [f.result() for f in cff.as_completed(futs)]
    check("as_completed() yields every future", sorted(done) == [0, 1, 2, 3, 4], done)

ex2 = cff.ThreadPoolExecutor()
ex2.shutdown()
check("shutdown() outside a `with` block doesn't raise", True)

for mod_name in ("app.backup", "app.aitag", "app.analysis"):
    import importlib
    m = importlib.import_module(mod_name)
    check(f"{mod_name} imports cleanly and cf has ThreadPoolExecutor", hasattr(m.cf, "ThreadPoolExecutor"))

import concurrent.futures as real_cf  # noqa: E402
from app import backup, aitag, analysis  # noqa: E402
check("in the normal case (module present), backup.py uses the real concurrent.futures", backup.cf is real_cf)
check("...same for aitag.py", aitag.cf is real_cf)
check("...same for analysis.py", analysis.cf is real_cf)

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
