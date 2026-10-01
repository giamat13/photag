# PyInstaller spec -> a program FOLDER (dist/photag/photag.exe + _internal). Build:  pyinstaller photag.spec
# A one-file EXE would unpack ~340 MB into %TEMP% at EVERY start (7 s on a quiet PC, much longer with antivirus scanning
# every unpacked file); a folder is installed once and starts in about a second.
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
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="photag",
          console=False, disable_windowed_traceback=False, upx=False,      # UPX makes antivirus false positives more likely
         
          icon="app/ui/icon.ico")
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="photag")
