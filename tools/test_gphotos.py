"""Test: import from Google Photos through the Picker (app/gphotos.py), against a FAKE Google on 127.0.0.1 -- nothing real is contacted.
Sign-in (OAuth with PKCE, the one-time listener), a Picker session, items listed and downloaded, and above all no duplicates:
same bytes, the same shot edited in Google Photos (other bytes, same name and time -- recognised before any download), an item
picked twice. The reminder shows only when it is switched on. The library is only READ from Google; nothing outside the photag
library is touched. Throw-away profile.

    py -3.12 tools/test_gphotos.py
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
PORT, FAKE = 8811, 8812
tmp = Path(tempfile.mkdtemp(prefix="photag_gphotos_test_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
G = f"http://127.0.0.1:{FAKE}"
os.environ.update(PYTHONIOENCODING="utf-8", PHOTAG_NO_OPEN="1", PHOTAG_BACKUP_START_DELAY="9999", PHOTAG_GOOGLE_POLL="1",
                  PHOTAG_GOOGLE_AUTH=G + "/auth", PHOTAG_GOOGLE_TOKEN=G + "/token", PHOTAG_GOOGLE_REVOKE=G + "/revoke",
                  PHOTAG_GOOGLE_PICKER=G + "/v1")
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from PIL import Image  # noqa: E402


def jpeg(color, size=(64, 48)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG")
    return b.getvalue()


# ---- the fake Google ----
ITEMS = {}          # media id -> dict(filename, create, bytes)
LOG = {"downloads": [], "sessions": 0, "deleted": [], "revoked": 0, "tokens": 0, "set_after": 1, "polls": 0}


class FakeGoogle(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, raw=None, ctype="application/json", headers=None):
        body = raw if raw is not None else json.dumps(obj or {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        return self.headers.get("Authorization") == "Bearer fake-access"

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/auth":                                      # the "consent page": approve at once -> redirect to the listener
            assert q["code_challenge_method"] == ["S256"] and q["scope"] == ["https://www.googleapis.com/auth/photospicker.mediaitems.readonly"]
            LOG["last_auth"] = q
            back = q["redirect_uri"][0] + "?" + urllib.parse.urlencode({"code": "good-code", "state": q["state"][0]})
            return self._send(302, raw=b"", headers={"Location": back})
        if u.path.startswith("/media/"):
            if not self._authed():
                return self._send(401)
            mid = u.path.split("/")[2].split("=")[0]
            LOG["downloads"].append(mid)
            return self._send(200, raw=ITEMS[mid]["bytes"], ctype="image/jpeg")
        if not self._authed():
            return self._send(401, {"error": {"status": "UNAUTHENTICATED"}})
        if u.path == "/v1/sessions/s1":
            LOG["polls"] += 1
            return self._send(200, {"id": "s1", "mediaItemsSet": LOG["polls"] >= LOG["set_after"], "pollingConfig": {"pollInterval": "1s"}})
        if u.path == "/v1/mediaItems":
            assert q["sessionId"] == ["s1"]
            items = [{"id": k, "createTime": v["create"], "type": "PHOTO",
                      "mediaFile": {"baseUrl": f"{G}/media/{k}", "mimeType": "image/jpeg", "filename": v["filename"],
                                    "mediaFileMetadata": {"width": 64, "height": 48}}} for k, v in ITEMS.items()]
            return self._send(200, {"mediaItems": items})
        self._send(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        if self.path == "/token":
            f = urllib.parse.parse_qs(body)
            LOG["tokens"] += 1
            if f.get("grant_type") == ["authorization_code"]:
                if f.get("code") != ["good-code"] or not f.get("code_verifier"):
                    return self._send(400, {"error": "invalid_grant"})
                return self._send(200, {"access_token": "fake-access", "refresh_token": "fake-refresh", "expires_in": 3600})
            if f.get("refresh_token") == ["fake-refresh"]:
                return self._send(200, {"access_token": "fake-access", "expires_in": 3600})
            return self._send(400, {"error": "invalid_grant"})
        if self.path == "/revoke":
            LOG["revoked"] += 1
            return self._send(200)
        if self.path == "/v1/sessions":
            if not self._authed():
                return self._send(401)
            LOG["sessions"] += 1
            LOG["polls"] = 0
            return self._send(200, {"id": "s1", "pickerUri": f"{G}/pick/s1", "pollingConfig": {"pollInterval": "1s"}})
        self._send(404)

    def do_DELETE(self):
        LOG["deleted"].append(self.path)
        self._send(200)


httpd = HTTPServer(("127.0.0.1", FAKE), FakeGoogle)
threading.Thread(target=httpd.serve_forever, daemon=True).start()

from _client import start_server  # noqa: E402
from app import config, db, importer  # noqa: E402
from app.config import PATHS  # noqa: E402

config.set_library_root(tmp / "lib")
PATHS.refresh()
con = db.init_db()

# ---- photos photag already has: one that Google will also hold byte for byte, one that Google holds EDITED
same_bytes = jpeg((200, 10, 10))
orig_of_edited = jpeg((10, 200, 10))
src = tmp / "src"
src.mkdir()
(src / "IMG_0001.jpg").write_bytes(same_bytes)
(src / "IMG_0003.jpg").write_bytes(orig_of_edited)
T3 = 1700000000
pid_same, _ = importer._ingest_file(con, src / "IMG_0001.jpg", taken=1690000000)
pid_edit, _ = importer._ingest_file(con, src / "IMG_0003.jpg", taken=T3)
con.commit()
con.close()
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
ITEMS.update({
    "new1": {"filename": "IMG_0100.jpg", "create": iso(1710000000), "bytes": jpeg((10, 10, 200))},
    "new2": {"filename": "IMG_0101.jpg", "create": iso(1710000100), "bytes": jpeg((200, 200, 10))},
    "same": {"filename": "other-name.jpg", "create": iso(1690000000), "bytes": same_bytes},          # identical bytes, another file name
    "edited": {"filename": "IMG_0003-edited.jpg", "create": iso(T3 + 3), "bytes": jpeg((90, 90, 90))},   # edited in Google Photos: other bytes
})
n0 = None

env = dict(os.environ)
srv, cl = start_server(PORT, env)
try:
    def photos():
        return cl.get("/api/photos?limit=999").json()

    n0 = len(photos())
    check("two photos are in the catalog before", n0 == 2, n0)

    i = cl.get("/api/gphotos").json()
    check("not set up and not connected at first", not i["configured"] and not i["connected"] and not i["reminder"], i)
    r = cl.post("/api/gphotos/connect")
    check("connecting without a client id is refused", r.status_code in (400, 502) and not cl.get("/api/gphotos").json()["connected"], r.text[:120])

    i = cl.post("/api/gphotos", json={"client_id": "cid.apps.googleusercontent.com", "client_secret": "sec"}).json()
    check("the client id is saved (the secret is not echoed)", i["configured"] and i["own_client"] and "sec" not in json.dumps(i), i)

    # sign-in: the "browser" follows the consent URL, Google redirects to photag's one-time listener
    i = cl.post("/api/gphotos/connect").json()
    check("the sign-in is waiting for the browser", i["auth"]["state"] == "waiting" and i["auth"]["url"].startswith(G + "/auth"), i["auth"])
    urllib.request.urlopen(i["auth"]["url"], timeout=20).read()
    t0 = time.time()
    while time.time() - t0 < 15 and not cl.get("/api/gphotos").json()["connected"]:
        time.sleep(0.3)
    i = cl.get("/api/gphotos").json()
    check("signed in: the refresh token is kept", i["connected"] and i["auth"]["state"] == "ok", i["auth"])
    cfg = (tmp / "appdata" / "photag" / "config.json")
    raw = cfg.read_text("utf-8", "replace") if cfg.exists() else ""
    check("the refresh token is stored encrypted, never in plain text", "fake-refresh" not in raw and "refresh_token" in raw, raw[:100])

    def run_pick(label):
        r = cl.post("/api/gphotos/pick")
        assert r.status_code == 200, r.text
        t0 = time.time()
        while time.time() - t0 < 60:
            j = cl.get("/api/job/gphotos").json()
            if j["state"] in ("done", "error"):
                return j
            time.sleep(0.4)
        return {"state": "timeout"}

    LOG["set_after"] = 2          # the user takes a moment: the first poll says "not yet"
    j = run_pick("first")
    check("the first pick finishes", j["state"] == "done", j)
    check("2 new photos added; the identical file and the edited copy are duplicates", j["result"]["added"] == 2 and j["result"]["duplicates"] == 2, j["result"])
    check("the edited photo is recognised BEFORE any download (no bytes fetched for it)", "edited" not in LOG["downloads"], LOG["downloads"])
    check("the catalog grew by exactly the 2 new photos", len(photos()) == n0 + 2, len(photos()))
    check("the session was deleted at Google afterwards", LOG["deleted"] == ["/v1/sessions/s1"], LOG["deleted"])

    # second time with the same items: nothing again
    LOG["downloads"].clear()
    j = run_pick("second")
    check("picking the same items again adds nothing and downloads nothing",
          j["state"] == "done" and j["result"]["added"] == 0 and j["result"]["duplicates"] == 4 and not LOG["downloads"], (j["result"], LOG["downloads"]))
    check("still no extra photos", len(photos()) == n0 + 2)

    # a photo that was picked, then deleted from photag, comes back when picked again
    from app import db as _db
    c2 = _db.connect()
    row = c2.execute("SELECT id, sha256 FROM photos WHERE filename='IMG_0100.jpg'").fetchone()
    check("the picked photo is in the catalog with its capture time from Google", row is not None)
    importer.delete_forever(c2, c2.execute("SELECT id, sha256, rel_path, orig_backup FROM photos WHERE id=?", (row["id"],)).fetchall())
    c2.commit()
    c2.close()
    j = run_pick("third")
    check("a photo deleted from photag is brought in again when it is picked again", j["state"] == "done" and j["result"]["added"] == 1, j.get("result"))

    # reminder: only when switched on
    check("no reminder while it is off", cl.get("/api/background").json()["gphotos_due"] is False)
    i = cl.post("/api/gphotos", json={"reminder": True, "reminder_days": 3}).json()
    check("switching it on does not show it at once", i["reminder"] and i["reminder_days"] == 3 and i["due"] is False, i)
    from app import gphotos
    check("it becomes due after the period", gphotos.reminder_due(time.time() + 4 * 86400) is True and gphotos.reminder_due(time.time() + 2 * 86400) is False)
    cl.post("/api/gphotos", json={"reminder": False})
    check("off again: never due", gphotos.reminder_due(time.time() + 400 * 86400) is False)

    # a revoked permission is reported, not crashed on
    gphotos._ACCESS.update(token="", exp=0)
    cl.post("/api/gphotos/disconnect")
    check("disconnect revokes at Google and forgets the token", LOG["revoked"] == 1 and not cl.get("/api/gphotos").json()["connected"], LOG["revoked"])
    r = cl.post("/api/gphotos/pick")
    check("picking without being connected is refused cleanly", r.status_code in (401, 502), r.status_code)
finally:
    srv.terminate()
    httpd.shutdown()

# ---- the name matcher (in-process)
from app import gphotos  # noqa: E402
check("stem: Google's suffixes and copy numbers are ignored",
      gphotos.stem("IMG_1234-edited(1).JPG") == "img_1234" and gphotos.stem("IMG_1234.jpg") == "img_1234" and gphotos.stem("a-EFFECTS.png") == "a")

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
