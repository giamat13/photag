"""Update from GitHub releases (https://github.com/giamat13/photag/releases).

check()    : asks the GitHub API for the latest release and compares its tag with the running version
download() : fetches the installer asset and VERIFIES it (size and SHA-256 from GitHub's asset digest,
             or from a "<asset>.sha256" file next to it) before it is ever run
launch()   : starts the Inno Setup installer silently with /update=1 (which restarts the app afterwards)
             and exits; from source (not the packaged EXE) it opens the release page instead
reconcile(): at every start-up, settles an update that was started earlier (see "Interrupted updates")
whats_new(): the release notes of one version, straight from GitHub

Interrupted updates (power loss, the installer being killed, the PC shutting down mid-way):
  * the update is installed OVER the existing app (no uninstall first), so the app is never "removed";
    your photos, catalog and settings live outside the program folder and are never touched
  * before the installer starts, the running program file is copied to %LOCALAPPDATA%\\photag\\update\\rollback
    and verified; a journal (pending.json) says what was about to happen
  * the installer writes done.flag when it has finished copying the new version
  * if the flag is missing the update did not finish: the next start of the app -- or, if the program itself is
    broken, a RunOnce script that Windows runs at the next sign-in -- puts the saved program file back

Safety: the installer is only ever downloaded from this repository's release URLs, and an asset with no
checksum is never installed automatically (the user is sent to the release page instead).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config
from .net import ssl_context
from .version import REPO, __version__

API = os.environ.get("PHOTAG_UPDATE_API", "https://api.github.com").rstrip("/")     # tests point this at a mock
ALLOWED_DOWNLOAD = os.environ.get("PHOTAG_UPDATE_ALLOW_PREFIX") or f"https://github.com/{REPO}/releases/download/"
ASSET_RE = re.compile(r"^photagSetup.*\.exe$", re.I)
CODE_RE = re.compile(r"^photag-code-(\d+\.\d+\.\d+)-rt(\d+)\.zip$")      # a code update (see codeboot.py): no installer to run
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


def running_is_prerelease() -> bool:
    """Whether the version currently running (app/version.py) is itself a pre-release build."""
    return parse_version(__version__)[3] == 0


def auto_enable_beta_if_prerelease():
    """Run at every start-up: once you're running a pre-release, tester mode turns itself on so later
    update checks keep offering pre-releases too -- otherwise, updating to one would silently drop you
    back to stable-only checks right afterwards, without ever having asked for that."""
    if running_is_prerelease() and not config.get_beta_channel():
        config.set_beta_channel(True)


def _get(url: str, timeout: float = 10):
    req = urllib.request.Request(url, headers={"User-Agent": f"photag/{__version__}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
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


def _pick_code_asset(assets: list[dict], latest: str) -> dict | None:
    """The code-only update of this release, when it is made for the exe that is running (same RUNTIME)."""
    import codeboot
    for a in assets:
        m = CODE_RE.match(a.get("name", ""))
        if not m or m.group(1) != latest or int(m.group(2)) != codeboot.RUNTIME:
            continue
        digest = (a.get("digest") or "").removeprefix("sha256:")
        if re.fullmatch(r"[0-9a-f]{64}", digest):
            return {"name": a["name"], "url": a.get("browser_download_url", ""), "size": a.get("size"), "sha256": digest, "sha256_url": None}
    return None


def _is_pre(rel: dict) -> bool:
    return bool(rel.get("prerelease")) or parse_version(rel.get("tag_name") or "")[3] == 0


def check(force: bool = False, include_pre: bool = False) -> dict:
    """Latest release info + whether it is newer. Never raises: a failed check is just {"available": False, "error"}.
    "prerelease" says the release offered is a pre-release (only possible in tester mode). include_pre (a manual check)
    also looks for a newer pre-release while tester mode is OFF and reports it as "pre" -- it is only mentioned, never
    offered for installation."""
    out = {"current": __version__, "available": False, "latest": None, "notes": "", "page": f"https://github.com/{REPO}/releases",
           "asset": None, "code_asset": None, "published": None, "skipped": False, "error": None,
           "prerelease": False, "pre": None}
    beta = config.get_beta_channel()
    now = time.time()
    if not force and _cache["data"] and _cache.get("beta") == beta and now - _cache["at"] < CACHE_SECONDS:
        rel = _cache["data"]
    else:
        try:
            if beta:
                # GitHub's own "latest" endpoint always skips pre-releases, so tester mode asks for the
                # most recent release of any kind instead and picks the first one by hand.
                rels = json.loads(_get(f"{API}/repos/{REPO}/releases?per_page=5"))
                rel = rels[0] if rels else {}
            else:
                rel = json.loads(_get(f"{API}/repos/{REPO}/releases/latest"))
        except urllib.error.HTTPError as e:
            out["error"] = f"HTTP {e.code}" + (" (no releases yet)" if e.code == 404 else "")
            return out
        except Exception as e:
            out["error"] = str(getattr(e, "reason", e))[:200]
            return out
        _cache.update(at=now, data=rel, beta=beta)
    tag = rel.get("tag_name") or ""
    out.update(latest=tag.lstrip("vV"), notes=rel.get("body") or "", page=rel.get("html_url") or out["page"],
               asset=_pick_asset(rel.get("assets") or []), published=rel.get("published_at"))
    out["code_asset"] = _pick_code_asset(rel.get("assets") or [], out["latest"])
    out["prerelease"] = bool(tag) and _is_pre(rel)
    out["available"] = bool(tag) and is_newer(tag, __version__)
    out["skipped"] = out["available"] and config.get_update_skipped() == out["latest"]
    if out["available"]:
        out["notes"] = _notes_since(__version__)       # several versions behind: show what's new in all of them, not just the latest
    if include_pre and not beta:
        out["pre"] = _newest_pre(out["latest"] if out["available"] else __version__)
    return out


def _newest_pre(newer_than: str) -> dict | None:
    """The newest pre-release that is newer than both the running version and `newer_than`, or None (also on any failure)."""
    try:
        rels = json.loads(_get(f"{API}/repos/{REPO}/releases?per_page=10"))
    except Exception:
        return None
    for r in rels:
        tag = r.get("tag_name") or ""
        if tag and _is_pre(r) and is_newer(tag, __version__) and is_newer(tag, newer_than):
            return {"latest": tag.lstrip("vV"), "page": r.get("html_url") or "", "notes": r.get("body") or ""}
    return None


def _notes_since(current: str) -> str:
    """Release notes of every version newer than `current`, newest first, so updating across several versions at once
    shows everything that changed, not just the last release's notes."""
    try:
        rels = json.loads(_get(f"{API}/repos/{REPO}/releases?per_page=20"))
    except Exception:
        return ""
    newer = [r for r in rels if is_newer(r.get("tag_name") or "", current)]
    if len(newer) <= 1:
        return newer[0].get("body") or "" if newer else ""
    return "\n\n---\n\n".join(f"## {r.get('tag_name', '')}\n\n{r.get('body') or ''}" for r in newer)


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
    return any(a and a["url"].startswith(ALLOWED_DOWNLOAD) and (a["sha256"] or a["sha256_url"])
               for a in (info.get("code_asset"), info.get("asset")))


def code_update_possible() -> bool:
    """A code update replaces files in <install folder>\\code: only when that folder can be written."""
    base = _exe_path().parent
    try:
        probe = base / ".write-test"
        probe.write_text("x"); probe.unlink()
        return True
    except OSError:
        return False


def state_dir() -> Path:
    """Where the update journal, the rollback copy and the installer's done.flag live (survives restarts)."""
    env = os.environ.get("PHOTAG_UPDATE_STATE_DIR")
    d = Path(env) if env else Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "photag" / "update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "photag-update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_download(progress):
    """Job: download + verify the installer of the latest release."""
    try:
        info = check(force=True)
        if not info["available"]:
            return progress.fail("No update available")
        if not can_install(info):
            return progress.fail("The installer can't be verified automatically. Download it from the release page")
        # a code update (a small zip, nothing executable) is preferred: it cannot be blocked by Smart App Control
        ca = info.get("code_asset")
        use_code = bool(ca and ca["url"].startswith(ALLOWED_DOWNLOAD) and ca["sha256"] and code_update_possible())
        a = ca if use_code else info["asset"]
        if not a or not a["url"].startswith(ALLOWED_DOWNLOAD):
            return progress.fail("The installer can't be verified automatically. Download it from the release page")
        want = _expected_sha(a)
        if not want:
            return progress.fail("The installer can't be verified automatically. Download it from the release page")
        dest = download_dir() / (a["name"] if use_code else f"photagSetup-{info['latest']}.exe")
        part = dest.with_suffix(".part")
        for old in list(download_dir().glob("photagSetup-*")) + list(download_dir().glob("photag-code-*")):          # leftovers of earlier updates
            if old != part:
                old.unlink(missing_ok=True)
        progress.state = "downloading"; progress.total = a.get("size") or 0; progress.done = 0
        progress.say("Downloading update… {pct}%", pct=0)
        h = hashlib.sha256()
        req = urllib.request.Request(a["url"], headers={"User-Agent": f"photag/{__version__}"})
        with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as r, open(part, "wb") as f:
            if not progress.total:
                progress.total = int(r.headers.get("Content-Length") or 0)
            while chunk := r.read(1 << 20):
                f.write(chunk); h.update(chunk); progress.done += len(chunk)
                progress.say("Downloading update… {pct}%", pct=int(100 * progress.done / progress.total) if progress.total else 0)
        size = part.stat().st_size
        if a.get("size") and size != a["size"]:
            part.unlink(missing_ok=True)
            return progress.fail("The downloaded file is incomplete ({got} of {want} bytes)", got=size, want=a["size"])
        if h.hexdigest() != want:
            part.unlink(missing_ok=True)
            return progress.fail("The downloaded file does not match its signature (SHA-256). It was deleted and not installed")
        os.replace(part, dest)
        progress.result = {"path": str(dest), "version": info["latest"], "sha256": want, "bytes": size, "kind": "code" if use_code else "installer"}
        progress.state = "done"; progress.say("Update downloaded and verified")
    except Exception as e:
        progress.fail("Download failed: {error}", error=str(e)[:200])


# ------------------------------------------------------------------ safe install: journal, rollback copy, recovery
RUNONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
RUNONCE_NAME = "photag-update-recovery"
STALE_AFTER = 10 * 60          # a started update with no done.flag after this long is treated as failed

RECOVER_PS1 = r"""param([string]$StateDir, [int]$WaitPid = 0, [switch]$NoLaunch)
# photag: put the previous program folder back if an update did not finish (see app/updater.py)
$ErrorActionPreference = 'Stop'
$log = Join-Path $StateDir 'recovery.log'
function Log($m) { Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format s), $m) }
$pj = Join-Path $StateDir 'pending.json'
if (-not (Test-Path $pj)) { exit 0 }
try { $j = Get-Content $pj -Raw -Encoding UTF8 | ConvertFrom-Json } catch { Log "unreadable journal: $_"; exit 1 }
if ($WaitPid -gt 0) { try { Wait-Process -Id $WaitPid -Timeout 60 } catch { } }
$deadline = (Get-Date).AddMinutes(5)       # an installer that is still running gets time to finish
while ((Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'photagSetup*' }) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
if (Test-Path (Join-Path $StateDir 'done.flag')) { Log 'update finished, nothing to restore'; exit 0 }
if (-not (Test-Path $j.backup_dir)) { Log 'no rollback copy found'; exit 1 }
Log ('restoring the previous version ' + $j.from_version)
# mirror the saved folder over the program folder: restores changed and deleted files, removes files a half-done update added;
# the uninstaller files (unins*) are left alone
& robocopy $j.backup_dir $j.app_dir /MIR /XF 'unins*.exe' 'unins*.dat' 'unins*.msg' /R:3 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Log ("robocopy failed, exit code " + $LASTEXITCODE); exit 1 }
Log 'restored'
Remove-Item $pj -Force
if (-not $NoLaunch) { Start-Process $j.exe }
"""


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _tree_fingerprint(root: Path) -> str:
    """Cheap fingerprint of a folder (names, sizes, times): tells whether a half-done update changed anything.
    The uninstaller files are ignored: they are not part of the program."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.name.lower().startswith("unins"):
            st = p.stat()
            h.update(f"{p.relative_to(root).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def _exe_path() -> Path:
    env = os.environ.get("PHOTAG_UPDATE_FAKE_EXE")          # tests: a stand-in for the installed program
    return Path(env) if env else Path(sys.executable)


def _write_json(path: Path, obj: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)                                    # all or nothing, even if the power goes now


def _runonce(set_it: bool, script: Path | None = None, state: Path | None = None):
    if sys.platform != "win32" or os.environ.get("PHOTAG_UPDATE_DRY_RUN"):
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUNONCE_KEY) as k:
        if set_it:
            cmd = (f'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script}" -StateDir "{state}"')
            winreg.SetValueEx(k, RUNONCE_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, RUNONCE_NAME)
            except FileNotFoundError:
                pass


def prepare_rollback(installer: Path, to_version: str) -> dict:
    """Everything that makes an interrupted update harmless. Raises (and starts nothing) if it cannot be done."""
    d = state_dir()
    exe = _exe_path()
    if not exe.is_file():
        raise UpdateError("program file not found")
    app_dir = exe.parent
    rb = d / "rollback"
    shutil.rmtree(rb, ignore_errors=True)
    backup_dir = rb / "app"
    shutil.copytree(app_dir, backup_dir, ignore=shutil.ignore_patterns("unins*"))        # the whole program folder
    fingerprint = _tree_fingerprint(app_dir)
    if _sha(backup_dir / exe.name) != _sha(exe):
        raise UpdateError("rollback copy does not match the program")
    (d / "done.flag").unlink(missing_ok=True)
    (d / "just_failed.json").unlink(missing_ok=True)
    script = d / "recover.ps1"
    script.write_text(RECOVER_PS1, "utf-8-sig")             # BOM: Windows PowerShell 5 reads it as UTF-8
    journal = {"from_version": __version__, "to_version": to_version, "installer": str(installer), "exe": str(exe),
               "app_dir": str(app_dir), "backup_dir": str(backup_dir), "fingerprint": fingerprint,
               "started": time.time(), "pid": os.getpid()}
    _write_json(d / "pending.json", journal)
    _runonce(True, script, d)
    return journal


def _clear_pending(d: Path, keep_flag: bool = False):
    (d / "pending.json").unlink(missing_ok=True)
    shutil.rmtree(d / "rollback", ignore_errors=True)
    if not keep_flag:
        (d / "done.flag").unlink(missing_ok=True)
    _runonce(False)


def reconcile() -> dict:
    """Run at every start. Settles an update that was started earlier:
    updated        - the installer finished and this is the new version (the UI shows "what's new")
    in_progress    - started a moment ago, the installer may still be running: leave everything alone
    failed_intact  - it never finished, but the program folder is exactly as before: nothing to restore
    restoring      - it never finished and the program folder changed: a recovery script puts the saved folder back"""
    d = state_dir()
    pj = d / "pending.json"
    if not pj.exists():
        return {"state": "none"}
    try:
        j = json.loads(pj.read_text("utf-8"))
    except Exception:
        pj.unlink(missing_ok=True)
        return {"state": "none"}
    flag = d / "done.flag"
    if flag.exists():
        if parse_version(__version__) >= parse_version(j.get("to_version", "")):
            _write_json(d / "just_updated.json", {"from": j.get("from_version"), "to": j.get("to_version")})
            _clear_pending(d)
            return {"state": "updated", "from": j.get("from_version"), "to": j.get("to_version")}
        return {"state": "in_progress"}
    if time.time() - j.get("started", 0) < STALE_AFTER:
        return {"state": "in_progress"}
    app_dir = Path(j.get("app_dir", ""))
    if app_dir.is_dir() and _tree_fingerprint(app_dir) == j.get("fingerprint"):
        _write_json(d / "just_failed.json", {"to": j.get("to_version"), "restored": False})
        _clear_pending(d)
        return {"state": "failed_intact"}
    script = d / "recover.ps1"
    if not script.exists():
        script.write_text(RECOVER_PS1, "utf-8-sig")
    if os.environ.get("PHOTAG_UPDATE_DRY_RUN"):
        return {"state": "restoring", "dry_run": True}
    _write_json(d / "just_failed.json", {"to": j.get("to_version"), "restored": True})
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script),
                      "-StateDir", str(d), "-WaitPid", str(os.getpid())],
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0), close_fds=True)
    threading.Timer(1.5, lambda: os._exit(0)).start()      # the script waits for this process, puts the old file back, restarts the app
    return {"state": "restoring"}


_notes_cache: dict[str, dict] = {}


def whats_new(version: str) -> dict:
    """Release notes of one version, fetched from GitHub (tag v<version>)."""
    out = {"version": version, "notes": "", "page": f"https://github.com/{REPO}/releases", "published": None, "error": None, "prerelease": False}
    if version in _notes_cache:
        return _notes_cache[version]
    try:
        rel = json.loads(_get(f"{API}/repos/{REPO}/releases/tags/v{version}"))
    except urllib.error.HTTPError as e:
        out["error"] = f"HTTP {e.code}"
        return out
    except Exception as e:
        out["error"] = str(getattr(e, "reason", e))[:200]
        return out
    out.update(notes=rel.get("body") or "", page=rel.get("html_url") or out["page"], published=rel.get("published_at"), prerelease=_is_pre(rel))
    _notes_cache[version] = out
    return out


def pending_notice() -> dict:
    """What the UI should tell the user after a restart: {"updated": {...}|None, "failed": {...}|None}."""
    d = state_dir()
    read = lambda n: (json.loads((d / n).read_text("utf-8")) if (d / n).exists() else None)
    try:
        return {"updated": read("just_updated.json"), "failed": read("just_failed.json")}
    except Exception:
        return {"updated": None, "failed": None}


def ack_notice():
    d = state_dir()
    for n in ("just_updated.json", "just_failed.json"):
        (d / n).unlink(missing_ok=True)


BLOCKED_WINERRORS = (4551, 1260, 4556)       # "an Application Control policy has blocked this file" and its relatives


class BlockedError(UpdateError):
    """The downloaded installer exists but Windows refused to run it (the app keeps running, nothing was changed)."""


def restart_command(exe: str, wait_pings: int = 4) -> str:
    """The command line (one string, handed to Windows as it is) that starts `exe` again a few seconds later.
    It must not go through Python's list quoting: that escapes the quotes around the program with a backslash, which cmd.exe
    does not understand, so `start` was given a lone backslash as the program to open ("Windows cannot find '\\\\'") and
    photag did not restart after an update."""
    return f'cmd.exe /d /s /c "ping -n {wait_pings} 127.0.0.1 >nul & start "" "{exe}""'


def apply_code(zip_path: Path) -> dict:
    """Install a downloaded code update: check it, put it in <install folder>\\code (the old code stays as code.prev),
    then restart the app. Nothing executable is written, so there is nothing for Smart App Control to block."""
    import codeboot
    import zipfile
    m = CODE_RE.match(zip_path.name)
    if not m or int(m.group(2)) != codeboot.RUNTIME:
        raise UpdateError("code update is not for this program")
    version = m.group(1)
    base = _exe_path().parent
    new, code, prev = base / "code.new", base / "code", base / "code.prev"
    shutil.rmtree(new, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            if z.testzip() is not None:
                raise UpdateError("zip is damaged")
            names = z.namelist()
            for n in names:                                  # only manifest.json and app/..., nothing that escapes the folder
                if n.startswith("/") or ".." in Path(n).parts or not (n == "manifest.json" or n.startswith("app/")):
                    raise UpdateError(f"unexpected file in the update: {n}")
                if n.lower().endswith((".exe", ".dll", ".pyd", ".sys", ".bat", ".cmd", ".ps1", ".msi", ".scr")):
                    raise UpdateError(f"an executable file in a code update: {n}")
            man = json.loads(z.read("manifest.json"))
            if man.get("version") != version or man.get("runtime") != codeboot.RUNTIME:
                raise UpdateError("manifest does not match")
            z.extractall(new)
        for f in new.rglob("*.py"):                          # a syntax error is found here, not after the restart
            compile(f.read_text("utf-8"), str(f), "exec")
        if not (new / "app" / "version.py").is_file():
            raise UpdateError("incomplete code update")
        (new / ".boots").write_text("0")
        shutil.rmtree(prev, ignore_errors=True)
        if code.exists():
            os.replace(code, prev)
        try:
            os.replace(new, code)
        except OSError:
            if prev.exists() and not code.exists():
                os.replace(prev, code)                       # put everything back as it was
            raise
    except UpdateError:
        shutil.rmtree(new, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(new, ignore_errors=True)
        raise UpdateError(str(e))
    _write_json(state_dir() / "just_updated.json", {"from": __version__, "to": version})      # the "what's new" window after the restart
    if os.environ.get("PHOTAG_UPDATE_DRY_RUN"):
        return {"mode": "code-dry-run", "version": version, "code": str(code)}
    # start the same (already allowed) photag.exe again a moment after this process has gone
    subprocess.Popen(restart_command(str(_exe_path())), close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                     | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    threading.Timer(1.0, lambda: os._exit(0)).start()
    return {"mode": "installing", "version": version}


def launch(path: str) -> dict:
    """Run the downloaded installer and quit so it can replace the files -- after making an interrupted install harmless.
    A downloaded code update (zip) is applied instead, without any installer."""
    p = Path(path)
    if not p.is_file() or p.parent != download_dir():
        raise UpdateError("not a downloaded update")
    if CODE_RE.match(p.name):
        return apply_code(p)
    dry = bool(os.environ.get("PHOTAG_UPDATE_DRY_RUN"))
    if dry and not os.environ.get("PHOTAG_UPDATE_FAKE_EXE"):
        return {"mode": "dry-run", "path": str(p)}
    if not dry and not getattr(sys, "frozen", False):
        return {"mode": "page"}                      # running from source: nothing to replace
    if not dry and config.PORTABLE:
        return {"mode": "page"}                      # portable build: no installed location for photagSetup.exe to update in place
    m = re.search(r"photagSetup-(.+)\.exe$", p.name, re.I)
    journal = prepare_rollback(p, m.group(1) if m else "")
    if dry:
        return {"mode": "dry-run", "path": str(p), "journal": journal}
    d = state_dir()
    args = [str(p), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/update=1", f"/LOG={d / 'installer.log'}"]
    # detached and outside this process's job object: closing the app (or the updater window) must not stop the installer
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        try:
            subprocess.Popen(args, creationflags=flags | 0x01000000, close_fds=True)         # CREATE_BREAKAWAY_FROM_JOB
        except OSError:
            subprocess.Popen(args, creationflags=flags, close_fds=True)
    except Exception as e:
        _clear_pending(d)                            # nothing started: nothing to roll back
        if getattr(e, "winerror", None) in BLOCKED_WINERRORS:
            raise BlockedError(str(e))               # Windows (Smart App Control / AppLocker) refused to run the unsigned installer
        raise UpdateError(str(e))
    threading.Timer(1.0, lambda: os._exit(0)).start()
    return {"mode": "installing"}
