"""Test: edits never rewrite the photo file (app/render.py), and photos edited by an older version are moved to the
new model without ever losing an original.

Phase 1 (no server, direct calls): render / migration edge cases.
Phase 2 (real server): the HTTP endpoints, the files on disk, exports, backups, deletion, concurrency.

    py -3.12 tools/test_nondestructive_edit.py
"""
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageStat

ROOT = Path(__file__).resolve().parent.parent
PORT = 8793
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_nondestructive_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h"), ("HOME", "h")):
    (tmp / v).mkdir(exist_ok=True)
    os.environ[k] = str(tmp / v)
os.environ.update(PYTHONIOENCODING="utf-8", PHOTAG_NO_OPEN="1", PHOTAG_BACKUP_START_DELAY="9999", PHOTAG_EXIF_DELAY="9999",
                  PHOTAG_MIGRATE_DELAY="9999", PHOTAG_MIGRATE_TICK="0.2", PHOTAG_MIGRATE_IDLE="1")
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import backup, config, db, images, importer, render  # noqa: E402
from app.config import PATHS  # noqa: E402

config.PATHS.root.mkdir(parents=True, exist_ok=True)
con = db.init_db()
work = tmp / "work"
work.mkdir()
sha = lambda b: hashlib.sha256(b).hexdigest()
mean = lambda data: ImageStat.Stat(Image.open(io.BytesIO(data)).convert("L")).mean[0]


def gradient(path: Path, w=200, h=100, shift=0):
    im = Image.linear_gradient("L").resize((w, h)).convert("RGB")
    if shift:
        im = Image.eval(im, lambda v: (v + shift) % 256)
    im.save(path, "PNG" if path.suffix == ".png" else "JPEG", quality=95)
    return path


def snapshot(path: Path):
    st = path.stat()
    return (sha(path.read_bytes()), st.st_size, st.st_mtime_ns, getattr(st, "st_ino", 0))


def photo_row(pid):
    return con.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()


def renders_of(pid):
    return sorted(p.name for p in PATHS.renders.glob(f"{pid}-*"))


def new_photo(name, shift=0, w=200, h=100, ext=".jpg"):
    src = gradient(work / f"{name}{ext}", w, h, shift)
    pid, _ = importer._ingest_file(con, src)
    con.commit()
    return pid


OPS = {"rotate": 90, "crop": [0, 0, 0.5, 1], "brightness": 1.2, "contrast": 1.1, "saturation": 0.8}


def make_legacy(name, ops=OPS, shift=0, neutral_state=False):
    """A photo exactly as the OLD _render left it: rel_path = the edited working file, orig_backup = the pristine copy."""
    orig_src = gradient(work / f"leg_{name}.jpg", shift=shift)
    pid, _ = importer._ingest_file(con, orig_src)
    row = photo_row(pid)
    wpath = PATHS.media / row["rel_path"]
    (PATHS.media / ".originals").mkdir(exist_ok=True)
    bpath = PATHS.media / ".originals" / f"{pid}_{row['filename']}"
    shutil.copy2(wpath, bpath)
    if neutral_state:
        cols = ("UPDATE photos SET orig_backup=?, edited=0, edit_ops=NULL WHERE id=?", (str(bpath.relative_to(PATHS.media)), pid))
    else:
        images.apply_edit(bpath, ops, wpath)
        w, h = images.dimensions(wpath)
        cols = ("UPDATE photos SET orig_backup=?, edited=1, edit_ops=?, sha256=?, bytes=?, width=?, height=? WHERE id=?",
                (str(bpath.relative_to(PATHS.media)), json.dumps(ops), images.sha256_file(wpath), wpath.stat().st_size, w, h, pid))
    con.execute(*cols)
    con.commit()
    images.thumb_path(row["sha256"]).unlink(missing_ok=True)
    return pid, orig_src.read_bytes(), wpath, bpath


# =====================================================================================  PHASE 1: migration
# ---- the normal case
pid, orig_bytes, wpath, bpath = make_legacy("normal")
edited_bytes = wpath.read_bytes()
linked = tmp / "older_backup_copy.jpg"
os.link(wpath, linked)                                      # a backup snapshot hard-links the old working file
old_sha = photo_row(pid)["sha256"]
check("(setup) the legacy working file is an edited picture, not the original", edited_bytes != orig_bytes)
ok = render.migrate_one(con, photo_row(pid))
r = photo_row(pid)
check("migrate_one: a legacy photo migrates", ok is True)
check("the library file is the pristine original again, byte for byte", wpath.read_bytes() == orig_bytes)
check("sha256 and bytes are the original's", r["sha256"] == sha(orig_bytes) and r["bytes"] == len(orig_bytes), r["sha256"][:12])
check("orig_backup is cleared and its file deleted", r["orig_backup"] is None and not bpath.exists())
check("the settings are kept and the photo is still marked edited", r["edited"] == 1 and json.loads(r["edit_ops"]) == OPS)
rp = renders_of(pid)
check("exactly one render exists", len(rp) == 1, rp)
check("width/height describe the LOOK (rotated + cropped), not the original",
      (r["width"], r["height"]) == images.dimensions(PATHS.renders / rp[0]) and (r["width"], r["height"]) != (200, 100), (r["width"], r["height"]))
check("the thumbnail of the new sha exists and the old one is gone", images.thumb_path(r["sha256"]).exists() and not images.thumb_path(old_sha).exists())
check("a backup that hard-linked the old working file still holds the edited bytes (replaced by a new inode, not overwritten in place)",
      linked.read_bytes() == edited_bytes)
check("migrating again is a harmless no-op", render.migrate_one(con, photo_row(pid)) is True and wpath.read_bytes() == orig_bytes)

# ---- legacy 'neutral' (the old code kept orig_backup after a revert)
pid_n, orig_n, wpath_n, bpath_n = make_legacy("neutral", neutral_state=True, shift=7)
check("migrate_one: a reverted legacy photo (orig_backup kept, edited=0)", render.migrate_one(con, photo_row(pid_n)) and photo_row(pid_n)["orig_backup"] is None)
rn = photo_row(pid_n)
check("...ends unedited with no render and the original's own size", rn["edited"] == 0 and rn["edit_ops"] is None and not renders_of(pid_n) and (rn["width"], rn["height"]) == (200, 100))

# ---- refusals leave everything exactly as it was
pid_m, orig_m, wpath_m, bpath_m = make_legacy("missing", shift=11)
bpath_m.unlink()
before = snapshot(wpath_m)
check("a missing backup file: migration refuses", render.migrate_one(con, photo_row(pid_m)) is False)
check("...and changes nothing (file and row)", snapshot(wpath_m) == before and photo_row(pid_m)["orig_backup"] is not None)

pid_e, orig_e, wpath_e, bpath_e = make_legacy("empty", shift=13)
bpath_e.write_bytes(b"")
before = snapshot(wpath_e)
check("an empty backup file: migration refuses", render.migrate_one(con, photo_row(pid_e)) is False and snapshot(wpath_e) == before)

pid_c, orig_c, wpath_c, bpath_c = make_legacy("clash", shift=17)
other = new_photo("holder_of_the_same_pixels", shift=17)           # the original's content is already another photo
other_sha = photo_row(other)["sha256"]
shutil.copy2(work / "leg_clash.jpg", PATHS.media / photo_row(other)["rel_path"])
con.execute("UPDATE photos SET sha256=? WHERE id=?", (sha(orig_c), other))
con.commit()
before = snapshot(wpath_c)
check("an original whose content already belongs to another photo: migration refuses (sha256 is unique)",
      render.migrate_one(con, photo_row(pid_c)) is False and snapshot(wpath_c) == before and bpath_c.exists())

pid_j, orig_j, wpath_j, bpath_j = make_legacy("badjson", shift=19)
con.execute("UPDATE photos SET edit_ops='{not json' WHERE id=?", (pid_j,))
con.commit()
check("damaged edit_ops JSON on a legacy photo: treated as 'no edit', original restored, nothing lost",
      render.migrate_one(con, photo_row(pid_j)) and (PATHS.media / photo_row(pid_j)["rel_path"]).read_bytes() == orig_j and photo_row(pid_j)["edited"] == 0)

# ---- a failure half-way is recoverable
pid_x, orig_x, wpath_x, bpath_x = make_legacy("interrupted", shift=23)
_real_dims = images.dimensions
calls = {"n": 0}
def _flaky(p):
    calls["n"] += 1
    raise OSError("disk went away")
images.dimensions = _flaky
try:
    ok = render.migrate_one(con, photo_row(pid_x))
finally:
    images.dimensions = _real_dims
check("a crash right after the file was swapped: reports failure", ok is False)
check("...the pristine backup is still there (deleted only after the catalog was updated)", bpath_x.exists() and photo_row(pid_x)["orig_backup"] is not None)
check("...and the working file was put back to the edited look the old model expects", (PATHS.media / photo_row(pid_x)["rel_path"]).read_bytes() != orig_x)
check("a later attempt finishes the job from that state", render.migrate_one(con, photo_row(pid_x)) is True
      and photo_row(pid_x)["orig_backup"] is None and (PATHS.media / photo_row(pid_x)["rel_path"]).read_bytes() == orig_x and not bpath_x.exists())

# ---- a busy catalog (another connection writing) is waited out, not treated as a refusal
class Flaky:
    def __init__(self, real, fail_times):
        self.real, self.left = real, fail_times
    def execute(self, sql, *a):
        if sql.lstrip().startswith("UPDATE photos SET sha256") and self.left > 0:
            self.left -= 1
            raise sqlite3.OperationalError("database is locked")
        return self.real.execute(sql, *a)
    def __getattr__(self, k):
        return getattr(self.real, k)

pid_f, orig_f, wpath_f, bpath_f = make_legacy("flaky", shift=27)
check("a catalog that is busy twice, then free: the migration waits and succeeds",
      render.migrate_one(Flaky(con, 2), photo_row(pid_f)) is True and (PATHS.media / photo_row(pid_f)["rel_path"]).read_bytes() == orig_f)
pid_g, orig_g, wpath_g, bpath_g = make_legacy("alwaysbusy", shift=29)
edited_g = wpath_g.read_bytes()
check("a catalog that stays busy: reported as failed...", render.migrate_one(Flaky(con, 99), photo_row(pid_g)) is False)
check("...with the working file back to the edited look, the pristine copy intact and the row untouched",
      wpath_g.read_bytes() == edited_g and bpath_g.read_bytes() == orig_g and photo_row(pid_g)["orig_backup"] is not None)
check("...and the reason is recorded", "busy" in render.REFUSED.get(pid_g, ""), render.REFUSED.get(pid_g))
check("(and a later attempt, once the catalog is free, simply works)", render.migrate_one(con, photo_row(pid_g)) is True)

# ---- batches
batch = [make_legacy(f"b{i}", shift=30 + i) for i in range(25)]
skip: set = set()
render._FAILS.clear()
ok1, bad1 = render.migrate_batch(con, skip, 20)
check("migrate_batch honours the limit", ok1 + bad1 == 20, (ok1, bad1))
totals = [(ok1, bad1)]
for _ in range(8):
    r_ = render.migrate_batch(con, skip, 20)
    totals.append(r_)
    if r_ == (0, 0):
        break
legacy_left = con.execute("SELECT COUNT(*) n FROM photos WHERE orig_backup IS NOT NULL").fetchone()["n"]
check("the refused ones (missing, empty, clash) are retried, and after three failures each they are skipped",
      len(skip) == 3 and legacy_left == 3 and all(render._FAILS[i] >= 3 for i in skip), (totals, len(skip), legacy_left))
check("once only skipped ones remain, a batch has nothing to do", render.migrate_batch(con, skip, 20) == (0, 0))
check("EVERY migrated original is byte-identical to what was imported (no photo lost or altered)",
      all((PATHS.media / photo_row(p)["rel_path"]).read_bytes() == ob for p, ob, _, _ in batch))
check("no .originals file is left for a migrated photo", not any((PATHS.media / ".originals").glob(f"{batch[0][0]}_*")))

# ---- render key
k1, k2 = render.render_key({"brightness": 1.2}, "aaa"), render.render_key({"brightness": 1.2}, "bbb")
check("the render key changes when the original changes", k1 != k2)
check("...and when the settings change", render.render_key({"brightness": 1.3}, "aaa") != k1)
check("...but not with the order the settings were written in", render.render_key({"a": 1, "b": 2}, "x") == render.render_key({"b": 2, "a": 1}, "x"))

# =====================================================================================  PHASE 2: real server
# rows for the server tests (made now, before it starts)
A = new_photo("a_photo", shift=140, w=200, h=100)
B = new_photo("b_png", shift=40, ext=".png")
C = new_photo("c_revert", shift=50)
D = new_photo("d_corrupt", shift=60)
V = new_photo("e_video", shift=70)
CON = new_photo("f_concurrent", shift=80)
EXP = new_photo("g_export", shift=90)
BK = new_photo("h_backup", shift=100)
con.execute("UPDATE photos SET is_video=1 WHERE id=?", (V,))
(PATHS.media / photo_row(D)["rel_path"]).write_bytes(b"this is not an image" * 100)       # cannot be rendered
raw_pid = new_photo("i_raw", shift=110)
shutil.move(PATHS.media / photo_row(raw_pid)["rel_path"], PATHS.media / (photo_row(raw_pid)["rel_path"] + ".cr2"))
con.execute("UPDATE photos SET rel_path=rel_path||'.cr2', filename=filename||'.cr2' WHERE id=?", (raw_pid,))
loop_ids = [make_legacy(f"loop{i}", shift=120 + i) for i in range(3)]                    # the server's own background loop migrates these
stuck_pid, stuck_orig, stuck_work, stuck_backup = make_legacy("stuck", shift=130)         # a legacy photo that can NOT migrate (clash)
twin = new_photo("stuck_twin", shift=130)
shutil.copy2(work / "leg_stuck.jpg", PATHS.media / photo_row(twin)["rel_path"])
con.execute("UPDATE photos SET sha256=? WHERE id=?", (sha(stuck_orig), twin))
con.commit()

orig_A = PATHS.media / photo_row(A)["rel_path"]
snapA = snapshot(orig_A)
hard = tmp / "hardlink_to_A.jpg"
os.link(orig_A, hard)
thumb_before = None

srv_env = os.environ.copy()
srv_env["PHOTAG_MIGRATE_DELAY"] = "3"
srv_log = open(tmp / "server.log", "w", encoding="utf-8")
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)],
                       env=srv_env, stdout=srv_log, stderr=subprocess.STDOUT, cwd=str(ROOT))


def call(method, path, body=None, raw=False):
    req = urllib.request.Request(APP + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
            return r.status, (data if raw else (json.loads(data) if data else {}))
    except urllib.error.HTTPError as e:
        return e.code, e.read()


try:
    for _ in range(80):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2).read()
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit("server did not start")

    _, orig_thumb = call("GET", f"/thumb/{A}", raw=True)
    original_mean = mean(orig_A.read_bytes())

    # ---- a plain edit
    code, _ = call("POST", f"/api/photo/{A}/edit", {"brightness": 1.5})
    rowA = photo_row(A)
    check("POST /edit succeeds", code == 200, code)
    check("the original file is untouched: same bytes, same size, same mtime, same inode", snapshot(orig_A) == snapA, (snapshot(orig_A)[1], snapA[1]))
    check("a hard link to it (like a backup snapshot's) still has the original content", hard.read_bytes() == orig_A.read_bytes())
    check("sha256 / bytes in the catalog are still the original's", rowA["sha256"] == snapA[0] and rowA["bytes"] == snapA[1])
    check("the photo is marked edited and the settings stored", rowA["edited"] == 1 and json.loads(rowA["edit_ops"]) == {"brightness": 1.5})
    check("exactly one render, in the renders folder", len(renders_of(A)) == 1, renders_of(A))
    check("no pristine copy was made in .originals (nothing extra to store or back up)",
          not (PATHS.media / ".originals").exists() or not any(p.name.startswith(f"{A}_") for p in (PATHS.media / ".originals").iterdir()))
    _, media = call("GET", f"/media/{A}", raw=True)
    check("/media serves the EDITED look (brighter than the original)", mean(media) > original_mean + 10, (round(mean(media), 1), round(original_mean, 1)))
    _, orig_http = call("GET", f"/original/{A}", raw=True)
    check("/original serves the untouched original", orig_http == orig_A.read_bytes())
    _, thumb_after = call("GET", f"/thumb/{A}", raw=True)
    check("the thumbnail was rebuilt from the edited look", mean(thumb_after) > mean(orig_thumb) + 10, (round(mean(thumb_after), 1), round(mean(orig_thumb), 1)))

    # ---- composition: all settings at once equal a single render from the original
    code, _ = call("POST", f"/api/photo/{A}/edit", OPS)
    _, media = call("GET", f"/media/{A}", raw=True)
    ref = work / "reference.jpg"
    images.apply_edit(orig_A, OPS, ref)
    check("rotate+crop+brightness+contrast+saturation = one render straight from the original (same pixels)",
          Image.open(io.BytesIO(media)).tobytes() == Image.open(ref).tobytes())
    rowA = photo_row(A)
    check("width/height are the look's size (rotated 90 then cropped to half the width)", (rowA["width"], rowA["height"]) == (100, 100) or (rowA["width"], rowA["height"]) == Image.open(ref).size, (rowA["width"], rowA["height"]))
    check("only the newest render is kept", len(renders_of(A)) == 1)
    check("the original is STILL untouched after the second edit", snapshot(orig_A) == snapA)

    rp = PATHS.renders / renders_of(A)[0]
    t0 = rp.stat().st_mtime_ns
    call("POST", f"/api/photo/{A}/edit", OPS)
    check("sending the same settings again does not re-render", rp.exists() and rp.stat().st_mtime_ns == t0)

    # ---- the rotate buttons compose with the settings
    call("POST", f"/api/photo/{A}/rotate", {"degrees": 90})
    ops_now = json.loads(photo_row(A)["edit_ops"])
    check("the rotate button folds into the settings (90 + 90 = 180) and the crop turns with the photo", abs(abs(ops_now["rotate"]) - 180) < 1e-6 and ops_now.get("crop") != OPS["crop"], ops_now)
    check("...still without touching the original", snapshot(orig_A) == snapA)

    # ---- a missing render is rebuilt on demand
    for p in PATHS.renders.glob(f"{A}-*"):
        p.unlink()
    code, again = call("GET", f"/media/{A}", raw=True)
    check("a deleted render is rebuilt the next time the photo is looked at", code == 200 and len(renders_of(A)) == 1 and mean(again) > 0, code)

    # ---- neutral settings and revert
    code, _ = call("POST", f"/api/photo/{A}/edit", {"brightness": 1.0, "contrast": 1.0, "saturation": 1.0})
    rowA = photo_row(A)
    check("all settings at zero: no longer edited, no render left, original size restored",
          rowA["edited"] == 0 and rowA["edit_ops"] is None and not renders_of(A) and (rowA["width"], rowA["height"]) == (200, 100))
    _, media = call("GET", f"/media/{A}", raw=True)
    check("...and /media serves the original again", media == orig_A.read_bytes())

    call("POST", f"/api/photo/{C}/edit", {"contrast": 1.8, "grayscale": True})
    orig_C = PATHS.media / photo_row(C)["rel_path"]
    check("(setup) photo C is edited", photo_row(C)["edited"] == 1 and renders_of(C))
    code, _ = call("POST", f"/api/photo/{C}/revert")
    rc = photo_row(C)
    check("revert: unedited, settings cleared, render deleted", code == 200 and rc["edited"] == 0 and rc["edit_ops"] is None and not renders_of(C))
    code, _ = call("POST", f"/api/photo/{C}/revert")
    check("reverting a photo that is not edited is refused", code == 400, code)

    # ---- PNG originals keep PNG
    code, _ = call("POST", f"/api/photo/{B}/edit", {"brightness": 1.3})
    check("a PNG original is edited to a PNG render", code == 200 and renders_of(B) and renders_of(B)[0].endswith(".png"), renders_of(B))

    # ---- failures change nothing
    corrupt = PATHS.media / photo_row(D)["rel_path"]
    before = (snapshot(corrupt), dict(photo_row(D)))
    code, _ = call("POST", f"/api/photo/{D}/edit", {"brightness": 1.4})
    after = photo_row(D)
    check("an original that cannot be read: the edit fails", code >= 400, code)
    check("...and nothing changed: row, file, no render, no half-written .part file",
          after["edited"] == 0 and after["edit_ops"] is None and snapshot(corrupt) == before[0] and not renders_of(D) and not list(PATHS.renders.glob("*.part*")))
    code, _ = call("POST", f"/api/photo/{V}/edit", {"brightness": 1.4})
    check("a video cannot be edited", code == 400, code)
    code, _ = call("POST", f"/api/photo/{raw_pid}/edit", {"brightness": 1.4})
    check("a RAW file cannot be edited", code == 400, code)
    code, _ = call("POST", "/api/photo/999999/edit", {"brightness": 1.4})
    check("an unknown photo is refused", code >= 400, code)

    # ---- exports
    call("POST", f"/api/photo/{EXP}/edit", {"brightness": 1.6})
    exp_orig = PATHS.media / photo_row(EXP)["rel_path"]
    class P:
        done = total = 0; cancel = False; state = None; error = None; extra = {}
        def say(self, *a, **k): pass
        def say_parts(self, *a, **k): pass
        def fail(self, msg, **v): self.error = msg.format(**v)
    d1, d2, d3 = tmp / "ex_orig", tmp / "ex_look", tmp / "ex_small"
    importer.run_export([EXP], str(d1), True, None, 100, False, False, P())
    importer.run_export([EXP], str(d2), False, None, 100, False, False, P())
    importer.run_export([EXP], str(d3), False, 64, 80, False, False, P())
    f1, f2, f3 = (next(d.iterdir()) for d in (d1, d2, d3))
    check("export 'originals': the untouched original", f1.read_bytes() == exp_orig.read_bytes())
    check("export of the photo as it looks: the edited picture", f2.read_bytes() != exp_orig.read_bytes() and mean(f2.read_bytes()) > mean(exp_orig.read_bytes()) + 10)
    check("a resized export is made from the edited look too", mean(f3.read_bytes()) > mean(exp_orig.read_bytes()) + 5 and max(Image.open(f3).size) <= 64)

    # ---- backups: an edit does not change a backed-up file
    bk_orig = PATHS.media / photo_row(BK)["rel_path"]
    backup.set_settings({"folder": str(tmp / "bk"), "include_media": True, "keep": 10})
    s1 = backup.create_snapshot("manual")
    call("POST", f"/api/photo/{BK}/edit", {"brightness": 1.7})
    s2 = backup.create_snapshot("manual")
    check("after an edit the next backup copies NO photo file again (before, an edit changed the file and it was copied)",
          s2["media"]["copied"] == 0 and s2["media"]["linked"] == s2["media"]["files"], s2["media"])
    snap_file = next((tmp / "bk" / s1["media_dir"]).rglob(bk_orig.name))
    check("the first backup's copy of the photo is still the original", snap_file.read_bytes() == bk_orig.read_bytes())
    check("renders are not part of a backup", not any(p.suffix == ".jpg" and p.name.startswith(f"{BK}-") for p in (tmp / "bk").rglob("*")))

    # ---- concurrency: many identical requests, then a race of different ones
    results = []
    def hit(ops):
        results.append(call("POST", f"/api/photo/{CON}/edit", ops)[0])
    ts = [threading.Thread(target=hit, args=({"brightness": 1.4},)) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    check("8 simultaneous identical edits all succeed", results.count(200) == 8, results)
    check("...leaving exactly one valid render", len(renders_of(CON)) == 1 and Image.open(PATHS.renders / renders_of(CON)[0]).size == (200, 100))
    results.clear()
    variants = [{"brightness": 1.0 + i / 10} for i in range(2, 8)]
    ts = [threading.Thread(target=hit, args=(v,)) for v in variants]
    [t.start() for t in ts]; [t.join() for t in ts]
    rowc = photo_row(CON)
    final_ops = json.loads(rowc["edit_ops"])
    check("a race of different edits: all succeed and the stored settings are one of them", results.count(200) == len(variants) and final_ops in variants, (results, final_ops))
    orig_con = PATHS.media / photo_row(CON)["rel_path"]
    check("...and the render on disk belongs to the stored settings (not a mix)",
          renders_of(CON) == [f"{CON}-{render.render_key(final_ops, rowc['sha256'])}.jpg"], renders_of(CON))

    # ---- deleting for good removes the render too
    call("POST", f"/api/photo/{EXP}/edit", {"brightness": 1.2})
    con.execute("UPDATE photos SET trashed=1 WHERE id=?", (EXP,))
    con.commit()
    importer.delete_forever(con, con.execute("SELECT id, sha256, rel_path, orig_backup FROM photos WHERE id=?", (EXP,)).fetchall())
    check("deleting a photo for good removes its render as well as the file", not renders_of(EXP) and not exp_orig.exists())

    # ---- stale renders can never be shown
    sA = photo_row(CON)
    con.execute("UPDATE photos SET sha256='changed-original' WHERE id=?", (CON,))
    con.commit()
    p_new = render.current_path(photo_row(CON))
    check("when the original's sha changes, the old render is not reused (a new one is made, the stale one removed)",
          p_new.name != f"{CON}-{render.render_key(final_ops, sA['sha256'])}.jpg" and len(renders_of(CON)) == 1, renders_of(CON))

    # ---- legacy photos that cannot migrate keep working the old way
    code, _ = call("POST", f"/api/photo/{stuck_pid}/edit", {"brightness": 1.5})
    rs = photo_row(stuck_pid)
    check("a legacy photo that cannot migrate (clash) is still editable, the old way", code == 200 and rs["orig_backup"] is not None and rs["edited"] == 1)
    check("...from its pristine copy (which is untouched)", stuck_backup.read_bytes() == stuck_orig and stuck_work.read_bytes() != stuck_orig)
    code, body = call("POST", f"/api/photo/{stuck_pid}/revert")
    check("reverting it is refused with a clear message (the original already exists as another photo) -- not a crash, and nothing is changed",
          code == 409 and b"already in the catalog" in body and stuck_backup.read_bytes() == stuck_orig and stuck_work.read_bytes() != stuck_orig, (code, body[:80]))
    con.execute("UPDATE photos SET sha256='released' WHERE id=?", (twin,))
    con.commit()
    code, _ = call("POST", f"/api/photo/{stuck_pid}/revert")
    check("once the other copy is gone, the same revert works and restores the original", code == 200 and stuck_work.read_bytes() == stuck_orig and photo_row(stuck_pid)["edited"] == 0)

    # ---- the server's own background loop migrates the rest
    deadline, left = time.time() + 60, None
    while time.time() < deadline:
        c2 = db.connect()
        left = c2.execute(f"SELECT COUNT(*) n FROM photos WHERE orig_backup IS NOT NULL AND id IN ({','.join(str(i[0]) for i in loop_ids)})").fetchone()["n"]
        c2.close()
        if left == 0:
            break
        time.sleep(0.5)
    check("the background loop migrated legacy photos by itself", left == 0, f"{left} left")
    if left:
        srv_log.flush()
        print("---- server log (tail) ----\n" + (tmp / "server.log").read_text("utf-8", "replace")[-3000:])
    check("...each one back to its exact original", all((PATHS.media / photo_row(p)["rel_path"]).read_bytes() == ob for p, ob, _, _ in loop_ids))
    code, body = call("GET", f"/media/{loop_ids[0][0]}", raw=True)
    check("a migrated photo is served as its edited look", code == 200 and body != loop_ids[0][1])
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
