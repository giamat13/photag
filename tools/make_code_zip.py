"""Build the code update of this version: dist/photag-code-<version>-rt<RUNTIME>.zip (see codeboot.py).

    py -3.12 tools/make_code_zip.py

It holds manifest.json and the app/ package (Python files, web UI, translations), nothing executable.
"""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import codeboot  # noqa: E402
from app.version import __version__  # noqa: E402

SKIP_DIRS = {"__pycache__"}
SKIP_NAMES = {"_keys.json"}                         # a translation helper, not used at run time


def main() -> Path:
    out_dir = ROOT / "dist"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"photag-code-{__version__}-rt{codeboot.RUNTIME}.zip"
    files = [p for p in sorted((ROOT / "app").rglob("*"))
             if p.is_file() and not (SKIP_DIRS & set(p.parts)) and p.name not in SKIP_NAMES and p.suffix != ".pyc"]
    manifest = {"version": __version__, "runtime": codeboot.RUNTIME, "files": len(files)}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        for p in files:
            z.write(p, p.relative_to(ROOT).as_posix())
    print(f"{out}  {out.stat().st_size / 1024:.0f} KB  {len(files)} files  sha256 {hashlib.sha256(out.read_bytes()).hexdigest()}")
    return out


if __name__ == "__main__":
    main()
