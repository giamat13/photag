"""The background backup on macOS (a launchd agent) and Linux (a systemd user timer) -- what backup_task.py does
with a Scheduled Task on Windows. Per user, no admin rights; it runs "<photag> --backup" every hour and at every
sign-in, and each run only backs up when one is actually due (app/backup.py). Standard library only."""
import os
import subprocess
import sys
from pathlib import Path

LABEL = "com.photag.backup"
UNIT = "photag-backup"


def command() -> list[str] | None:
    """What to run: the packaged program, or `python photag.py` when started from source."""
    env = os.environ.get("PHOTAG_BACKUP_TASK_EXE")        # tests
    if env:
        return [env, "--backup"]
    if getattr(sys, "frozen", False):
        return [sys.executable, "--backup"]
    script = Path(__file__).resolve().parent.parent / "photag.py"
    return [sys.executable, str(script), "--backup"] if script.is_file() else None


def _home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def plist_path() -> Path:
    return _home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def systemd_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config") / "systemd" / "user"


def make_plist(cmd: list[str]) -> bytes:
    def esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    args = "".join(f"\n    <string>{esc(a)}</string>" for a in cmd)
    return (f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>{args}
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>3600</integer>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
</dict>
</plist>
''').encode("utf-8")


def _quote(arg: str) -> str:
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def make_units(cmd: list[str]) -> tuple[str, str]:
    service = ("[Unit]\nDescription=photag: automatic catalog backup\n\n[Service]\nType=oneshot\nNice=10\n"
               "IOSchedulingClass=idle\nExecStart=" + " ".join(_quote(a) for a in cmd) + "\n")
    timer = ("[Unit]\nDescription=photag: run the catalog backup every hour\n\n[Timer]\nOnBootSec=5min\n"
             "OnUnitActiveSec=1h\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")
    return service, timer


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


def tool() -> str | None:
    import shutil
    return shutil.which("launchctl" if sys.platform == "darwin" else "systemctl")


def registered() -> bool:
    if sys.platform == "darwin":
        return plist_path().is_file()
    return (systemd_dir() / f"{UNIT}.timer").is_file()


def register(cmd: list[str]) -> None:
    if sys.platform == "darwin":
        p = plist_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(make_plist(cmd))
        uid = str(os.getuid())
        _run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"])
        r = _run(["launchctl", "bootstrap", f"gui/{uid}", str(p)])
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[:300])
        return
    d = systemd_dir()
    d.mkdir(parents=True, exist_ok=True)
    service, timer = make_units(cmd)
    (d / f"{UNIT}.service").write_text(service, encoding="utf-8")
    (d / f"{UNIT}.timer").write_text(timer, encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"])
    r = _run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"])
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])


def unregister() -> None:
    if sys.platform == "darwin":
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
        plist_path().unlink(missing_ok=True)
        return
    _run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"])
    for ext in ("timer", "service"):
        (systemd_dir() / f"{UNIT}.{ext}").unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"])
