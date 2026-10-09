"""Problem reports: "Help > Report a problem" sends what the user wrote (and, if they allow it, a few technical details) to the
photag GitHub repository as an issue -- without the user needing a GitHub account.

The issue is created by a bot account whose access token is NOT in the source: the release workflow writes it into app/_report_token.py
(from the repository secret REPORT_TOKEN) while building, so only the shipped program has it. Without it (a build from source) the program
falls back to opening GitHub's "new issue" page with the text filled in. The token should be a classic token of the bot account with
only the `public_repo` scope; the bot owns nothing, so a leaked token can do no more than open issues (rotate it and ship an update).

Privacy: the issue is public. The dialog shows exactly what is sent; paths, user name, e-mail addresses and IP addresses are removed from the
log lines. Rate limit: 3 reports an hour, 10 a day per installation.
"""
import json
import os
import re
import sys
import time

REPO = "giamat13/photag"
API = "https://api.github.com"
MAX_DESC = 4000
MIN_DESC = 10
LOG_LINES = 120
LOG_CHARS = 9000
FALLBACK_URL_CHARS = 6500
_RING = __import__("collections").deque(maxlen=400)       # (imported this way: a new import name would change runtime.lock)


class ReportError(RuntimeError):
    pass


_LOG = None            # the open log file (photag.log in the settings folder), None when it cannot be written
_PREV = []             # the end of the log of the previous run: what happened just before a crash
_STARTED = time.time()
LOG_MAX = 1_500_000


def log_file():
    from . import platform_dirs
    return platform_dirs.roaming_base() / "photag" / "logs" / "photag.log"


def _open_log():
    """Open the log for appending (the old one becomes photag.1.log when it is big); remember the tail of the previous run."""
    global _LOG
    try:
        f = log_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists():
            try:
                _PREV[:] = [ln[:300] for ln in f.read_text("utf-8", errors="replace").splitlines()[-60:]]
            except OSError:
                pass
            if f.stat().st_size > LOG_MAX:
                f.replace(f.with_name("photag.1.log"))
        _LOG = open(f, "a", encoding="utf-8", errors="replace")
        _LOG.write(time.strftime("%Y-%m-%d %H:%M:%S") + " ---- start\n")
        _LOG.flush()
    except OSError:
        _LOG = None


def _keep(line: str):
    line = line[:400]
    _RING.append(line)
    if _LOG is not None:
        try:
            _LOG.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
            _LOG.flush()
        except (OSError, ValueError):
            pass


class _Tee:
    """Passes everything written to stderr on, and keeps the last lines for the report (and in the log file)."""

    def __init__(self, inner):
        self.inner = inner
        self._partial = ""

    def write(self, s):
        try:
            if self.inner is not None:
                self.inner.write(s)
        except Exception:
            pass
        try:
            self._partial += s
            *lines, self._partial = self._partial.split("\n")
            for ln in lines:
                if ln.strip():
                    _keep(ln)
        except Exception:
            pass
        return len(s)

    def flush(self):
        try:
            if self.inner is not None:
                self.inner.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self.inner, name)


def install():
    """Start keeping the recent error lines (called once when the server starts)."""
    if not isinstance(sys.stderr, _Tee):
        _open_log()
        sys.stderr = _Tee(sys.stderr)


def remember(line: str):
    """Add a line to the recent log by hand (used for caught errors)."""
    _keep(str(line))


# ------------------------------------------------------------------------------------------------ what is sent
def redact(text: str, extra: tuple = ()) -> str:
    """Remove what identifies the person or the computer: home folder, user name, library folder, e-mail and IP addresses."""
    import pathlib
    out = text
    secrets = [str(pathlib.Path.home()), *(e for e in extra if e)]
    for v in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA"):
        if os.environ.get(v):
            secrets.append(os.environ[v])
    for s in sorted({x for x in secrets if x and len(x) > 3}, key=len, reverse=True):
        for form in (s, s.replace("\\", "/"), s.replace("/", "\\")):
            out = out.replace(form, "<folder>")
    try:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        if user and len(user) > 2:
            out = re.sub(r"(?i)(?<![A-Za-z0-9])" + re.escape(user) + r"(?![A-Za-z0-9])", "<user>", out)
    except Exception:
        pass
    out = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "<email>", out)
    out = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", lambda m: m.group(0) if m.group(0).startswith(("127.", "0.0.0.")) else "<ip>", out)
    return out


def _versions() -> str:
    """Versions of the libraries the program depends on most (a bug is often in one of them)."""
    md = __import__("importlib.metadata", fromlist=["version"])
    out = []
    for name in ("Pillow", "numpy", "scipy", "fastapi", "uvicorn", "pywebview", "onnxruntime", "insightface"):
        try:
            out.append(f"{name} {md.version(name)}")
        except Exception:
            pass
    return ", ".join(out)


def tech_text(version: str, language: str = "", library: str = "", photos: int | None = None, extra: dict | None = None) -> str:
    """The technical details block (already redacted): version, system, the catalog in numbers, settings that matter, the last log lines."""
    platform = __import__("platform")
    shutil = __import__("shutil")
    lines = [f"photag {version}" + (" (packaged)" if getattr(sys, "frozen", False) else " (from source)"),
             f"System: {platform.system()} {platform.release()} ({platform.version()[:60]}) {platform.machine()}, {os.cpu_count()} CPUs",
             f"Python: {platform.python_version()}", f"Libraries: {_versions()}",
             f"Running for: {int((time.time() - _STARTED) // 60)} min"]
    if language:
        lines.append(f"Language: {language}")
    if photos is not None:
        lines.append(f"Photos in the catalog: {photos}")
    for k, v in (extra or {}).items():
        lines.append(f"{k}: {v}")
    try:
        if library:
            du = shutil.disk_usage(library)
            lines.append(f"Library disk: {du.free // 2**30} GB free of {du.total // 2**30} GB")
    except OSError:
        pass
    recent = list(_RING)[-LOG_LINES:]
    if recent:
        lines += ["", "Recent messages:"] + recent
    if _PREV:
        lines += ["", "End of the previous session's log:"] + _PREV[-40:]
    text = redact("\n".join(lines), (library,))
    return text[-LOG_CHARS:] if len(text) > LOG_CHARS else text


def compose(description: str, tech: str | None, version: str, client: str = "") -> tuple[str, str]:
    """(title, body) of the issue."""
    desc = description.strip()
    first = re.sub(r"\s+", " ", desc.splitlines()[0] if desc else "").strip()
    title = "[Report] " + (first[:70] + ("…" if len(first) > 70 else ""))
    body = desc + "\n\n---\n"
    if tech:
        body += "<details><summary>Technical details</summary>\n\n```\n" + tech.replace("```", "'''") + "\n```\n</details>\n\n"
        if client.strip():
            body += "<details><summary>Window details</summary>\n\n```\n" + redact(client.strip()[:3500]).replace("```", "'''") + "\n```\n</details>\n\n"
    body += f"_Sent from photag {version} with “Report a problem”._"
    return title, body


# ------------------------------------------------------------------------------------------------ limits
def _times_file():
    from . import platform_dirs
    return platform_dirs.roaming_base() / "photag" / "report-times.json"


def _recent_times() -> list[float]:
    try:
        return [float(x) for x in json.loads(_times_file().read_text("utf-8")) if time.time() - float(x) < 86400]
    except (OSError, ValueError, TypeError):
        return []


def allowed() -> bool:
    t = _recent_times()
    return sum(1 for x in t if time.time() - x < 3600) < 3 and len(t) < 10


def _note_sent():
    try:
        f = _times_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(_recent_times() + [time.time()]), "utf-8")
    except OSError:
        pass


# ------------------------------------------------------------------------------------------------ sending
def token() -> str:
    t = os.environ.get("PHOTAG_REPORT_TOKEN", "")
    if t:
        return t
    try:
        from . import _report_token                      # written by the release workflow, not in the repository
        return str(_report_token.TOKEN or "")
    except Exception:
        return ""


def can_send() -> bool:
    return bool(token())


def fallback_url(title: str, body: str) -> str:
    """GitHub's own "new issue" page with the text filled in (the user needs a GitHub account to finish it)."""
    from urllib.parse import quote
    b = body if len(body) < 4000 else body[:3900] + "\n\n…(shortened)"
    url = f"https://github.com/{REPO}/issues/new?title={quote(title)}&body={quote(b)}&labels=user-report"
    return url[:FALLBACK_URL_CHARS] if len(url) > FALLBACK_URL_CHARS else url


def send(title: str, body: str, version: str) -> dict:
    """Create the issue. Returns {"number", "url"}; raises ReportError."""
    import urllib.error
    import urllib.request
    tok = token()
    if not tok:
        raise ReportError("no token")
    base = os.environ.get("PHOTAG_REPORT_API", API).rstrip("/")
    req = urllib.request.Request(f"{base}/repos/{REPO}/issues", data=json.dumps({"title": title, "body": body, "labels": ["user-report"]}).encode("utf-8"),
                                 method="POST", headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                                                         "User-Agent": f"photag/{version}", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        from .net import ssl_context
        ctx = ssl_context() if base.startswith("https") else None
        with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ReportError(f"GitHub answered {e.code}")
    except Exception as e:
        raise ReportError(str(e)[:120])
    _note_sent()
    return {"number": data.get("number"), "url": data.get("html_url")}
