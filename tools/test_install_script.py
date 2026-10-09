"""Test: the install scripts (tools/photag-install.ps1 and the photag-install.bat built from it).

Static checks run everywhere. The real runs (unpack, replace, keep the data folder, uninstall, wrong checksum, a ZIP that carries the
Mark-of-the-Web) need Windows PowerShell and are skipped on other systems. One check asks GitHub which release it would download.

    py -3.12 tools/test_install_script.py
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


import make_install_script  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="photag_install_test_"))
ps1, bat = make_install_script.build(tmp / "out")
src = (ROOT / "tools" / "photag-install.ps1").read_text("utf-8")
ps1_bytes, bat_bytes = ps1.read_bytes(), bat.read_bytes()

# ---- static
check("the .ps1 has a UTF-8 BOM (Windows PowerShell 5.1 reads it correctly)", ps1_bytes.startswith(b"\xef\xbb\xbf"))
check("the .bat is one file: batch lines first, then the marker, then the PowerShell script", bat_bytes.startswith(b"@echo off") and b"#PS1#START\r\n" in bat_bytes)
bt = bat_bytes.decode("utf-8")
head, _, body = bt.partition("#PS1#START\r\n")
check("the marker appears once as a line (the batch command builds it in two pieces so it cannot find itself)", bt.count("#PS1#START") == 1 and "'#PS1#'+'START'" in head)
check("the script after the marker is exactly the .ps1 (same text)", body.replace("\r\n", "\n") == src.replace("\r\n", "\n"))
check("the batch part runs PowerShell without the execution-policy problem and passes the arguments on", "-ExecutionPolicy Bypass" in head and "%*" in head and "exit /b" in head)
check("both files use Windows line endings", b"\n" not in ps1_bytes.replace(b"\r\n", b"") and b"\n" not in bat_bytes.replace(b"\r\n", b""))
check("the script's parameters are documented in its header", all(f"-{x}" in src.split("#>")[0] for x in ("InstallDir", "ZipPath", "Sha256", "ListOnly", "Uninstall", "NoLaunch", "NoShortcuts", "NoPause")))
check("it never touches photos or the catalog (only the install folder and shortcuts)", "Pictures" not in src and "photag\\catalog" not in src.lower())
check("it checks the SHA-256 before installing anything", src.index("Get-FileHash") < src.index("ExtractToFile"))
check("it stops if photag is running instead of replacing files under it", src.count("is running") >= 2)
check("the end of the batch part cannot run into the PowerShell text (exit /b before the marker)", head.rstrip().endswith("exit /b %errorlevel%"))
check("it shows progress: numbered steps, a live download line and an unpacking line", all(x in src for x in ("Step 1 of 4", "Step 2 of 4", "Step 3 of 4", "Step 4 of 4", 'Show-Progress "Downloading"', 'Show-Progress "Unpacking"', "Write-Progress")))
check("it refuses ZIP entries that point outside the unpack folder", "unsafe path" in src)
check("an offline -ZipPath path exists", "[string]$ZipPath" in src)

if not sys.platform.startswith("win"):
    print("SKIP the real PowerShell runs (not Windows)")
else:
    def run(cmd, **kw):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300, **kw)

    def make_zip(path, version):
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("photag/photag.exe", f"fake exe {version}")
            z.writestr("photag/_internal/lib.dll", f"dll {version}")
            z.writestr("photag/portable.txt", "marker")
            z.writestr("photag/data/.keep", "")
            if version == "new":
                z.writestr("photag/newfile.txt", "only in the new one")
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    inst = tmp / "inst"
    z1, z2 = tmp / "one.zip", tmp / "two.zip"
    h1, h2 = make_zip(z1, "old"), make_zip(z2, "new")
    PS = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)]
    common = ["-InstallDir", str(inst), "-NoLaunch", "-NoShortcuts", "-NoPause"]

    # a ZIP that carries the Mark-of-the-Web, like one downloaded by a browser
    subprocess.run(["powershell", "-NoProfile", "-Command", f"Set-Content -LiteralPath '{z1}' -Stream Zone.Identifier -Value \"[ZoneTransfer]`r`nZoneId=3\""], capture_output=True)
    r = run(PS + ["-ZipPath", str(z1), "-Sha256", h1] + common)
    check("install from a ZIP: exit code 0", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    check("...and it said what it was doing, with progress (steps, check, unpacking at 100%)", all(x in r.stdout for x in ("Step 2 of 4", "Step 3 of 4", "Unpacking", "100%", "Step 4 of 4")), r.stdout[-300:])
    check("...the program is in place (photag.exe, subfolder, marker)", (inst / "photag.exe").read_text() == "fake exe old" and (inst / "_internal" / "lib.dll").exists() and (inst / "portable.txt").exists())
    check("...there is a data folder", (inst / "data").is_dir())
    z = run(["powershell", "-NoProfile", "-Command", f"(Get-Item -LiteralPath '{inst / 'photag.exe'}' -Stream *).Stream -join ','"])
    check("...and photag.exe carries NO Mark-of-the-Web (Zone.Identifier), although the ZIP did", "Zone.Identifier" not in z.stdout, z.stdout.strip())

    (inst / "data" / "my_catalog.db").write_text("precious")
    (inst / "stale.txt").write_text("from the old version")
    r = run(PS + ["-ZipPath", str(z2), "-Sha256", h2] + common)
    check("update over an existing install: exit code 0", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    check("...new files are in, old files are gone", (inst / "photag.exe").read_text() == "fake exe new" and (inst / "newfile.txt").exists() and not (inst / "stale.txt").exists())
    check("...and the data folder with its content was kept", (inst / "data" / "my_catalog.db").read_text() == "precious")

    r = run(PS + ["-ZipPath", str(z1), "-Sha256", "0" * 64] + common)
    check("a wrong SHA-256 stops the install (non-zero exit, clear message)", r.returncode != 0 and "does not match" in (r.stdout + r.stderr))
    check("...and changes nothing on disk", (inst / "photag.exe").read_text() == "fake exe new")
    bad = tmp / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("something/else.txt", "no photag.exe here")
    r = run(PS + ["-ZipPath", str(bad)] + common)
    check("a ZIP without photag\\photag.exe is refused", r.returncode != 0 and "does not contain" in (r.stdout + r.stderr))
    check("...installed files are still the new version (nothing was removed first)", (inst / "photag.exe").read_text() == "fake exe new")
    r = run(PS + ["-ZipPath", str(tmp / "nothing.zip")] + common)
    check("a missing ZIP is a clear error", r.returncode != 0 and "not found" in (r.stdout + r.stderr))

    # the .bat, double-clicked: same thing through cmd.exe, with arguments
    inst2 = tmp / "inst_bat"
    r = run(["cmd", "/c", str(bat), "-ZipPath", str(z2), "-Sha256", h2, "-InstallDir", str(inst2), "-NoLaunch", "-NoShortcuts", "-NoPause"])
    check("the .bat installs too (arguments reach the script)", r.returncode == 0 and (inst2 / "photag.exe").read_text() == "fake exe new", (r.stdout + r.stderr)[-300:])

    # shortcuts
    inst3 = tmp / "inst_lnk"
    env = {**os.environ, "APPDATA": str(tmp / "appdata")}
    (tmp / "appdata" / "Microsoft" / "Windows" / "Start Menu" / "Programs").mkdir(parents=True)
    r = run(PS + ["-ZipPath", str(z2), "-InstallDir", str(inst3), "-NoLaunch", "-NoPause"], env=env)
    lnk = tmp / "appdata" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "photag.lnk"
    check("a Start-menu shortcut is made (and the run still succeeds without a checksum, with a note)", r.returncode == 0 and lnk.exists() and "no SHA-256" in r.stdout, (r.stdout + r.stderr)[-200:])

    # uninstall keeps the data
    r = run(PS + ["-Uninstall", "-InstallDir", str(inst), "-NoPause"])
    check("-Uninstall removes the program but keeps the data folder", r.returncode == 0 and not (inst / "photag.exe").exists() and (inst / "data" / "my_catalog.db").read_text() == "precious", (r.stdout + r.stderr)[-200:])

# ---- which release would be downloaded (needs the network; skipped if GitHub cannot be reached)
if sys.platform.startswith("win"):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-ListOnly", "-NoPause"], capture_output=True, text=True, timeout=120)
        out = r.stdout + r.stderr
        if r.returncode == 0:
            check("-ListOnly finds the latest release's portable ZIP and its SHA-256 on GitHub", "-portable.zip" in out and "SHA256: " in out and len(out.split("SHA256: ")[1].split()[0]) == 64, out[-200:])
        else:
            print("SKIP -ListOnly (GitHub not reachable here):", out[-120:].replace("\n", " "))
    except Exception as e:
        print("SKIP -ListOnly:", e)

# ---- the one-line command from the README (runs the script from a URL, no file saved: nothing carries a Mark-of-the-Web)
ONE = ("[Net.ServicePointManager]::SecurityProtocol='Tls12'; $w=New-Object Net.WebClient; $w.Encoding=[Text.Encoding]::UTF8; "
       "& ([scriptblock]::Create($w.DownloadString('https://github.com/giamat13/photag/releases/latest/download/windows-photag-install.ps1').TrimStart([char]0xFEFF)))")
for readme in ("README.md", "README.he.md"):
    check(f"{readme} shows the one-line command exactly as tested", ONE in (ROOT / readme).read_text(encoding="utf-8"))
if sys.platform.startswith("win"):
    inst2 = tmp / "inst_oneliner"
    inst2.mkdir()
    (inst2 / "photag.exe").write_text("x")
    local = ONE.replace("https://github.com/giamat13/photag/releases/latest/download/windows-photag-install.ps1", ps1.as_uri()) + f" -Uninstall -InstallDir '{inst2}' -NoPause"
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", local], capture_output=True, text=True, timeout=120)
    check("the one-line command runs the script (here from a local URL) and passes parameters on", r.returncode == 0 and "Done." in r.stdout and not (inst2 / "photag.exe").exists(), (r.stdout + r.stderr)[-250:])

shutil.rmtree(tmp, ignore_errors=True)
n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
