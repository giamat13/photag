"""Pack the PyInstaller output of photag_unix.spec for release:

    Linux : dist/photag-<version>-linux-x64.tar.gz       (the folder dist/photag/, run ./photag/photag)
    macOS : dist/photag-<version>-macos-<arch>.zip       (photag.app)

    python tools/make_unix_package.py
"""
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.version import __version__  # noqa: E402


def main() -> Path:
    dist = ROOT / "dist"
    if sys.platform == "darwin":
        app = dist / "photag.app"
        if not app.is_dir():
            raise SystemExit(f"{app} is missing: run pyinstaller photag_unix.spec first")
        arch = "arm64" if platform.machine() == "arm64" else "x64"
        out = dist / f"photag-{__version__}-macos-{arch}.zip"
        out.unlink(missing_ok=True)
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app), str(out)], check=True)   # keeps symlinks and permissions
    elif sys.platform.startswith("linux"):
        folder = dist / "photag"
        if not (folder / "photag").is_file():
            raise SystemExit(f"{folder}/photag is missing: run pyinstaller photag_unix.spec first")
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copy2(ROOT / name, folder / name)
        shutil.copy2(ROOT / "docs" / "LINUX_MACOS.md", folder / "README.md")
        out = dist / f"photag-{__version__}-linux-x64.tar.gz"
        out.unlink(missing_ok=True)
        with tarfile.open(out, "w:gz") as tf:
            tf.add(folder, arcname="photag")
    else:
        raise SystemExit("this packs the Linux and macOS builds only")
    print(f"{out} ({out.stat().st_size / 1048576:.0f} MB)")
    return out


if __name__ == "__main__":
    main()
