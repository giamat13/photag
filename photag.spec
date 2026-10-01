# PyInstaller spec -> single Windows EXE.  Build:  pyinstaller photag.spec
# Output: dist/photag.exe
# Note: buffalo_l (~300MB) downloads at runtime, not bundled.
from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = [("app/ui", "app/ui")]
binaries = []
hiddenimports = ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
                 "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on"]
REQUIRED = ("insightface", "onnxruntime", "pillow_heif", "scipy", "imageio_ffmpeg")   # without these the EXE would be broken
OPTIONAL = ("sklearn",)
for pkg in REQUIRED + OPTIONAL:
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception as e:
        if pkg in REQUIRED:
            raise SystemExit(f"photag.spec: required package '{pkg}' is not installed ({e}). Run: py -3.12 -m pip install -r requirements.txt")

a = Analysis(["photag.py"], pathex=[], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], runtime_hooks=[], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="photag",
          console=False, disable_windowed_traceback=False, upx=False,      # UPX makes antivirus false positives more likely
         
          icon="app/ui/icon.ico")
