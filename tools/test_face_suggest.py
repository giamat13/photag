"""Test: faces.suggest_names() -- for an unnamed face cluster, suggest an already-named person
when the cluster's average embedding is close enough to theirs ("is this <name>?" in the People
view), and stay silent otherwise. Pure numpy/sqlite, no face-detection model involved.

    py -3.12 tools/test_face_suggest.py
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_face_suggest_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import config, db, faces  # noqa: E402


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


_sha_n = [0]
def add_photo(con, path):
    _sha_n[0] += 1
    con.execute("INSERT INTO photos(rel_path,sha256,is_video,faces_done) VALUES(?,?,0,1)", (path, f"sha{_sha_n[0]}"))
    return con.execute("SELECT last_insert_rowid() id").fetchone()["id"]


def add_face(con, photo_id, emb, person_id=None, cluster_id=None):
    con.execute("INSERT INTO faces(photo_id,x1,y1,x2,y2,det_score,embedding,cluster_id,person_id) "
                "VALUES(?,0,0,1,1,0.9,?,?,?)", (photo_id, unit(emb).tobytes(), cluster_id, person_id))


config.PATHS.root.mkdir(parents=True, exist_ok=True)
con = db.init_db()

con.execute("INSERT INTO people(id,name,source) VALUES(1,'Danny','manual')")
p1 = add_photo(con, "a.jpg"); p2 = add_photo(con, "b.jpg"); p3 = add_photo(con, "c.jpg"); p4 = add_photo(con, "d.jpg")

danny_dir = unit([1, 0, 0, 0])
add_face(con, p1, danny_dir + unit([0.02, 0.01, 0, 0]) * 0.05, person_id=1)
add_face(con, p2, danny_dir + unit([-0.01, 0.02, 0, 0]) * 0.05, person_id=1)
# a new, unnamed cluster that is close to Danny's centroid (different session/lighting)
add_face(con, p3, danny_dir + unit([0, 0, 0.08, 0]) * 0.4, cluster_id=100)
# a cluster for a clearly different person -- orthogonal embedding, far from Danny
add_face(con, p4, unit([0, 1, 0, 0]), cluster_id=200)
con.commit()

sug = faces.suggest_names(con)
check("the close unnamed cluster gets suggested as the named person", sug.get(100, (None,))[0] == 1, sug.get(100))
check("the suggestion carries the person's name", sug.get(100, (None, None))[1] == "Danny", sug.get(100))
check("a distant cluster is not suggested", 200 not in sug, sug.get(200))
check("no false suggestion when nobody is named yet", True)  # covered by the empty-`named` early return below

con2 = db.connect()
con2.execute("DELETE FROM faces"); con2.execute("DELETE FROM people"); con2.commit()
add_face(con2, p1, unit([1, 0, 0, 0]), cluster_id=1)
con2.commit()
check("suggest_names() is empty when no one is named yet", faces.suggest_names(con2) == {})

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
