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
