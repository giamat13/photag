"""Build the two install-script downloads of a release from tools/photag-install.ps1 (see its header for what they do):

    dist/photag-install.ps1   the PowerShell script as it is
    dist/photag-install.bat   the same script behind a double-clickable batch file: one file, no execution-policy trouble

    py -3.12 tools/make_install_script.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "photag-install.ps1"
MARKER = "#PS1#" + "START"            # written in two pieces so the batch line below does not contain the marker itself

BAT_HEAD = (
    "@echo off\r\n"
    "setlocal\r\n"
    "rem photag installer: this batch file carries its own PowerShell script (everything after the marker line).\r\n"
    "rem Read it before you run it. See README.md, 'Windows says the installer is blocked'.\r\n"
    "set \"PHOTAG_BAT=%~f0\"\r\n"
    "powershell -NoProfile -ExecutionPolicy Bypass -Command \"$t=[IO.File]::ReadAllText($env:PHOTAG_BAT); $i=$t.IndexOf('#PS1#'+'START'); & ([scriptblock]::Create($t.Substring($i+11))) %*\"\r\n"
    "exit /b %errorlevel%\r\n"
)


def build(out_dir: Path | None = None) -> tuple[Path, Path]:
    out_dir = out_dir or ROOT / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    ps1_text = SRC.read_text("utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    ps1 = out_dir / "photag-install.ps1"
    bat = out_dir / "photag-install.bat"
    ps1.write_bytes(b"\xef\xbb\xbf" + ps1_text.encode("utf-8"))          # a BOM, so Windows PowerShell 5.1 reads it as UTF-8
    bat.write_bytes((BAT_HEAD + MARKER + "\r\n" + ps1_text).encode("utf-8"))
    return ps1, bat


if __name__ == "__main__":
    for p in build():
        print(p, p.stat().st_size, "bytes")
