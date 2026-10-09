"""Build the portable ZIP: dist/photag/* + photag-backup.exe + LICENSE + THIRD_PARTY_NOTICES.md + a "portable.txt" marker,
zipped as windows-photag-<version>-portable.zip. No installer, nothing is written to this PC: the result runs from a USB stick
or any folder, on any PC, and keeps its data (catalog, settings, backups) in a "data" folder right beside photag.exe
(see app/config.py: portable_dir()).

    py -3.12 build.bat                 (builds dist/photag first)
    py -3.12 tools/make_portable.py
"""
import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.version import __version__  # noqa: E402

MARKER = """This file tells photag to run in portable mode: it keeps its catalog, settings and backups in the "data"
folder right here, next to photag.exe, instead of this PC's user profile. Nothing is written anywhere else on this
computer. You can move or copy this whole folder (for example to a USB stick) and it keeps working; just make sure
photag is closed first. Delete this file to make photag use the normal per-PC location instead (on the next start it
asks before touching anything).
"""


def main() -> Path:
    dist = ROOT / "dist" / "photag"
    if not (dist / "photag.exe").is_file():
        raise SystemExit(f"{dist}\\photag.exe is missing: run the normal build first (build.bat, or pyinstaller photag.spec)")
    backup_exe = ROOT / "dist" / "photag-backup.exe"

    out_dir = ROOT / "dist"
    stage = out_dir / "photag-portable-stage"
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(dist, stage)
    if backup_exe.is_file():
        shutil.copy2(backup_exe, stage / "photag-backup.exe")
    shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", stage / "THIRD_PARTY_NOTICES.md")
    (stage / "portable.txt").write_text(MARKER, "utf-8")
    (stage / "data").mkdir(exist_ok=True)

    out = out_dir / f"windows-photag-{__version__}-portable.zip"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, Path("photag") / p.relative_to(stage))
    shutil.rmtree(stage, ignore_errors=True)
    print(f"{out}  {out.stat().st_size / 1048576:.1f} MB  sha256 {hashlib.sha256(out.read_bytes()).hexdigest()}")
    return out


if __name__ == "__main__":
    main()
