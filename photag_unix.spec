# PyInstaller spec for Linux and macOS -> a program FOLDER (dist/photag/photag) and, on macOS, dist/photag.app. Build:  pyinstaller photag_unix.spec
# Same bundle as photag.spec (the Windows one, which is the one tools/test_runtime_lock.py watches), without the Windows version
# resource and icon. Not signed: on macOS Gatekeeper asks once (right-click > Open); on Linux nothing asks.
import sys
from PyInstaller.utils.hooks import collect_all

datas = [("app/ui", "app/ui")]
binaries = []
hiddenimports = ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
                 "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on"]
REQUIRED = ("insightface", "onnxruntime", "pillow_heif", "scipy", "imageio_ffmpeg")
OPTIONAL = ("sklearn",)
for pkg in REQUIRED + OPTIONAL:
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception as e:
        if pkg in REQUIRED:
            raise SystemExit(f"photag_unix.spec: required package '{pkg}' is not installed ({e}). Run: pip install -r requirements.txt")

a = Analysis(["photag.py"], pathex=[], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], runtime_hooks=[], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="photag", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="photag")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="photag.app", bundle_identifier="io.github.giamat13.photag",
                 info_plist={"CFBundleName": "photag", "CFBundleDisplayName": "photag", "NSHighResolutionCapable": True})
