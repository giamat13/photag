"""Update from GitHub releases (https://github.com/giamat13/photag/releases).

check()    : asks the GitHub API for the latest release and compares its tag with the running version
download() : fetches the installer asset and VERIFIES it (size and SHA-256 from GitHub's asset digest,
             or from a "<asset>.sha256" file next to it) before it is ever run
launch()   : starts the Inno Setup installer silently with /update=1 (which restarts the app afterwards)
             and exits; from source (not the packaged EXE) it opens the release page instead

Safety: the installer is only ever downloaded from this repository's release URLs, and an asset with no
checksum is never installed automatically (the user is sent to the release page instead).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config
from .version import REPO, __version__

API = os.environ.get("PHOTAG_UPDATE_API", "https://api.github.com").rstrip("/")     # tests point this at a mock
ALLOWED_DOWNLOAD = os.environ.get("PHOTAG_UPDATE_ALLOW_PREFIX") or f"https://github.com/{REPO}/releases/download/"
ASSET_RE = re.compile(r"^photagSetup.*\.exe$", re.I)
CACHE_SECONDS = 6 * 3600
_cache: dict = {"at": 0, "data": None}


class UpdateError(Exception):
    pass


def parse_version(s: str) -> tuple:
    """"v1.10.2" -> (1, 10, 2); a pre-release suffix ("-beta") sorts before the release itself."""
    m = re.match(r"\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$", s or "")
    if not m:
        return (0, 0, 0, 0)
    nums = tuple(int(x or 0) for x in m.groups()[:3])
    return nums + (0 if m.group(4).strip().startswith("-") else 1,)


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def _get(url: str, timeout: float = 10):
    req = urllib.request.Request(url, headers={"User-Agent": f"photag/{__version__}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _pick_asset(assets: list[dict]) -> dict | None:
    cands = [a for a in assets if ASSET_RE.match(a.get("name", ""))]
    if not cands:
        return None
    a = next((x for x in cands if x["name"].lower() == "photagsetup.exe"), cands[0])
    digest = (a.get("digest") or "").removeprefix("sha256:") or None
    sidecar = next((x for x in assets if x.get("name") == a["name"] + ".sha256"), None)
    return {"name": a["name"], "url": a.get("browser_download_url", ""), "size": a.get("size"),
            "sha256": digest if digest and re.fullmatch(r"[0-9a-f]{64}", digest) else None,
            "sha256_url": sidecar.get("browser_download_url") if sidecar else None}


def check(force: bool = False) -> dict:
    """Latest release info + whether it is newer. Never raises: a failed check is just {"available": False, "error"}."""
    out = {"current": __version__, "available": False, "latest": None, "notes": "", "page": f"https://github.com/{REPO}/releases",
           "asset": None, "published": None, "skipped": False, "error": None}
    now = time.time()
    if not force and _cache["data"] and now - _cache["at"] < CACHE_SECONDS:
        rel = _cache["data"]
    else:
        try:
            rel = json.loads(_get(f"{API}/repos/{REPO}/releases/latest"))
        except urllib.error.HTTPError as e:
            out["error"] = f"HTTP {e.code}" + (" (no releases yet)" if e.code == 404 else "")
            return out
        except Exception as e:
            out["error"] = str(getattr(e, "reason", e))[:200]
            return out
        _cache.update(at=now, data=rel)
    tag = rel.get("tag_name") or ""
    out.update(latest=tag.lstrip("vV"), notes=rel.get("body") or "", page=rel.get("html_url") or out["page"],
               asset=_pick_asset(rel.get("assets") or []), published=rel.get("published_at"))
    out["available"] = bool(tag) and is_newer(tag)
    out["skipped"] = out["available"] and config.get_update_skipped() == out["latest"]
    return out


def skip(version: str):
    config.set_update_skipped(version)


def _expected_sha(asset: dict) -> str | None:
    if asset.get("sha256"):
        return asset["sha256"]
    if asset.get("sha256_url") and asset["sha256_url"].startswith(ALLOWED_DOWNLOAD):
        m = re.search(r"\b([0-9a-fA-F]{64})\b", _get(asset["sha256_url"]).decode("utf-8", "replace"))
        return m.group(1).lower() if m else None
    return None


def can_install(info: dict) -> bool:
    a = info.get("asset")
    return bool(a and a["url"].startswith(ALLOWED_DOWNLOAD) and (a["sha256"] or a["sha256_url"]))


def download_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "photag-update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_download(progress):
    """Job: download + verify the installer of the latest release."""
    try:
        info = check(force=True)
        if not info["available"]:
            return progress.fail("אין עדכון זמין")
        if not can_install(info):
            return progress.fail("אי אפשר לאמת את קובץ ההתקנה אוטומטית. הורידו אותו מדף השחרור")
        a = info["asset"]
        want = _expected_sha(a)
        if not want:
            return progress.fail("אי אפשר לאמת את קובץ ההתקנה אוטומטית. הורידו אותו מדף השחרור")
        dest = download_dir() / f"photagSetup-{info['latest']}.exe"
        part = dest.with_suffix(".part")
        for old in download_dir().glob("photagSetup-*"):          # leftovers of earlier updates
            if old != part:
                old.unlink(missing_ok=True)
        progress.state = "downloading"; progress.total = a.get("size") or 0; progress.done = 0
        progress.say("מוריד עדכון… {pct}%", pct=0)
        h = hashlib.sha256()
        req = urllib.request.Request(a["url"], headers={"User-Agent": f"photag/{__version__}"})
        with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:
            if not progress.total:
                progress.total = int(r.headers.get("Content-Length") or 0)
            while chunk := r.read(1 << 20):
                f.write(chunk); h.update(chunk); progress.done += len(chunk)
                progress.say("מוריד עדכון… {pct}%", pct=int(100 * progress.done / progress.total) if progress.total else 0)
        size = part.stat().st_size
        if a.get("size") and size != a["size"]:
            part.unlink(missing_ok=True)
            return progress.fail("הקובץ שהורד חלקי ({got} מתוך {want} בתים)", got=size, want=a["size"])
        if h.hexdigest() != want:
            part.unlink(missing_ok=True)
            return progress.fail("הקובץ שהורד לא תואם לחתימה (SHA-256). הוא נמחק ולא הותקן")
        os.replace(part, dest)
        progress.result = {"path": str(dest), "version": info["latest"], "sha256": want, "bytes": size}
        progress.state = "done"; progress.say("העדכון הורד ואומת")
    except Exception as e:
        progress.fail("ההורדה נכשלה: {error}", error=str(e)[:200])


def launch(path: str) -> dict:
    """Run the downloaded installer and quit so it can replace the files."""
    p = Path(path)
    if not p.is_file() or p.parent != download_dir():
        raise UpdateError("not a downloaded update")
    if os.environ.get("PHOTAG_UPDATE_DRY_RUN"):
        return {"mode": "dry-run", "path": str(p)}
    if not getattr(sys, "frozen", False):
        return {"mode": "page"}                      # running from source: nothing to replace
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(p), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/update=1"],
                     creationflags=flags, close_fds=True)
    threading.Timer(1.0, lambda: os._exit(0)).start()
    return {"mode": "installing"}
