# PyInstaller spec -> single Windows EXE.  Build:  pyinstaller photag.spec
# Output: dist/photag.exe
# Note: buffalo_l (~300MB) and the CLIP tagging model (~600MB) download at runtime, not bundled.
from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = [("app/ui", "app/ui"), ("app/label_stats.json", "app")]
binaries = []
hiddenimports = ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
                 "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on"]
for pkg in ("insightface", "onnxruntime", "pillow_heif", "scipy", "sklearn"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

a = Analysis(["photag.py"], pathex=[], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], runtime_hooks=[], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="photag",
          console=False, disable_windowed_traceback=False, upx=True,
          icon="app/ui/icon.ico")
