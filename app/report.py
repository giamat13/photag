"""Problem reports and feature suggestions: "Help > Report a problem or suggest a feature" sends what the user wrote (and, if they allow it, a few technical details) to the
photag GitHub repository as an issue -- without the user needing a GitHub account.

The issue is created by a bot account whose access token is NOT in the source: the release workflow writes it into app/_report_token.py
(from the repository secret REPORT_TOKEN) while building, so only the shipped program has it. Without it (a build from source) the program
falls back to opening GitHub's "new issue" page with the text filled in. The token should be a classic token of the bot account with
only the `public_repo` scope; the bot owns nothing, so a leaked token can do no more than open issues (rotate it and ship an update).

Privacy: the issue is public. The dialog shows exactly what is sent; paths, user name, e-mail addresses and IP addresses are removed from the
log lines. Rate limit: 10 reports an hour, 30 a day per installation.
"""
import json
import os
import re
import sys
import time

REPO = "giamat13/photag"
API = "https://api.github.com"
MAX_DESC = 4000
MIN_TEXT = 3           # a title or a description of at least this many characters is enough: no description is needed (#29)
PER_HOUR, PER_DAY = 10, 30         # reports one installation may send (the first limits, 3 an hour and 10 a day, were too small: #24)
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


def compose(description: str, tech: str | None, version: str, client: str = "", kind: str = "problem", title: str = "") -> tuple[str, str]:
    """(title, body) of the issue. kind: "problem" or "feature" (a suggestion). The title is the one the user wrote, else the first line of the text."""
    desc = description.strip()
    first = re.sub(r"\s+", " ", title.strip() or (desc.splitlines()[0] if desc else "")).strip()
    title = ("[Suggestion] " if kind == "feature" else "[Report] ") + (first[:70] + ("…" if len(first) > 70 else ""))
    body = desc + "\n\n---\n"
    if tech:
        body += "<details><summary>Technical details</summary>\n\n```\n" + tech.replace("```", "'''") + "\n```\n</details>\n\n"
        if client.strip():
            body += "<details><summary>Window details</summary>\n\n```\n" + redact(client.strip()[:3500]).replace("```", "'''") + "\n```\n</details>\n\n"
    kind_name = "feature suggestion" if kind == "feature" else "problem"
    body += f"_Sent from photag {version} with “Report a problem or suggest a feature” ({kind_name})._"
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


def retry_in_minutes() -> int:
    """How long until the limit lets one more report through (at least 1 minute)."""
    now, t = time.time(), sorted(_recent_times())
    hour = [x for x in t if now - x < 3600]
    waits = []
    if len(hour) >= PER_HOUR:
        waits.append(hour[len(hour) - PER_HOUR] + 3600 - now)
    if len(t) >= PER_DAY:
        waits.append(t[len(t) - PER_DAY] + 86400 - now)
    return max(1, int(max(waits or [60]) // 60) + 1)


def allowed() -> bool:
    t = _recent_times()
    return sum(1 for x in t if time.time() - x < 3600) < PER_HOUR and len(t) < PER_DAY


def _note_sent():
    try:
        f = _times_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(_recent_times() + [time.time()]), "utf-8")
    except OSError:
        pass


# ------------------------------------------------------------------------------------------------ sending
def token() -> str:
    if "PHOTAG_REPORT_TOKEN" in os.environ:             # set (even empty = "no token"): tests and a deliberate off switch
        return os.environ["PHOTAG_REPORT_TOKEN"]
    try:
        from . import _report_token                      # written by the release workflow, not in the repository
        return str(_report_token.TOKEN or "")
    except Exception:
        return ""


def can_send() -> bool:
    return bool(token())


def fallback_url(title: str, body: str, kind: str = "problem") -> str:
    """GitHub's own "new issue" page with the text filled in (the user needs a GitHub account to finish it)."""
    from urllib.parse import quote
    b = body if len(body) < 4000 else body[:3900] + "\n\n…(shortened)"
    label = "enhancement" if kind == "feature" else "user-report"
    url = f"https://github.com/{REPO}/issues/new?title={quote(title)}&body={quote(b)}&labels={label}"
    return url[:FALLBACK_URL_CHARS] if len(url) > FALLBACK_URL_CHARS else url


def send(title: str, body: str, version: str, kind: str = "problem") -> dict:
    """Create the issue. Returns {"number", "url"}; raises ReportError."""
    import urllib.error
    import urllib.request
    tok = token()
    if not tok:
        raise ReportError("no token")
    base = os.environ.get("PHOTAG_REPORT_API", API).rstrip("/")
    labels = ["user-report", "enhancement"] if kind == "feature" else ["user-report"]
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "Content-Type": "application/json",
               "User-Agent": f"photag/{version}", "X-GitHub-Api-Version": "2022-11-28"}
    from .net import ssl_context
    ctx = ssl_context() if base.startswith("https") else None

    def post(with_labels: bool) -> dict:
        payload = {"title": title, "body": body, **({"labels": labels} if with_labels else {})}
        req = urllib.request.Request(f"{base}/repos/{REPO}/issues", data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))

    try:
        try:
            data = post(True)
        except urllib.error.HTTPError as e:
            if e.code not in (403, 404, 422):
                raise
            data = post(False)                           # the bot may not be allowed to label: the title says it anyway
    except urllib.error.HTTPError as e:
        remember(f"report: GitHub answered {e.code}")
        raise ReportError(f"GitHub answered {e.code}")
    except Exception as e:
        raise ReportError(str(e)[:120])
    _note_sent()
    remember_issue(data.get("number"), title, kind)
    return {"number": data.get("number"), "url": data.get("html_url")}


# ------------------------------------------------------------------------------------------------ "your suggestion was completed" (#26)
def _issues_file():
    from . import platform_dirs
    return platform_dirs.roaming_base() / "photag" / "report-issues.json"


def _load_issues() -> dict:
    try:
        d = json.loads(_issues_file().read_text("utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("issues"), list) else {"issues": [], "checked_at": 0}
    except (OSError, ValueError):
        return {"issues": [], "checked_at": 0}


def _save_issues(d: dict):
    try:
        f = _issues_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
    except OSError:
        pass


def remember_issue(number, title: str, kind: str):
    """Keep the number of an issue this installation created, to tell the user when it has been dealt with."""
    if not isinstance(number, int):
        return
    d = _load_issues()
    d["issues"] = (d["issues"] + [{"number": number, "title": re.sub(r"^\[(Report|Suggestion)\]\s*", "", title)[:120], "kind": kind,
                                   "at": time.time(), "closed": False, "notified": False}])[-100:]
    _save_issues(d)


def closed_news(force: bool = False, every: float = 3600.0) -> list[dict]:
    """The issues of this installation that were closed on GitHub since the user last heard of them. GitHub is asked at most once an
    `every` seconds (public data, no token); an unreachable GitHub just means no news now."""
    import urllib.request
    d = _load_issues()
    open_ones = [x for x in d["issues"] if not x.get("closed") and time.time() - x.get("at", 0) < 365 * 86400]
    if open_ones and (force or time.time() - d.get("checked_at", 0) >= every):
        base = os.environ.get("PHOTAG_REPORT_API", API).rstrip("/")
        from .net import ssl_context
        ctx = ssl_context() if base.startswith("https") else None
        for x in open_ones[:30]:
            try:
                req = urllib.request.Request(f"{base}/repos/{REPO}/issues/{x['number']}", headers={"Accept": "application/vnd.github+json", "User-Agent": "photag"})
                with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                    g = json.loads(r.read().decode("utf-8"))
                if g.get("state") == "closed":
                    x["closed"] = True
                    x["url"] = g.get("html_url") or f"https://github.com/{REPO}/issues/{x['number']}"
            except Exception:
                break                                    # offline or rate limited: try again at the next check
        d["checked_at"] = time.time()
        _save_issues(d)
    return [x for x in d["issues"] if x.get("closed") and not x.get("notified")]


def ack_news(numbers) -> None:
    d = _load_issues()
    for x in d["issues"]:
        if x["number"] in set(numbers):
            x["notified"] = True
    _save_issues(d)


# ------------------------------------------------------------------------------------------------ "My reports": see, edit and withdraw them (#34)
def _api_base() -> str:
    return os.environ.get("PHOTAG_REPORT_API", API).rstrip("/")


def _call(method: str, path: str, payload: dict | None = None, auth: bool = False) -> dict:
    """One GitHub API call. Raises ReportError. Only this project's issues are ever touched (the path is built by the callers)."""
    import urllib.error
    import urllib.request
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "photag", "X-GitHub-Api-Version": "2022-11-28"}
    if auth:
        tok = token()
        if not tok:
            raise ReportError("no token")
        headers["Authorization"] = f"Bearer {tok}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    from .net import ssl_context
    base = _api_base()
    try:
        req = urllib.request.Request(f"{base}/repos/{REPO}{path}", data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=15, context=ssl_context() if base.startswith("https") else None) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ReportError(f"GitHub answered {e.code}")
    except Exception as e:
        raise ReportError(str(e)[:120])


def split_body(body: str) -> tuple[str, str]:
    """(what the user wrote, everything after it: technical details and the "Sent from" line) of an issue body made by compose()."""
    i = (body or "").find("\n\n---\n")
    return (body, "") if i < 0 else (body[:i], body[i:])


def my_issues(refresh: bool = True) -> list[dict]:
    """The issues of this installation, newest first: number, title, kind, state ("open"/"closed"), description, url, comments.
    The state, title and text are re-read from GitHub (public data); offline, the remembered ones are returned."""
    d = _load_issues()
    items = list(reversed(d["issues"]))
    if refresh:
        for x in items[:30]:
            try:
                g = _call("GET", f"/issues/{int(x['number'])}")
            except ReportError:
                break
            x["closed"] = g.get("state") == "closed"
            x["title"] = re.sub(r"^\[(Report|Suggestion)\]\s*", "", g.get("title") or x["title"])[:120]
            x["description"] = split_body(g.get("body") or "")[0]
            x["comments"] = g.get("comments", 0)
        _save_issues(d)
    return [{"number": x["number"], "title": x["title"], "kind": x.get("kind", "problem"), "state": "closed" if x.get("closed") else "open",
             "description": x.get("description", ""), "comments": x.get("comments", 0), "at": x.get("at", 0)} for x in items]


def _mine(number: int) -> dict:
    for x in _load_issues()["issues"]:
        if x["number"] == number:
            return x
    raise ReportError("not your report")                  # the bot's token never edits an issue this installation did not create


def edit_issue(number: int, title: str, description: str) -> None:
    """Change the title and the text the user wrote; the technical details below it stay."""
    x = _mine(number)
    g = _call("GET", f"/issues/{number}")
    kind = x.get("kind", "problem")
    new_title = ("[Suggestion] " if kind == "feature" else "[Report] ") + re.sub(r"\s+", " ", title.strip())[:200]
    new_body = redact(description.strip()) + split_body(g.get("body") or "")[1]
    _call("PATCH", f"/issues/{number}", {"title": new_title, "body": new_body}, auth=True)
    x["title"] = title.strip()[:120]
    d = _load_issues()
    for y in d["issues"]:
        if y["number"] == number:
            y["title"] = x["title"]
    _save_issues(d)


def set_state(number: int, closed: bool) -> None:
    """Withdraw (close) one of the user's own reports, or open it again."""
    _mine(number)
    _call("PATCH", f"/issues/{number}", {"state": "closed" if closed else "open", **({"state_reason": "not_planned"} if closed else {})}, auth=True)
    d = _load_issues()
    for y in d["issues"]:
        if y["number"] == number:
            y["closed"] = closed
            y["notified"] = True if closed else False
    _save_issues(d)
