"""Issues #51 #59 #63 #65 #66 #67 #46 #55 (server side): pictures in problem reports, a series made by hand, the AI gets the EXIF and the
keywords of photos taken close in time (when picking and when tagging), the language is kept by the server, a person tagged by hand,
AI-suggested develop settings, and the changing wallpaper (settings, candidates and the copy it makes; the Windows call itself is not made here).
Throw-away profile, the real server, a fake AI provider and a fake GitHub.

    py -3.12 tools/test_issue_batch.py
"""
import base64
import io
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_batch_test_"))
for d in ("appdata", "local", "home"):
    (tmp / d).mkdir()
os.environ.update({"APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
                   "HOME": str(tmp / "home"), "PHOTAG_NO_OPEN": "1"})
sys.path.insert(0, str(ROOT))

import _client  # noqa: E402
from PIL import Image  # noqa: E402

from app import db, images, wallpaper  # noqa: E402
from app.config import PATHS  # noqa: E402

APP, AI, GH = 8890, 8891, 8892
res, SEEN, GHSEEN = [], [], []
GHMODE = {"put": 201}


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def _send(h, code, obj):
    out = json.dumps(obj).encode()
    h.send_response(code)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(out)))
    h.end_headers()
    h.wfile.write(out)


def _texts(body):
    return "\n".join(c.get("text", "") for c in body["messages"][0]["content"] if c["type"] == "text")


class FakeAI(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        SEEN.append((self.path, body))
        text = _texts(body)
        if "photo editor" in text:
            ans = '{"exposure": 9, "contrast": 5, "highlights": -30, "shadows": 20, "temperature": 7, "tint": 0, "vibrance": 10, "sharpness": 15, "reason": "Brighter"}'
        elif "choose the best photo" in text:
            ans = '{"best": 1, "reason": "Sharper"}'
        else:
            ans = '{"keywords": ["beach", "sea"]}'
        _send(self, 200, {"choices": [{"message": {"content": ans}}]})


class FakeGitHub(BaseHTTPRequestHandler):
    branch = False

    def log_message(self, *a):
        pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        GHSEEN.append(("GET", self.path, None))
        if self.path.endswith("/git/ref/heads/report-images"):
            return _send(self, 200 if FakeGitHub.branch else 404, {"object": {"sha": "abc"}})
        if self.path.endswith("/git/ref/heads/main"):
            return _send(self, 200, {"object": {"sha": "abc"}})
        _send(self, 200, {"default_branch": "main"})

    def do_POST(self):
        b = self._body()
        GHSEEN.append(("POST", self.path, b))
        if self.path.endswith("/git/refs"):
            FakeGitHub.branch = True
            return _send(self, 201, {})
        _send(self, 201, {"number": 77, "html_url": "https://github.com/x/y/issues/77"})

    def do_PUT(self):
        GHSEEN.append(("PUT", self.path, self._body()))
        _send(self, GHMODE["put"], {})


for port, handler in ((AI, FakeAI), (GH, FakeGitHub)):
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever, daemon=True).start()

db.init_db()
con = db.connect()
T0 = 1700000000


def photo(n, taken, **extra):
    sha = f"batch{n}"
    rel = f"2023/{sha}.jpg"
    f = PATHS.media / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 60), (40 * n, 90, 200 - 30 * n)).save(f, "JPEG")
    con.execute("INSERT INTO photos(sha256,filename,rel_path,mime,is_video,width,height,bytes,taken_at,camera_make,camera_model,exif_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (sha, sha + ".jpg", rel, "image/jpeg", 0, 80, 60, f.stat().st_size, taken, "Canon", "EOS R6",
                 json.dumps({"Exif": {"ISOSpeedRatings": 400, "FNumber": 2.8}})))
    pid = con.execute("SELECT id FROM photos WHERE sha256=?", (sha,)).fetchone()["id"]
    tp = images.thumb_path(sha)
    tp.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", (80, 60), (40 * n, 90, 200 - 30 * n)).save(buf, "JPEG")
    tp.write_bytes(buf.getvalue())
    return pid


def tag(pid, names):
    for n in names:
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (n,))
        tid = con.execute("SELECT id FROM tags WHERE name=?", (n,)).fetchone()["id"]
        con.execute("INSERT OR IGNORE INTO photo_tags(photo_id,tag_id,source) VALUES(?,?, 'user')", (pid, tid))


p1, p2, p3, p4 = photo(1, T0), photo(2, T0 + 600), photo(3, T0 + 1200), photo(4, T0 + 86400 * 30)
tag(p2, ["beach", "sunset"]); tag(p3, ["beach"]); tag(p4, ["snow"])
con.commit()

srv, cl = _client.start_server(APP, {**os.environ, "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999",
                                     "PHOTAG_AI_BASE_OPENAI": f"http://127.0.0.1:{AI}",
                                     "PHOTAG_REPORT_TOKEN": "tok", "PHOTAG_REPORT_API": f"http://127.0.0.1:{GH}"})
try:
    # ---- #66 the language is kept by the server (the window's own storage is lost when the port changes)
    check("no language saved yet", cl.get("/api/ui-lang").json() == {"lang": ""})
    cl.post("/api/ui-lang", json={"lang": "he"})
    check("the language is kept by the server", cl.get("/api/ui-lang").json() == {"lang": "he"})
    i18n = (ROOT / "app" / "ui" / "i18n.js").read_text("utf-8")
    check("the window asks the server when its own storage has none, and tells it when the user picks one", "'/api/ui-lang'" in i18n and "'POST', '/api/ui-lang'" in i18n)

    # ---- #59 a series by hand
    check("one photo is not a series", cl.post("/api/series", json={"ids": [p1]}).status_code == 400)
    r = cl.post("/api/series", json={"ids": [p1, p2]})
    sers = cl.get("/api/analysis/series").json()["series"]
    check("two photos made a series", r.status_code == 200 and any(set(s["members"]) == {p1, p2} for s in sers), sers)
    cl.post("/api/series", json={"ids": [p2, p3]})
    sers = cl.get("/api/analysis/series").json()["series"]
    check("a photo is in one series only: the new one took p2 from the first", any(set(s["members"]) == {p2, p3} for s in sers) and not any(p1 in s["members"] for s in sers), sers)
    cl.post("/api/series/remove", json={"ids": [p3]})
    check("removing a photo from a series of two ends it", cl.get("/api/analysis/series").json()["series"] == [])

    # ---- AI settings (the same provider as AI tagging)
    cl.post("/api/ai/settings", json={"provider": "openai", "model": "fake-model", "language": "en", "base_url": "", "api_key": "sk-test-key"})

    # ---- #63 picking: the AI gets the EXIF and the keywords of photos taken close in time
    r = cl.post("/api/ai/pick", json={"ids": [p1, p2]})
    text = _texts(SEEN[-1][1])
    check("pick works", r.status_code == 200 and r.json()["best_id"] == p1, r.text[:150])
    check("the AI got the capture time, camera and exposure of each photo", "Photo 1:" in text and "Taken: 2023-11-14" in text and "Canon EOS R6" in text and "ISO 400" in text, text[-400:])
    check("...and the keywords of the photos taken close in time (not of far away ones)", "Keywords of photos taken close in time: beach" in text and "snow" not in text, text[-300:])

    # ---- #65 tagging: the same
    r = cl.post("/api/aitag", json={"ids": [p1], "only_untagged": False})
    for _ in range(100):
        if cl.get("/api/job/aitag").json()["state"] in ("done", "error"):
            break
        time.sleep(0.3)
    tag_req = [b for _, b in SEEN if "tag photos" in _texts(b) or "You tag photos" in _texts(b)]
    check("tagging sends the photo's EXIF and the keywords of photos taken close in time", tag_req and "Keywords of photos taken close in time: beach" in _texts(tag_req[-1]) and "ISO 400" in _texts(tag_req[-1]),
          _texts(tag_req[-1])[-300:] if tag_req else "no request")
    names = [x["name"] for x in cl.get(f"/api/photo/{p1}").json()["tags"]]
    check("the photo got the AI's keywords", "beach" in names and "sea" in names, names)

    # ---- #46 AI-suggested develop settings: clamped, and nothing is saved
    r = cl.post(f"/api/photo/{p1}/ai-edit")
    j = r.json()
    check("the AI suggests settings, kept inside safe ranges", r.status_code == 200 and j["ops"]["exposure"] == 2.0 and j["ops"]["contrast"] == 1.4 and j["ops"]["highlights"] == -30 and j["reason"] == "Brighter", r.text[:200])
    check("nothing was saved (it is only a suggestion)", db.connect().execute("SELECT edited, edit_ops FROM photos WHERE id=?", (p1,)).fetchone()[:] == (0, None))

    # ---- #67 a person tagged by hand
    r = cl.post(f"/api/photo/{p1}/face", json={"x1": 0.25, "y1": 0.5, "x2": 0.5, "y2": 1.0, "name": "Dana"})
    fid = r.json().get("id")
    row = db.connect().execute("SELECT * FROM faces WHERE id=?", (fid,)).fetchone()
    check("the face is saved in the picture's pixels, with the person's name and no embedding",
          r.status_code == 200 and row and (row["x1"], row["y1"], row["x2"], row["y2"]) == (20.0, 30.0, 40.0, 60.0) and row["embedding"] is None, dict(row) if row else r.text)
    d = cl.get(f"/api/photo/{p1}").json()
    check("the person is listed on the photo and in People", [x["name"] for x in d["people"]] == ["Dana"] and any(x["name"] == "Dana" for x in cl.get("/api/people").json()))
    check("a too small rectangle is refused", cl.post(f"/api/photo/{p1}/face", json={"x1": .1, "y1": .1, "x2": .1, "y2": .1, "name": "X"}).status_code == 400)
    ce = db.connect()
    ce.execute("UPDATE photos SET edited=1, edit_ops=? WHERE id=?", (json.dumps({"rotate": 90}), p3))
    ce.commit(); ce.close()
    check("an edited photo is refused (its picture is not the original's)", cl.post(f"/api/photo/{p3}/face", json={"x1": .1, "y1": .1, "x2": .5, "y2": .5, "name": "X"}).status_code == 400)
    check("a hand-made face can be removed", cl.delete(f"/api/face/{fid}").status_code == 200
          and db.connect().execute("SELECT COUNT(*) FROM faces WHERE id=?", (fid,)).fetchone()[0] == 0)

    # ---- #55 the changing wallpaper
    w = cl.get("/api/wallpaper").json()
    check("on this system the wallpaper is not supported, and the settings say so", w["supported"] == (sys.platform == "win32") and w["enabled"] is False)
    c2 = db.connect()
    cfg = wallpaper.save(c2, {"enabled": True, "source": "all", "minutes": 5, "order": "newest"})
    ids = wallpaper.candidates(c2, cfg)
    check("settings are kept and 'newest' orders by date", cfg["enabled"] and cfg["minutes"] == 5 and ids[0] == p4, ids)
    c2.execute("INSERT OR IGNORE INTO albums(name,kind) VALUES('Trip','album')")
    aid = c2.execute("SELECT id FROM albums WHERE name='Trip'").fetchone()["id"]
    c2.execute("INSERT INTO photo_albums(photo_id,album_id) VALUES(?,?)", (p2, aid)); c2.commit()
    cfg = wallpaper.save(c2, {"source": "album", "album_id": aid})
    check("a collection as the source", wallpaper.candidates(c2, cfg) == [p2])
    check("favourites as the source", wallpaper.candidates(c2, wallpaper.save(c2, {"source": "favorites"})) == [])
    before = (PATHS.media / "2023/batch2.jpg").read_bytes()
    out = wallpaper.make_file(c2, p2, apply=False)
    im = Image.open(out)
    check("the wallpaper is a JPEG copy in photag's own folder, the photo itself is untouched",
          im.format == "JPEG" and out.parent == PATHS.root / "wallpaper" and (PATHS.media / "2023/batch2.jpg").read_bytes() == before)
    wallpaper.save(c2, {"minutes": 0}); check("the interval has a floor of one minute", wallpaper.get(c2)["minutes"] == 1)
    if sys.platform != "win32":
        check("changing it is refused where it cannot work", cl.post("/api/wallpaper", json={"enabled": True}).status_code == 400 and cl.post("/api/wallpaper/now").status_code == 400)
        check("tick does nothing where it cannot work", wallpaper.tick(c2) is None)

    # ---- #51 pictures in a report
    buf = io.BytesIO(); Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "JPEG")
    shot = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    r = cl.post("/api/report", json={"title": "It crashes", "description": "see the picture", "include_tech": False, "images": [shot, "data:image/png;base64,AAAA"]})
    j = r.json()
    puts = [g for g in GHSEEN if g[0] == "PUT"]
    issue = [g for g in GHSEEN if g[0] == "POST" and g[1].endswith("/issues")]
    check("the report was sent with one picture; the one that is not a JPEG was left out", r.status_code == 200 and j["sent"] and j["images"] == 1 and j["images_failed"] == 1, r.text[:200])
    check("the picture was stored on its own branch of the repository (created first)", len(puts) == 1 and "/contents/report-images/" in puts[0][1] and puts[0][2]["branch"] == "report-images"
          and any(g[0] == "POST" and g[1].endswith("/git/refs") and g[2]["ref"] == "refs/heads/report-images" for g in GHSEEN))
    check("the issue shows the picture", issue and "![picture 1](https://raw.githubusercontent.com/giamat13/photag/report-images/report-images/" in issue[-1][2]["body"], issue[-1][2]["body"][:300] if issue else "no issue")
    GHMODE["put"] = 403
    r = cl.post("/api/report", json={"title": "Again", "description": "x", "include_tech": False, "images": [shot]})
    j = r.json()
    check("when the picture cannot be stored the report is still sent, and the window is told", r.status_code == 200 and j["sent"] and j["images"] == 0 and j["images_failed"] == 1, r.text[:200])
finally:
    srv.terminate()
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
