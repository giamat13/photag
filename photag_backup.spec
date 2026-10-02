# PyInstaller spec -> dist/photag-backup.exe (tiny, backup only).  Build:  pyinstaller photag_backup.spec
a = Analysis(["photag_backup.py"], pathex=[], binaries=[], datas=[], hiddenimports=[], hookspath=[], runtime_hooks=[],
             excludes=["numpy", "scipy", "PIL", "uvicorn", "fastapi", "starlette", "pydantic", "webview", "onnxruntime", "insightface",
                       "cv2", "skimage", "sklearn", "tkinter", "imageio", "imageio_ffmpeg", "matplotlib", "pillow_heif", "piexif"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="photag-backup",
          console=False, disable_windowed_traceback=False, upx=False, version="version_info.txt", icon="app/ui/icon.ico")
