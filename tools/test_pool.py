"""Test: app.pool, the thread pool used by app.backup / app.aitag / app.analysis. Unlike concurrent.futures it must
keep working after the main program ended (the window was closed and the icon next to the clock keeps photag alive from an
atexit handler -- see app/background.py): there ThreadPoolExecutor raised "can't register atexit after shutdown".

    py -3.12 tools/test_pool.py
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import pool  # noqa: E402

with pool.ThreadPoolExecutor(2) as ex:
    fut = ex.submit(lambda x: x * 2, 21)
    check("submit() runs the work and result() returns it", fut.result() == 42, fut.result())


def boom():
    raise ValueError("nope")


with pool.ThreadPoolExecutor(2) as ex:
    bad = ex.submit(boom)
    try:
        bad.result()
        check("a failing submit() re-raises on result()", False)
    except ValueError as e:
        check("a failing submit() re-raises on result()", str(e) == "nope")

with pool.ThreadPoolExecutor(3) as ex:
    out = list(ex.map(lambda x: x + 1, [1, 2, 3, 4, 5]))
    check("map() returns every result in order", out == [2, 3, 4, 5, 6], out)

with pool.ThreadPoolExecutor(3) as ex:
    futs = {ex.submit(lambda x: x, i): i for i in range(8)}
    done = [f.result() for f in pool.as_completed(futs)]
    check("as_completed() yields every future", sorted(done) == list(range(8)), done)

with pool.ThreadPoolExecutor(3) as ex:
    seen, lock = set(), threading.Lock()

    def slow(i):
        time.sleep(0.2)
        with lock:
            seen.add(threading.get_ident())
        return i
    t0 = time.time()
    list(ex.map(slow, range(6)))
    dt = time.time() - t0
    check("the work really runs in parallel", dt < 0.9 and len(seen) > 1, f"{dt:.2f}s, {len(seen)} threads")

ex = pool.ThreadPoolExecutor(1)
gate = threading.Event()
first = ex.submit(gate.wait)
queued = [ex.submit(lambda: 1) for _ in range(3)]
check("cancel() stops a queued job", all(q.cancel() for q in queued) and all(q.cancelled() for q in queued))
check("a cancelled job is still reported by as_completed()", len(list(pool.as_completed(queued))) == 3)
gate.set()
ex.shutdown(wait=True, cancel_futures=True)
check("shutdown() waits for the running job", first.result() in (True, None))

# the real case: work started from an atexit handler, after Python ended the main program
code = r"""
import atexit, sys
sys.path.insert(0, %r)
from app import pool
def late():
    with pool.ThreadPoolExecutor(2) as ex:
        print("RESULT", sorted(f.result() for f in pool.as_completed([ex.submit(lambda i=i: i * i, i) for i in range(4)])))
atexit.register(late)
""" % str(ROOT)
r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
check("a pool started from an atexit handler works", "RESULT [0, 1, 4, 9]" in r.stdout, (r.stdout + r.stderr).strip()[-200:])

code2 = code.replace("from app import pool", "import concurrent.futures as pool") .replace("pool.as_completed", "pool.as_completed")
r2 = subprocess.run([sys.executable, "-I", "-c", code2], capture_output=True, text=True, timeout=60)
print("info: concurrent.futures in the same situation ->", "works" if "RESULT" in r2.stdout else "fails (that is why app.pool exists)")

import importlib  # noqa: E402
for mod_name in ("app.backup", "app.aitag", "app.analysis"):
    m = importlib.import_module(mod_name)
    check(f"{mod_name} uses app.pool", m.cf is pool)

sys.exit(0 if all(res) else 1)
