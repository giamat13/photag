"""Create tools/sandbox/photag.wsb for Windows Sandbox: a clean Windows that installs the freshly built
installer, exercises photag and uninstalls it (tools/sandbox/run-test.ps1). Results appear in
tools/sandbox/results/report.txt on this machine.

    python tools/sandbox/make_wsb.py      then double-click tools/sandbox/photag.wsb
Needs installer_output/photagSetup.exe (run build.bat first).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
(HERE / "results").mkdir(exist_ok=True)
# a tiny "code update" used by run-test.ps1 section 4b: the real exe must prefer it, and must recover from a broken one
import json as _json
import shutil as _shutil
sys_path_root = str(ROOT)
import sys as _sys
_sys.path.insert(0, sys_path_root)
import codeboot as _codeboot
cu = HERE / "codeupdate"
_shutil.rmtree(cu, ignore_errors=True)
for kind in ("good", "broken"):
    (cu / kind / "app").mkdir(parents=True)
    (cu / kind / "manifest.json").write_text(_json.dumps({"version": "9.9.9", "runtime": _codeboot.RUNTIME}), "utf-8")
    (cu / kind / "app" / "__init__.py").write_text("", "utf-8")
    (cu / kind / "app" / "version.py").write_text('__version__ = "9.9.9"\nREPO = "giamat13/photag"\n', "utf-8")
(cu / "broken" / "app" / "server.py").write_text("def (:  # not valid python\n", "utf-8")

for old in (HERE / "results").iterdir():
    if old.is_file():
        old.unlink()
if not (ROOT / "installer_output" / "photagSetup.exe").exists():
    raise SystemExit("installer_output/photagSetup.exe is missing: run build.bat first")
wsb = f"""<Configuration>
  <VGpu>Default</VGpu>
  <Networking>Default</Networking>
  <MemoryInMB>4096</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>{ROOT / 'installer_output'}</HostFolder><SandboxFolder>C:\\photag-installer</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>{HERE}</HostFolder><SandboxFolder>C:\\photag-test</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>{HERE / 'results'}</HostFolder><SandboxFolder>C:\\photag-results</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\\photag-test\\run-test.ps1</Command></LogonCommand>
</Configuration>
"""
(HERE / "photag.wsb").write_text(wsb, "utf-8")
print("written:", HERE / "photag.wsb")
