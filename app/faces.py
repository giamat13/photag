"""Face recognition: InsightFace buffalo_l embeddings, cosine distance,
SciPy average-linkage clustering at threshold 0.38, then auto-name each cluster
from the Google Takeout people tags (majority vote)."""
import time
import numpy as np

from . import db, images
from .config import PATHS, FACE_MODEL, FACE_CLUSTER_THRESHOLD

_APP = None


def _app():
    """Lazy singleton. Downloads buffalo_l (~300MB) on first use. CPU by default;
    picks up onnxruntime-gpu automatically if installed."""
    global _APP
    if _APP is None:
        from insightface.app import FaceAnalysis
        _APP = FaceAnalysis(name=FACE_MODEL)
        _APP.prepare(ctx_id=0, det_size=(640, 640))
    return _APP


def _to_bgr(path):
    im = images.open_image(path)          # RGB, EXIF-oriented
    return np.asarray(im)[:, :, ::-1].copy()  # -> BGR for insightface


def detect_photo(con, photo_row):
    p = PATHS.media / photo_row["rel_path"]
    if not p.exists() or photo_row["is_video"]:
        con.execute("UPDATE photos SET faces_done=1 WHERE id=?", (photo_row["id"],))
        return 0
    try:
        faces = _app().get(_to_bgr(p))
    except Exception:
        con.execute("UPDATE photos SET faces_done=1 WHERE id=?", (photo_row["id"],))
        return 0
    for f in faces:
        emb = np.asarray(f.normed_embedding, dtype=np.float32)
        x1, y1, x2, y2 = [float(v) for v in f.bbox]
        con.execute(
            "INSERT INTO faces(photo_id,x1,y1,x2,y2,det_score,embedding) VALUES(?,?,?,?,?,?,?)",
            (photo_row["id"], x1, y1, x2, y2, float(f.det_score), emb.tobytes()))
    con.execute("UPDATE photos SET faces_done=1 WHERE id=?", (photo_row["id"],))
    return len(faces)


def cluster_all(con):
    """Average-linkage over cosine distance, cut at FACE_CLUSTER_THRESHOLD.
    ponytail: pdist is O(n^2) memory; fine for a personal library (~10k faces),
    swap for faiss/HDBSCAN if you ever index hundreds of thousands."""
    rows = con.execute("SELECT id, embedding FROM faces WHERE embedding IS NOT NULL").fetchall()
    if len(rows) < 2:
        return 0
    from scipy.cluster.hierarchy import linkage, fcluster
    from scipy.spatial.distance import pdist
    X = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    Z = linkage(pdist(X, metric="cosine"), method="average")
    labels = fcluster(Z, t=FACE_CLUSTER_THRESHOLD, criterion="distance")
    con.executemany("UPDATE faces SET cluster_id=? WHERE id=?",
                    [(int(c), r["id"]) for c, r in zip(labels, rows)])
    con.commit()
    return int(labels.max())


def name_clusters(con):
    """Assign a person to each cluster by majority vote of the Takeout people
    tags on the photos its faces appear in. Unmatched clusters stay unnamed
    ('אדם לא מזוהה') for the user to name in the UI."""
    clusters = con.execute(
        "SELECT cluster_id, GROUP_CONCAT(photo_id) pids, COUNT(*) n "
        "FROM faces WHERE cluster_id IS NOT NULL GROUP BY cluster_id ORDER BY n DESC").fetchall()
    taken = {}  # person_id -> set already claimed by a bigger cluster
    for c in clusters:
        pids = [int(x) for x in c["pids"].split(",")]
        q = ",".join("?" * len(pids))
        votes = con.execute(
            f"SELECT pp.person_id, COUNT(*) v FROM photo_people pp "
            f"WHERE pp.photo_id IN ({q}) GROUP BY pp.person_id ORDER BY v DESC", pids).fetchall()
        chosen = next((v["person_id"] for v in votes if v["person_id"] not in taken), None)
        if chosen is not None:
            taken[chosen] = True
            con.execute("UPDATE faces SET person_id=? WHERE cluster_id=?", (chosen, c["cluster_id"]))
            # give the person a representative face crop
            con.execute("UPDATE people SET cover_face_id=(SELECT id FROM faces WHERE cluster_id=? "
                        "ORDER BY det_score DESC LIMIT 1), source='cluster' WHERE id=?",
                        (c["cluster_id"], chosen))
    con.commit()


def run_faces(progress):
    con = db.connect()
    todo = con.execute("SELECT id, rel_path, is_video FROM photos WHERE faces_done=0 AND trashed=0").fetchall()
    progress.state = "detecting"; progress.total = len(todo); progress.done = 0
    for i, row in enumerate(todo, 1):
        detect_photo(con, row)
        progress.done = i
        if i % 20 == 0:
            progress.msg = f"מזהה פנים {i}/{len(todo)}"; con.commit()
    con.commit()
    progress.state = "clustering"; progress.msg = "מקבץ פנים…"
    n = cluster_all(con)
    name_clusters(con)
    progress.state = "done"; progress.msg = f"נמצאו {n} קבוצות פנים"
    con.commit()


if __name__ == "__main__":  # self-check: clustering separates two tight groups
    a = np.tile([1.0, 0, 0], (5, 1)) + np.random.randn(5, 3) * 0.01
    b = np.tile([0, 1.0, 0], (5, 1)) + np.random.randn(5, 3) * 0.01
    X = np.vstack([a, b]).astype(np.float32)
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    from scipy.cluster.hierarchy import linkage, fcluster
    from scipy.spatial.distance import pdist
    lab = fcluster(linkage(pdist(X, "cosine"), "average"), t=0.38, criterion="distance")
    assert len(set(lab)) == 2, lab
    print("clustering OK")
