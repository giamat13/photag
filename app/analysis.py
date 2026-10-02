"""Photo analysis, all local: a quality score (1-100) per photo, blur / dark / screenshot / receipt detection for the
library cleanup report, closed-eyes detection (uses the face model once the user has run face detection), and a
perceptual hash that finds duplicates and near-duplicates (bursts) in a library, also by capture time and place.

Scores are heuristics tuned for "which of these shots is the keeper"; they rank photos against each other much better
than they judge a single photo in absolute terms. Thresholds are constants at the top so they are easy to retune."""
import concurrent.futures as cf
import math
import re
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from . import db, images
from .config import PATHS

ANALYSIS_VERSION = 1          # bump when the measurements change: every photo is analysed again
SIZE = 512                    # long side the measurements run on
BLUR_SHARP = 12.0             # sharpest-region Laplacian variance below this = blurry
DARK_MEAN, DARK_P95 = 38.0, 85.0
DUP_DIST = 4                  # hash bits that may differ for "the same picture" (a copy, resize or re-save)
SIM_DIST = 12                 # ... for "a similar shot", when time or place agree
TIME_WINDOW = 600             # seconds: shots this close in time belong to one series
PLACE_M = 150                 # metres: shots this close belong to one place
EAR_CLOSED = 0.2              # eye height / width below this = closed
LEFT_EYE = [35, 41, 40, 42, 39, 37, 33, 36]       # InsightFace 2d106 landmark indices
RIGHT_EYE = [89, 95, 94, 96, 93, 91, 87, 90]
SHOT_NAME = re.compile(r"screen[\s_-]?shot|screen[\s_-]?capture|screenrecord|צילום[\s_-]?מסך|capture d.[eé]cran|bildschirmfoto", re.I)
SCREEN_SIZES = {(1080, 1920), (1080, 2160), (1080, 2220), (1080, 2280), (1080, 2340), (1080, 2400), (1170, 2532), (1179, 2556),
                (1284, 2778), (1290, 2796), (750, 1334), (828, 1792), (1125, 2436), (1242, 2688), (1440, 2560), (1440, 3040),
                (1440, 3120), (1440, 3200), (720, 1280), (720, 1520), (720, 1600), (1920, 1080), (2560, 1440), (3840, 2160),
                (1366, 768), (1536, 864), (1440, 900), (1680, 1050), (2880, 1800), (2560, 1600), (3024, 1964), (3456, 2234)}


# ------------------------------------------------------------------ measurements
def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _laplacian_var(g: np.ndarray) -> float:
    lap = g[1:-1, 1:-1] * -4 + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    return float(lap.var())


def sharpness(g: np.ndarray) -> float:
    """Laplacian variance of the sharpest parts: the 80th percentile over a 4x4 grid of tiles, so a sharp subject in
    front of a (deliberately) blurred background still counts as sharp, and a uniformly blurred shot does not."""
    h, w = g.shape
    if h < 16 or w < 16:
        return 0.0
    vals = []
    for i in range(4):
        for j in range(4):
            t = g[i * h // 4:(i + 1) * h // 4, j * w // 4:(j + 1) * w // 4]
            if t.shape[0] > 4 and t.shape[1] > 4:
                vals.append(_laplacian_var(t))
    return float(np.percentile(vals, 80)) if vals else 0.0


def dhash(im: Image.Image) -> int | None:
    """64-bit difference hash (signed, to fit SQLite); None for flat images, whose hashes would all be alike."""
    g = np.asarray(im.convert("L").resize((9, 8), Image.LANCZOS), dtype=np.float32)
    if g.std() < 3:
        return None
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v - (1 << 64) if v >= 1 << 63 else v


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & 0xFFFFFFFFFFFFFFFF).count("1")


def quality_score(m: dict, eyes_closed: int | None = None, width: int = 0, height: int = 0) -> int:
    """1..100. Sharpness 60%, exposure 25%, contrast 15%; a closed eye cuts the score by 40%."""
    s_sharp = 100 * _clamp(math.log1p(m["sharp"]) / math.log1p(400))
    mean = m["mean"]
    s_expo = 100.0
    if mean < 70:
        s_expo -= (70 - mean) * 1.6
    elif mean > 190:
        s_expo -= (mean - 190) * 1.6
    s_expo -= 120 * max(0.0, m["clip_dark"] - 0.15) + 120 * max(0.0, m["clip_white"] - 0.15)
    s_expo = _clamp(s_expo, 0, 100)
    s_con = 100 * _clamp((m["p95"] - m["p5"]) / 150)
    score = 0.6 * s_sharp + 0.25 * s_expo + 0.15 * s_con
    if width and height and width * height < 400_000:
        score *= 0.9                                    # tiny pictures are rarely the keeper
    if eyes_closed:
        score *= 0.6
    return int(_clamp(round(score), 1, 100))


def measure(im: Image.Image, filename: str = "", fmt: str = "", has_camera: bool = False, width: int = 0, height: int = 0) -> dict:
    """Everything that can be read from the pixels (and the name / format) of one photo. `im` is RGB."""
    w0, h0 = width or im.width, height or im.height
    im = im.copy()
    im.thumbnail((SIZE, SIZE), Image.LANCZOS)
    rgb = np.asarray(im, dtype=np.float32)
    g = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    sat = float(np.mean((mx - mn) / np.maximum(mx, 1)))
    p5, p95 = np.percentile(g, [5, 95])
    mean = float(g.mean())
    ink_mask = g < min(150.0, mean - 45)
    # text-like structure: rows that contain ink alternate with blank rows many times (lines of text)
    rows = ink_mask.mean(axis=1) > 0.012
    flips = int(np.count_nonzero(rows[1:] != rows[:-1]))
    m = {"phash": dhash(im), "sharp": sharpness(g), "mean": mean, "p5": float(p5), "p95": float(p95),
         "clip_dark": float(np.mean(g < 8)), "clip_white": float(np.mean(g > 247)), "sat": sat,
         "ink": float(ink_mask.mean()), "white": float(np.mean(g > 165)), "has_camera": int(bool(has_camera))}
    portrait = h0 >= w0 * 1.25
    m["is_receipt"] = int(portrait and m["white"] >= 0.5 and sat < 0.14 and 0.015 < m["ink"] < 0.3 and flips >= 0.06 * g.shape[0])
    big = max(w0, h0) >= 900
    m["is_screenshot"] = int(bool(SHOT_NAME.search(filename or "")) or
                             (fmt.upper() == "PNG" and not has_camera and big and ((w0, h0) in SCREEN_SIZES or (h0, w0) in SCREEN_SIZES)))
    m["score"] = quality_score(m, None, w0, h0)
    return m


def _load(path: Path):
    """(small RGB image, format, has camera EXIF) without decoding more pixels than needed."""
    if images.is_raw(path):
        return images.open_image(path), "RAW", True
    with Image.open(path) as im0:
        fmt = im0.format or ""
        try:
            cam = bool(im0.getexif().get(0x010F) or im0.getexif().get(0x0110))
        except Exception:
            cam = False
        try:
            im0.draft("RGB", (SIZE * 2, SIZE * 2))      # JPEG: decode at reduced size
        except Exception:
            pass
        im = ImageOps.exif_transpose(im0).convert("RGB")
    return im, fmt, cam


def analyze_file(path: Path, filename: str, width: int = 0, height: int = 0) -> dict:
    im, fmt, cam = _load(path)
    return measure(im, filename, fmt, cam, width, height)


# ------------------------------------------------------------------ closed eyes
def eye_ratio(pts: np.ndarray) -> float:
    """Eye opening: extent across the eye's short axis over its long axis (rotation-proof, so a tilted head works)."""
    pts = np.asarray(pts, dtype=np.float64)
    c = pts - pts.mean(axis=0)
    _, _, vt = np.linalg.svd(c, full_matrices=False)
    proj = c @ vt.T
    ext = proj.max(axis=0) - proj.min(axis=0)
    return float(ext[1] / ext[0]) if ext[0] > 1e-6 else 1.0


def face_eyes_closed(lm106: np.ndarray) -> bool:
    return (eye_ratio(lm106[LEFT_EYE]) + eye_ratio(lm106[RIGHT_EYE])) / 2 < EAR_CLOSED


def face_model_present() -> bool:
    """The face model is downloaded by Face Detection (~300 MB); the eyes check never triggers that download itself."""
    return (Path.home() / ".insightface" / "models" / "buffalo_l").is_dir()


def detect_eyes(path: Path) -> tuple[int, int]:
    """(faces that count, of which with closed eyes)."""
    from . import faces
    bgr = faces._to_bgr(path)
    H, W = bgr.shape[:2]
    n = closed = 0
    for f in faces._app().get(bgr):
        x1, y1, x2, y2 = [float(v) for v in f.bbox]
        lm = getattr(f, "landmark_2d_106", None)
        if f.det_score < 0.6 or (x2 - x1) < 0.03 * W or lm is None:
            continue
        n += 1
        closed += int(face_eyes_closed(np.asarray(lm)))
    return n, closed


# ------------------------------------------------------------------ the job
COLS = ("phash", "sharp", "mean", "p5", "p95", "clip_dark", "clip_white", "sat", "ink", "white", "is_screenshot", "is_receipt", "has_camera", "score")


def _todo(con):
    return con.execute(
        "SELECT p.id, p.sha256, p.rel_path, p.filename, p.width, p.height FROM photos p "
        "LEFT JOIN photo_analysis a ON a.photo_id=p.id "
        "WHERE p.is_video=0 AND p.trashed=0 AND (a.photo_id IS NULL OR a.sha IS NOT p.sha256 OR a.version<>?) "
        "ORDER BY p.taken_at DESC", (ANALYSIS_VERSION,)).fetchall()


def pending_counts(con) -> dict:
    total = con.execute("SELECT COUNT(*) FROM photos WHERE is_video=0 AND trashed=0").fetchone()[0]
    todo = len(_todo(con))
    eyes = con.execute("SELECT COUNT(*) FROM photo_analysis a JOIN photos p ON p.id=a.photo_id "
                       "WHERE p.trashed=0 AND a.eyes_closed IS NULL AND a.faces_n IS NOT 0").fetchone()[0]
    return {"total": total, "pending": todo, "eyes_pending": eyes, "face_model": face_model_present()}


def _store(con, pid, sha, m):
    con.execute(
        f"INSERT INTO photo_analysis(photo_id,sha,version,{','.join(COLS)}) VALUES(?,?,?,{','.join('?' * len(COLS))}) "
        f"ON CONFLICT(photo_id) DO UPDATE SET sha=excluded.sha, version=excluded.version, faces_n=NULL, eyes_closed=NULL, "
        + ",".join(f"{c}=excluded.{c}" for c in COLS), (pid, sha, ANALYSIS_VERSION, *[m[c] for c in COLS]))


def _rescore(con, pid):
    r = con.execute("SELECT a.*, p.width, p.height FROM photo_analysis a JOIN photos p ON p.id=a.photo_id WHERE a.photo_id=?", (pid,)).fetchone()
    if r:
        con.execute("UPDATE photo_analysis SET score=? WHERE photo_id=?",
                    (quality_score(dict(r), r["eyes_closed"], r["width"] or 0, r["height"] or 0), pid))


def run_eyes(con, ids: list[int], progress=None, cancelled=lambda: False) -> int:
    """Fill faces_n / eyes_closed for the given photos and refresh their scores. Needs the face model on disk."""
    done = 0
    for pid in ids:
        if cancelled():
            break
        r = con.execute("SELECT rel_path FROM photos WHERE id=?", (pid,)).fetchone()
        try:
            n, c = detect_eyes(PATHS.media / r["rel_path"])
        except Exception:
            n, c = 0, 0
        con.execute("UPDATE photo_analysis SET faces_n=?, eyes_closed=? WHERE photo_id=?", (n, c, pid))
        _rescore(con, pid)
        done += 1
        if progress is not None:
            progress.done = done
            if done % 10 == 0:
                progress.say("Checking eyes {done}/{total}", done=done, total=progress.total)
                con.commit()
    con.commit()
    return done


def run_analysis(eyes: bool, progress):
    """Analyse every photo that has no (current) analysis; then, when the face model is installed, check eyes."""
    con = db.init_db()
    cancelled = lambda: bool(getattr(progress, "cancel", False))
    todo = _todo(con)
    progress.state = "analyzing"; progress.total = len(todo); progress.done = 0
    progress.say("Analysing photos {done}/{total}", done=0, total=len(todo))

    def work(r):
        if cancelled():
            return r, None
        try:
            return r, analyze_file(PATHS.media / r["rel_path"], r["filename"] or "", r["width"] or 0, r["height"] or 0)
        except Exception:
            return r, False

    ok = fails = 0
    with cf.ThreadPoolExecutor(3) as ex:
        for r, m in ex.map(work, todo):
            if m is None:
                continue
            if m is False:
                fails += 1          # unreadable: leave it without analysis (retried next time)
            else:
                _store(con, r["id"], r["sha256"], m); ok += 1
            progress.done = ok + fails
            if progress.done % 25 == 0:
                progress.say("Analysing photos {done}/{total}", done=progress.done, total=len(todo)); con.commit()
    con.commit()
    msgs = [("Analysed {n} photos", {"n": ok})]
    if fails:
        msgs.append(("{n} failed", {"n": fails}))
    if cancelled():
        progress.state = "done"; return progress.say_parts(("Analysis was stopped", {}), *msgs)
    if eyes:
        ids = [r["photo_id"] for r in con.execute(
            "SELECT a.photo_id FROM photo_analysis a JOIN photos p ON p.id=a.photo_id "
            "WHERE p.trashed=0 AND a.eyes_closed IS NULL AND a.faces_n IS NOT 0 AND a.sharp>=? ORDER BY p.taken_at DESC", (BLUR_SHARP / 2,))]
        if ids and face_model_present():
            progress.total = len(ids); progress.done = 0; progress.state = "analyzing"
            run_eyes(con, ids, progress, cancelled)
            msgs.append(("Checked eyes in {n} photos", {"n": progress.done}))
        elif ids:
            msgs.append(("Eyes were not checked: run Face Detection first", {}))
    progress.state = "done"
    progress.say_parts(*msgs)


# ------------------------------------------------------------------ ranking a selection
def rank(con, ids: list[int], eyes: bool = True) -> list[dict]:
    """Scores for the given photos (analysing the ones not analysed yet), best first."""
    ids = list(dict.fromkeys(ids))[:200]
    q = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in con.execute(
        f"SELECT id, sha256, rel_path, filename, width, height, bytes, is_video FROM photos WHERE id IN ({q})", ids)}
    for pid in ids:
        r = rows.get(pid)
        if not r or r["is_video"]:
            continue
        a = con.execute("SELECT sha, version FROM photo_analysis WHERE photo_id=?", (pid,)).fetchone()
        if not a or a["sha"] != r["sha256"] or a["version"] != ANALYSIS_VERSION:
            try:
                _store(con, pid, r["sha256"], analyze_file(PATHS.media / r["rel_path"], r["filename"] or "", r["width"] or 0, r["height"] or 0))
            except Exception:
                continue
    con.commit()
    if eyes and face_model_present():
        todo = [pid for pid in ids if (con.execute("SELECT eyes_closed, faces_n FROM photo_analysis WHERE photo_id=?", (pid,)).fetchone() or {"eyes_closed": 1, "faces_n": 0})["eyes_closed"] is None]
        if todo:
            run_eyes(con, todo)
    out = []
    for pid in ids:
        a = con.execute("SELECT * FROM photo_analysis WHERE photo_id=?", (pid,)).fetchone()
        if a:
            out.append({"id": pid, "score": a["score"], "sharp": round(a["sharp"], 1), "mean": round(a["mean"]),
                        "faces": a["faces_n"], "eyes_closed": a["eyes_closed"], "bytes": rows[pid]["bytes"] or 0})
    out.sort(key=lambda x: (-x["score"], x["eyes_closed"] or 0, -x["bytes"]))
    for i, x in enumerate(out):
        x["rank"] = i + 1
    return out


# ------------------------------------------------------------------ duplicates and similar shots
def _km(a_lat, a_lng, b_lat, b_lng) -> float:
    R = math.pi / 180
    a = math.sin((b_lat - a_lat) * R / 2) ** 2 + math.cos(a_lat * R) * math.cos(b_lat * R) * math.sin((b_lng - a_lng) * R / 2) ** 2
    return 12742 * math.asin(min(1, math.sqrt(a)))


def find_groups(con, dup_dist: int = DUP_DIST, sim_dist: int = SIM_DIST, window: int = TIME_WINDOW, place_m: int = PLACE_M) -> list[dict]:
    """Groups of photos that show the same picture ('copy': hash within dup_dist) or the same moment (hash within sim_dist
    AND taken within `window` seconds, or within place_m metres). Each group names its best member and why it is a group."""
    rows = con.execute(
        "SELECT p.id, p.taken_at, p.lat, p.lng, p.bytes, p.width, p.height, p.filename, a.phash, a.score, a.eyes_closed, a.sharp "
        "FROM photos p JOIN photo_analysis a ON a.photo_id=p.id WHERE p.trashed=0 AND p.is_video=0 AND a.phash IS NOT NULL").fetchall()
    n = len(rows)
    if n < 2:
        return []
    H = [r["phash"] & 0xFFFFFFFFFFFFFFFF for r in rows]
    cand: set[tuple[int, int]] = set()
    # same time: neighbours in capture order within the window
    order = sorted((i for i in range(n) if rows[i]["taken_at"]), key=lambda i: rows[i]["taken_at"])
    for a, i in enumerate(order):
        for j in order[a + 1:a + 41]:
            if rows[j]["taken_at"] - rows[i]["taken_at"] > window:
                break
            cand.add((min(i, j), max(i, j)))
    # same picture anywhere: pigeonhole bands (two hashes within 6 bits agree completely on one of 7 bands)
    cuts = [0, 9, 18, 27, 36, 45, 54, 64]
    for b in range(7):
        mask, shift = (1 << (cuts[b + 1] - cuts[b])) - 1, cuts[b]
        buckets: dict[int, list[int]] = {}
        for i, h in enumerate(H):
            buckets.setdefault((h >> shift) & mask, []).append(i)
        for ids in buckets.values():
            if 1 < len(ids) <= 120:
                for x in range(len(ids)):
                    for y in range(x + 1, len(ids)):
                        cand.add((ids[x], ids[y]))
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x

    links: list[tuple[int, int, set[str]]] = []
    for i, j in cand:
        d = hamming(H[i], H[j])
        if d > sim_dist:
            continue
        ri, rj = rows[i], rows[j]
        why = set()
        if d <= dup_dist:
            why.add("copy")
        if ri["taken_at"] and rj["taken_at"] and abs(ri["taken_at"] - rj["taken_at"]) <= window:
            why.add("time")
        if None not in (ri["lat"], ri["lng"], rj["lat"], rj["lng"]) and _km(ri["lat"], ri["lng"], rj["lat"], rj["lng"]) * 1000 <= place_m:
            why.add("place")
        if "copy" in why or why & {"time", "place"}:
            links.append((i, j, why))
            parent[find(i)] = find(j)
    groups: dict[int, dict] = {}
    for i, j, why in links:
        g = groups.setdefault(find(i), {"members": set(), "why": set(), "all_copy": True})
        g["members"] |= {i, j}; g["why"] |= why - {"copy"}; g["all_copy"] &= "copy" in why
    out = []
    for g in groups.values():
        ms = sorted(g["members"], key=lambda i: (rows[i]["taken_at"] or 0, rows[i]["id"]))
        best = max(ms, key=lambda i: (rows[i]["score"] or 0, 0 if rows[i]["eyes_closed"] else 1, (rows[i]["width"] or 0) * (rows[i]["height"] or 0), rows[i]["bytes"] or 0))
        out.append({"kind": "copy" if g["all_copy"] else "similar", "why": sorted(g["why"]),
                    "best": rows[best]["id"], "taken_at": rows[ms[0]]["taken_at"],
                    "photos": [{"id": rows[i]["id"], "score": rows[i]["score"], "bytes": rows[i]["bytes"] or 0, "w": rows[i]["width"], "h": rows[i]["height"],
                                "eyes_closed": rows[i]["eyes_closed"], "taken_at": rows[i]["taken_at"], "filename": rows[i]["filename"]} for i in ms]})
    out.sort(key=lambda g: (g["kind"] != "copy", -len(g["photos"]), -(g["taken_at"] or 0)))
    return out


# ------------------------------------------------------------------ library cleanup report
def cleanup_report(con) -> dict:
    """Photos worth a look, one category each (screenshot > receipt/document > very dark > blurry)."""
    cats = {"screenshot": [], "receipt": [], "dark": [], "blurry": []}
    for r in con.execute("SELECT a.*, p.bytes, p.id pid FROM photo_analysis a JOIN photos p ON p.id=a.photo_id "
                         "WHERE p.trashed=0 AND p.is_video=0 ORDER BY p.taken_at DESC"):
        if r["is_screenshot"]:
            c = "screenshot"
        elif r["is_receipt"]:
            c = "receipt"
        elif r["mean"] < DARK_MEAN and r["p95"] < DARK_P95:
            c = "dark"
        elif r["sharp"] < BLUR_SHARP and r["phash"] is not None:
            c = "blurry"
        else:
            continue
        cats[c].append((r["pid"], r["bytes"] or 0))
    return {"analysed": con.execute("SELECT COUNT(*) FROM photo_analysis").fetchone()[0],
            "categories": {k: {"ids": [i for i, _ in v], "bytes": sum(b for _, b in v)} for k, v in cats.items()}}
