"""Helpers for the code-signing steps of .github/workflows/release.yml (SignPath returns the signed files in a folder).

    python tools/signing.py install-programs <folder>   put the signed photag.exe / photag-backup.exe where the build expects them
    python tools/signing.py install-setup <folder>      put the signed photagSetup.exe in installer_output/
    python tools/signing.py verify <file>...            fail unless every file carries a valid Authenticode signature (Windows)

Every install-* command also verifies what it installed, so a release can never go out unsigned when signing is switched on.
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = {"photag.exe": ROOT / "dist" / "photag" / "photag.exe", "photag-backup.exe": ROOT / "dist" / "photag-backup.exe"}
SETUP = ROOT / "installer_output" / "photagSetup.exe"


def find_signed(folder: Path, name: str) -> Path:
    """The signed file `name` inside what SignPath handed back (the files themselves, or a zip of them)."""
    for z in folder.rglob("*.zip"):
        with zipfile.ZipFile(z) as zf:
            zf.extractall(folder / (z.stem + "_unzipped"))
    hits = [p for p in folder.rglob(name) if p.is_file()]
    if not hits:
        raise SystemExit(f"the signed {name} is not in {folder}: {[str(p.relative_to(folder)) for p in folder.rglob('*') if p.is_file()]}")
    return hits[0]


def verify(paths) -> None:
    for p in paths:
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-AuthenticodeSignature -LiteralPath '{p}').Status"],
                                 capture_output=True, text=True).stdout.strip()
        except OSError as e:                          # no PowerShell: the signature cannot be checked, so it counts as not signed
            raise SystemExit(f"cannot check the signature of {p}: {e}")
        if out != "Valid":
            raise SystemExit(f"{p} is not validly signed (status: {out or 'unknown'})")
        print(f"signed: {p}")


def main(argv) -> None:
    if len(argv) < 2:
        raise SystemExit(__doc__)
    cmd, args = argv[0], argv[1:]
    if cmd == "install-programs":
        folder = Path(args[0])
        for name, dest in PROGRAMS.items():
            shutil.copy2(find_signed(folder, name), dest)
        verify(PROGRAMS.values())
    elif cmd == "install-setup":
        shutil.copy2(find_signed(Path(args[0]), SETUP.name), SETUP)
        verify([SETUP])
    elif cmd == "verify":
        verify(args)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
