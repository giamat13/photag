"""Import from Google Photos through Google's official Picker API.

Google closed the old Library API to whole-library reads (March 2025); what is left is the Picker: the user chooses photos in
Google's own window and the app receives exactly those. So this is "import what I pick", never a background read of the library.

Flow: connect once (OAuth in the system browser, the refresh token is kept encrypted by keystore) -> `start_pick()` makes a
Picker session and opens it -> the user marks photos and presses Done -> `run_import()` (a job) lists the chosen items, skips
the ones photag already has, downloads the rest and files them like any other import.

The user's Google Photos library is only READ. Everything that comes in is a new copy in the photag library.

Duplicates (a photo must never be stored twice):
  1. an item picked before (its Google id is remembered in `gphotos_items`) while its photo is still in the catalog;
  2. the same file content (SHA-256, done by importer._ingest_file after the download);
  3. the same photo under another file: Google serves the EDITED version of an edited photo, so its bytes differ from the
     original that was imported earlier (from Takeout, a folder, ...). `find_known()` recognises it by file name + capture time /
     size, before anything is downloaded.
"""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config, db, importer, keystore
from .config import PATHS
from .net import ssl_context

SCOPE = "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
PICKER_URL = "https://photospicker.googleapis.com/v1"


def _url(env: str, default: str) -> str:
    return os.environ.get(env) or default       # the tests point these at a fake Google on 127.0.0.1


class GError(Exception):
    """A Google call that failed. `key` is an English message for the UI (translated by t())."""

    def __init__(self, key: str, status: int = 0, **vars):
        super().__init__(key.format(**vars))
        self.key, self.status, self.vars = key, status, vars


# ---------- settings ----------
def _cfg() -> dict:
    return dict(config.get_gphotos())


def _save(**kw) -> None:
    s = _cfg()
    for k, v in kw.items():
        if v is None:
            s.pop(k, None)
        else:
            s[k] = v
    config.set_gphotos(s)


def client_credentials() -> tuple[str, str]:
    """(client_id, client_secret): the ones typed in Preferences, else the ones the release build carries. ('', '') = not set up."""
    s = _cfg()
    cid, sec = s.get("client_id") or "", ""
    if s.get("client_secret"):
        try:
            sec = keystore.unprotect(s["client_secret"])
        except Exception:
            sec = ""
    if not cid:
        try:
            from . import _google_client                    # written by the release workflow, not in the repository
            cid, sec = str(_google_client.CLIENT_ID or ""), str(_google_client.CLIENT_SECRET or "")
        except Exception:
            pass
    return cid, sec


def configured() -> bool:
    return bool(client_credentials()[0])


def connected() -> bool:
    return bool(_cfg().get("refresh_token"))


def reminder() -> dict:
    s = _cfg()
    return {"enabled": bool(s.get("reminder")), "days": max(1, min(365, int(s.get("reminder_days") or 7)))}


def info() -> dict:
    s = _cfg()
    r = reminder()
    return {"configured": configured(), "own_client": bool(s.get("client_id")), "connected": connected(),
            "reminder": r["enabled"], "reminder_days": r["days"], "last_import": s.get("last_import"),
            "due": reminder_due(), "auth": dict(AUTH), "pick": dict(PICK)}


def configure(client_id=None, client_secret=None, reminder_on=None, reminder_days=None) -> None:
    kw = {}
    if client_id is not None:
        kw["client_id"] = client_id.strip() or None
        if (client_id.strip() or None) != _cfg().get("client_id"):
            kw.update(refresh_token=None)                    # another app registration: the old permission is not valid for it
    if client_secret is not None:
        kw["client_secret"] = keystore.protect(client_secret.strip()) if client_secret.strip() else None
    if reminder_on is not None:
        kw["reminder"] = bool(reminder_on)
        if reminder_on:
            kw["last_reminder"] = int(time.time())          # the first reminder comes one period from now, not at once
    if reminder_days is not None:
        kw["reminder_days"] = max(1, min(365, int(reminder_days)))
    _save(**kw)


def reminder_due(now: float | None = None) -> bool:
    """The reminder is shown only when it is switched on AND the connection can work AND a whole period passed since the last
    import / reminder / switching it on."""
    r = reminder()
    if not r["enabled"] or not configured():
        return False
    s = _cfg()
    last = max(int(s.get("last_import") or 0), int(s.get("last_reminder") or 0))
    return (now or time.time()) - last >= r["days"] * 86400


def snooze() -> None:
    _save(last_reminder=int(time.time()))


# ---------- HTTP ----------
def _http(method: str, url: str, *, data=None, form=None, token: str | None = None, timeout: float = 30) -> dict:
    headers = {"User-Agent": "photag"}
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context() if url.startswith("https") else None) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            detail = {}
        err = detail.get("error")
        code = err if isinstance(err, str) else (err or {}).get("status") or ""
        if e.code in (401, 403) or code == "invalid_grant":
            raise GError("Google refused the access. Connect again in Preferences.", e.code)
        raise GError("Google answered with an error ({status}).", e.code, status=e.code)
    except (urllib.error.URLError, OSError, TimeoutError):
        raise GError("Could not reach Google. Check the internet connection.")
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        return {}


# ---------- sign-in (OAuth for an installed app: PKCE + a one-time listener on 127.0.0.1) ----------
AUTH = {"state": "idle", "url": "", "error": None}      # idle | waiting | ok | error  (for the UI)
_ACCESS = {"token": "", "exp": 0.0}


def _rand(n: int) -> str:
    return base64.urlsafe_b64encode(os.urandom(n)).rstrip(b"=").decode()


def _pkce() -> tuple[str, str]:
    import hashlib
    verifier = _rand(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


_PAGE = "<html><meta charset='utf-8'><body style='font:18px sans-serif;text-align:center;margin-top:20vh'>{}</body></html>"


def start_auth(open_browser=None) -> dict:
    """Start the sign-in: a one-time listener on 127.0.0.1 catches Google's redirect; the system browser shows Google's consent page.
    (A bare socket, not http.server: no new library for the packaged program -- see tools/test_runtime_lock.py.)"""
    import threading
    sk = __import__("socket")
    cid, secret = client_credentials()
    if not cid:
        raise GError("Google Photos is not set up in this program yet.")
    if AUTH["state"] == "waiting":
        return dict(AUTH)
    verifier, challenge = _pkce()
    state = _rand(16)
    srv = sk.socket(sk.AF_INET, sk.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    srv.settimeout(1)
    redirect = f"http://127.0.0.1:{srv.getsockname()[1]}"

    def finish(q: dict) -> bool:
        try:
            if "error" in q or q.get("state", [""])[0] != state or not q.get("code"):
                raise GError("Google refused the access. Connect again in Preferences.")
            tok = _http("POST", _url("PHOTAG_GOOGLE_TOKEN", TOKEN_URL), form={
                "code": q["code"][0], "client_id": cid, "client_secret": secret, "redirect_uri": redirect,
                "grant_type": "authorization_code", "code_verifier": verifier})
            if not tok.get("refresh_token"):
                raise GError("Google refused the access. Connect again in Preferences.")
            _save(refresh_token=keystore.protect(tok["refresh_token"]))
            _ACCESS.update(token=tok.get("access_token", ""), exp=time.time() + int(tok.get("expires_in", 0)) - 60)
            AUTH.update(state="ok", error=None)
            return True
        except GError as e:
            AUTH.update(state="error", error=e.key)
        except Exception as e:
            AUTH.update(state="error", error=str(e)[:200])
        return False

    url = _url("PHOTAG_GOOGLE_AUTH", AUTH_URL) + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})
    AUTH.update(state="waiting", url=url, error=None)

    def serve():
        t_end = time.time() + 600                             # ten minutes to finish in the browser
        done = False
        try:
            while not done and time.time() < t_end:
                try:
                    conn, _ = srv.accept()
                except OSError:                              # timeout: look at the clock again
                    continue
                try:
                    conn.settimeout(5)
                    head = conn.recv(8192).decode("latin-1", "replace")
                    target = head.split(" ", 2)[1] if head.startswith("GET ") and head.count(" ") >= 2 else ""
                    q = urllib.parse.parse_qs(urllib.parse.urlsplit(target).query)
                    if "code" in q or "error" in q:
                        ok = finish(q)
                        body = _PAGE.format("photag is connected to Google Photos. You can close this tab." if ok else
                                            "Connection failed. You can close this tab and try again.").encode()
                        done = True
                    else:                                    # a favicon request or the like: not the redirect
                        body = b""
                    conn.sendall(b"HTTP/1.1 " + (b"200 OK" if body else b"404 Not Found") + b"\r\nContent-Type: text/html; charset=utf-8\r\n"
                                 b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
                except OSError:
                    pass
                finally:
                    conn.close()
            if not done:
                AUTH.update(state="error", error="The sign-in took too long. Try again.")
        finally:
            srv.close()

    threading.Thread(target=serve, daemon=True).start()
    (open_browser or _open_browser)(url)
    return dict(AUTH)


def _open_browser(url: str) -> None:
    if os.environ.get("PHOTAG_NO_OPEN"):                      # tests: nothing may open on the screen
        return
    try:
        __import__("webbrowser").open(url)
    except Exception:
        pass


def access_token() -> str:
    if _ACCESS["token"] and time.time() < _ACCESS["exp"]:
        return _ACCESS["token"]
    s = _cfg()
    cid, secret = client_credentials()
    if not s.get("refresh_token") or not cid:
        raise GError("Connect to Google Photos first (Preferences).", 401)
    try:
        refresh = keystore.unprotect(s["refresh_token"])
    except Exception:
        _save(refresh_token=None)
        raise GError("Connect to Google Photos first (Preferences).", 401)
    try:
        tok = _http("POST", _url("PHOTAG_GOOGLE_TOKEN", TOKEN_URL), form={
            "client_id": cid, "client_secret": secret, "refresh_token": refresh, "grant_type": "refresh_token"})
    except GError as e:
        if e.status in (400, 401, 403):                       # revoked or expired: the permission has to be given again
            _save(refresh_token=None)
            _ACCESS.update(token="", exp=0.0)
            raise GError("Google refused the access. Connect again in Preferences.", e.status)
        raise
    _ACCESS.update(token=tok.get("access_token", ""), exp=time.time() + int(tok.get("expires_in", 3600)) - 60)
    return _ACCESS["token"]


def disconnect() -> None:
    s = _cfg()
    if s.get("refresh_token"):
        try:
            _http("POST", _url("PHOTAG_GOOGLE_REVOKE", REVOKE_URL), form={"token": keystore.unprotect(s["refresh_token"])}, timeout=10)
        except Exception:
            pass                                              # the local copy is removed in any case
    _save(refresh_token=None)
    _ACCESS.update(token="", exp=0.0)
    AUTH.update(state="idle", url="", error=None)


# ---------- the Picker session ----------
PICK = {"id": "", "uri": ""}                                   # the session in progress (for the UI)


def start_pick(open_browser=None, max_items: int | None = None) -> dict:
    """Create a Picker session and open Google's chooser. The import job (run_import) waits for the user to finish."""
    tok = access_token()
    body = {"pickingConfig": {"maxItemCount": str(max_items)}} if max_items else {}
    sess = _http("POST", _url("PHOTAG_GOOGLE_PICKER", PICKER_URL) + "/sessions", data=body, token=tok)
    uri = (sess.get("pickerUri") or "").rstrip("/")
    if not sess.get("id") or not uri:
        raise GError("Google answered with an error ({status}).", 0, status=0)
    PICK.update(id=sess["id"], uri=uri + "/autoclose")
    (open_browser or _open_browser)(PICK["uri"])
    return {"id": sess["id"], "uri": PICK["uri"], "poll": sess.get("pollingConfig") or {}}


def _list_items(session_id: str) -> list[dict]:
    out, page = [], ""
    base = _url("PHOTAG_GOOGLE_PICKER", PICKER_URL)
    while True:
        q = {"sessionId": session_id, "pageSize": "100"}
        if page:
            q["pageToken"] = page
        r = _http("GET", base + "/mediaItems?" + urllib.parse.urlencode(q), token=access_token())
        out += r.get("mediaItems") or []
        page = r.get("nextPageToken") or ""
        if not page:
            return out


def _wait_for_choice(session_id: str, progress, timeout: float) -> bool:
    """Poll until the user pressed Done in Google's window. False = cancelled or timed out."""
    base = _url("PHOTAG_GOOGLE_PICKER", PICKER_URL)
    t_end = time.time() + timeout
    interval = float(os.environ.get("PHOTAG_GOOGLE_POLL", 3))
    while time.time() < t_end and not progress.cancel:
        s = _http("GET", f"{base}/sessions/{session_id}", token=access_token())
        if s.get("mediaItemsSet"):
            return True
        pc = s.get("pollingConfig") or {}
        try:
            interval = max(1.0, min(10.0, float(str(pc.get("pollInterval", interval)).rstrip("s"))))
        except ValueError:
            pass
        time.sleep(interval)
    return False


# ---------- recognising photos photag already has ----------
_SUFFIX_RE = re.compile(r"[\s_-]*(edited|effects|collage|animation|mix|motion)$", re.I)
_COPY_RE = re.compile(r"\s*\(\d+\)$")


def stem(name: str) -> str:
    """'IMG_1234-edited(1).JPG' -> 'img_1234': what the same shot is called whatever Google added to the name."""
    s = Path(name or "").stem.lower().strip()
    s = _COPY_RE.sub("", s)
    s = _SUFFIX_RE.sub("", s)
    return _COPY_RE.sub("", s).strip()


def _epoch(create_time: str | None) -> int | None:
    if not create_time:
        return None
    import datetime
    try:
        d = datetime.datetime.fromisoformat(create_time.replace("Z", "+00:00"))
        return int(d.timestamp())
    except ValueError:
        return None


TZ_SLACK = 15 * 3600       # a capture time may be stored as local time in one place and UTC in the other


def find_known(con, filename: str, taken: int | None, width: int | None, height: int | None) -> int | None:
    """The id of a photo in the catalog that is the same shot as this Google item, or None.
    Same name (ignoring what Google adds to edited copies) and a capture time within the time-zone slack -- or the same name and
    pixel size, or the same size and the capture second. An edited copy that was cropped still matches by name and time."""
    s = stem(filename)
    if not s:
        return None
    like = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = con.execute("SELECT id, filename, taken_at, width, height FROM photos WHERE lower(filename) LIKE ? ESCAPE '\\'", (like,)).fetchall()
    same_size = (lambda r: width and height and ((r["width"], r["height"]) in ((width, height), (height, width))))
    for r in rows:
        if stem(r["filename"]) != s:
            continue
        if taken is None or r["taken_at"] is None or abs(int(r["taken_at"]) - taken) <= TZ_SLACK or same_size(r):
            return r["id"]
    if taken is not None and width and height:
        for r in con.execute("SELECT id, taken_at, width, height FROM photos WHERE taken_at BETWEEN ? AND ?", (taken - 2, taken + 2)):
            if same_size(r):
                return r["id"]
    return None


def _seen(con, media_id: str) -> int | None:
    row = con.execute("SELECT g.photo_id FROM gphotos_items g JOIN photos p ON p.id=g.photo_id WHERE g.media_id=?", (media_id,)).fetchone()
    return row["photo_id"] if row else None


def _remember(con, media_id: str, photo_id: int | None, status: str) -> None:
    con.execute("INSERT OR REPLACE INTO gphotos_items(media_id,photo_id,status,at) VALUES(?,?,?,?)", (media_id, photo_id, status, int(time.time())))


# ---------- the import job ----------
_MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/heic": ".heic", "image/webp": ".webp", "image/gif": ".gif",
             "video/mp4": ".mp4", "video/quicktime": ".mov"}


def _download(base_url: str, is_video: bool, dest: Path, token: str) -> int:
    url = base_url + ("=dv" if is_video else "=d")           # '=d': the original bytes with their metadata
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "User-Agent": "photag"})
    n = 0
    try:
        with urllib.request.urlopen(req, timeout=120, context=ssl_context() if url.startswith("https") else None) as r, open(dest, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                n += len(chunk)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        dest.unlink(missing_ok=True)
        raise GError("Could not reach Google. Check the internet connection.") from e
    return n


def run_import(progress, wait_seconds: float | None = None) -> None:
    """Job body: wait for the user's choice in the open Picker session, then bring in what is new."""
    import tempfile
    session_id = PICK["id"]
    con = db.connect()
    tmp = Path(tempfile.mkdtemp(prefix="photag_gphotos_", dir=str(PATHS.root)))
    try:
        progress.state = "scanning"
        progress.say("Waiting for you to choose photos in Google Photos…")
        if not _wait_for_choice(session_id, progress, wait_seconds if wait_seconds is not None else float(os.environ.get("PHOTAG_GOOGLE_WAIT", 1800))):
            progress.state = "done"
            progress.result = {"added": 0, "duplicates": 0, "failed": 0, "cancelled": True}
            progress.say("Nothing was chosen")
            return
        items = _list_items(session_id)
        stats = importer.ImportStats(progress, "gphotos", 0)
        progress.state = "importing"; progress.total = len(items); progress.done = 0
        importer.mark_import_start(con)
        for it in items:
            if progress.cancel:
                break
            progress.done += 1
            progress.say("{done}/{total}", done=progress.done, total=progress.total)
            mf = it.get("mediaFile") or {}
            meta = mf.get("mediaFileMetadata") or {}
            name = (mf.get("filename") or "").strip() or ("google-" + str(it.get("id", ""))[:12])
            media_id = str(it.get("id") or "")
            if not media_id or not mf.get("baseUrl"):
                stats.item("failed", name)
                continue
            try:
                if _seen(con, media_id):
                    stats.item("duplicate", name)
                    continue
                taken = _epoch(it.get("createTime"))
                known = find_known(con, name, taken, meta.get("width"), meta.get("height"))
                if known:                                            # the same shot (maybe edited in Google Photos): nothing is downloaded
                    _remember(con, media_id, known, "matched")
                    stats.item("duplicate", name)
                    continue
                is_video = (it.get("type") == "VIDEO") or str(mf.get("mimeType", "")).startswith("video/")
                if not Path(name).suffix:
                    name += _MIME_EXT.get(str(mf.get("mimeType", "")).lower(), ".mp4" if is_video else ".jpg")
                dest = tmp / importer._safe_component(name)
                size = _download(mf["baseUrl"], is_video, dest, access_token())
                pid, new = importer._ingest_file(con, dest, taken=taken)
                dest.unlink(missing_ok=True)
                _remember(con, media_id, pid, "added" if new else "same")
                stats.item("added" if new else "duplicate", name, size)
            except GError:
                raise
            except Exception:
                stats.item("failed", name)
            if progress.done % 10 == 0:
                con.commit()
        con.commit()
        _save(last_import=int(time.time()), last_reminder=int(time.time()))
        progress.state = "done"
        d = stats.d
        progress.result = {"added": d["added"], "duplicates": d["duplicates"], "failed": d["failed"]}
        progress.say_parts(("{n} added", {"n": d["added"]}),
                           *([("{n} already in catalog", {"n": d["duplicates"]})] if d["duplicates"] else []),
                           *([("{n} failed", {"n": d["failed"]})] if d["failed"] else []))
    except GError as e:
        progress.fail(e.key, **e.vars)
    except Exception as e:
        progress.fail("Import failed: {error}", error=str(e))
    finally:
        try:
            if session_id:
                _http("DELETE", f"{_url('PHOTAG_GOOGLE_PICKER', PICKER_URL)}/sessions/{session_id}", token=access_token(), timeout=10)
        except Exception:
            pass
        PICK.update(id="", uri="")
        try:
            for f in tmp.iterdir():
                f.unlink(missing_ok=True)
            tmp.rmdir()
        except OSError:
            pass
        con.close()
