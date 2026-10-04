"""Test: the background backup on macOS (launchd) and Linux (systemd user timer): the generated files and the commands run."""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

tmp = Path(tempfile.mkdtemp(prefix="photag_bu_"))
os.environ["HOME"] = str(tmp)
os.environ["XDG_CONFIG_HOME"] = str(tmp / "cfg")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import backup_unix as bu  # noqa: E402

res = []
def check(n, ok, extra=""):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + (f"  [{extra}]" if extra else ""))

cmd = ["/opt/pho tag/photag", "--backup"]
plist = bu.make_plist(cmd).decode()
check("launchd plist: label, arguments, hourly and at load", "<string>com.photag.backup</string>" in plist and "<string>--backup</string>" in plist
      and "<integer>3600</integer>" in plist and "RunAtLoad" in plist)
check("launchd plist is well-formed XML (special characters escaped)", __import__("xml.dom.minidom", fromlist=["x"]).parseString(bu.make_plist(["/a&b/<x>", "--backup"])) is not None)
service, timer = bu.make_units(cmd)
check("systemd service runs the program with --backup, quoted for the space in the path", 'ExecStart="/opt/pho tag/photag" "--backup"' in service)
check("systemd timer: hourly, 5 minutes after boot, catches up after sleep (Persistent)", "OnUnitActiveSec=1h" in timer and "OnBootSec=5min" in timer and "Persistent=true" in timer)

ran = []
def fake_run(args):
    ran.append(args); return mock.Mock(returncode=0, stdout="", stderr="")
with mock.patch.object(bu, "_run", fake_run):
    with mock.patch.object(sys, "platform", "linux"):
        bu.register(cmd)
        d = bu.systemd_dir()
        check("linux: both unit files written under ~/.config/systemd/user", (d / "photag-backup.timer").is_file() and (d / "photag-backup.service").is_file())
        check("linux: daemon-reload, then enable --now the timer", ran[0][:3] == ["systemctl", "--user", "daemon-reload"] and ran[1][-2:] == ["--now", "photag-backup.timer"], ran)
        check("linux: registered() sees it", bu.registered())
        bu.unregister()
        check("linux: unregister removes the files", not bu.registered() and not (d / "photag-backup.service").exists())
    ran.clear()
    with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(os, "getuid", lambda: 501, create=True):
        bu.register(cmd)
        check("macOS: plist in ~/Library/LaunchAgents, loaded with launchctl bootstrap gui/501", bu.plist_path().is_file()
              and any(a[:3] == ["launchctl", "bootstrap", "gui/501"] for a in ran), ran)
        bu.unregister()
        check("macOS: unregister boots it out and removes the plist", not bu.plist_path().exists() and any(a[1] == "bootout" for a in ran))

with mock.patch.dict(os.environ, {"PHOTAG_BACKUP_TASK_EXE": "/x/photag"}):
    check("command(): the test override", bu.command() == ["/x/photag", "--backup"])
os.environ.pop("PHOTAG_BACKUP_TASK_EXE", None)
c = bu.command()
check("command() from source: python photag.py --backup", c and c[0] == sys.executable and c[1].endswith("photag.py") and c[2] == "--backup", c)

from app import backup_task  # noqa: E402
with mock.patch.object(sys, "platform", "linux"), mock.patch.object(bu, "tool", lambda: None):
    check("backup_task.supported() is False without systemctl", backup_task.supported() is False)
with mock.patch.object(sys, "platform", "linux"), mock.patch.object(bu, "tool", lambda: "/bin/systemctl"):
    check("backup_task.supported() is True with systemctl (from source)", backup_task.supported() is True)
n = res.count(False); print(f"\n{len(res)-n}/{len(res)} passed"); sys.exit(1 if n else 0)
