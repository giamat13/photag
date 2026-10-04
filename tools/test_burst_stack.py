"""Test: analysis.find_bursts() -- rapid-fire sequences (a camera's burst/continuous mode) collapse
into one stack per run, naming the best photo, for the grid's optional "Stack Bursts" view.

    py -3.12 tools/test_burst_stack.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_burst_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h"), ("HOME", "h")):
    (tmp / v).mkdir(exist_ok=True)
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import analysis, config, db  # noqa: E402

config.PATHS.root.mkdir(parents=True, exist_ok=True)
con = db.init_db()


def add(rel, taken, phash, score, bytes_=1000, w=100, h=100, eyes_closed=0):
    phash = phash - (1 << 64) if phash >= 1 << 63 else phash   # signed two's complement, same as analysis.dhash()
    pid = con.execute("INSERT INTO photos(rel_path,sha256,taken_at,bytes,width,height) VALUES(?,?,?,?,?,?)",
                      (rel, rel, taken, bytes_, w, h)).lastrowid
    con.execute("INSERT INTO photo_analysis(photo_id,phash,score,eyes_closed) VALUES(?,?,?,?)",
               (pid, phash, score, eyes_closed))
    return pid


# a 5-shot burst, 1s apart, near-identical hashes, the 3rd is the sharpest/best
base = 0x1000000000000000
ids_burst = [add(f"b{i}.jpg", 1000 + i, base + i, 50 + (30 if i == 2 else 0)) for i in range(5)]
# a lone photo right after, far enough in time: not part of the burst
id_lone = add("lone.jpg", 1000 + 5 + 100, base + 99, 60)
# two photos close in time but visually unrelated (hash far apart): not a burst together
id_a = add("x1.jpg", 2000, 0x0000000000000000, 40)
id_b = add("x2.jpg", 2001, 0xFFFFFFFFFFFFFFFF, 45)
# a short run of only 2 near-identical, close-in-time photos: below BURST_MIN, stays ungrouped
id_pair1 = add("p1.jpg", 3000, 0x2000000000000000, 10)
id_pair2 = add("p2.jpg", 3001, 0x2000000000000001, 10)
con.commit()

bursts = analysis.find_bursts(con)
check("exactly one burst is found", len(bursts) == 1, len(bursts))
b = bursts[0]
check("the burst holds all 5 consecutive, similar, close-in-time photos", sorted(b["members"]) == sorted(ids_burst), b["members"])
check("the sharpest/highest-scored photo in the run is picked as best", b["best"] == ids_burst[2], b["best"])
check("a lone photo far in time isn't swept into the burst", id_lone not in b["members"])
check("visually unrelated photos close in time don't form a burst", not any(id_a in x["members"] or id_b in x["members"] for x in bursts))
check("a run shorter than BURST_MIN doesn't form a stack", not any(id_pair1 in x["members"] for x in bursts))

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
