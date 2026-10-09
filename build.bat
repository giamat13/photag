@echo off
setlocal
cd /d "%~dp0"

echo === Closing any running photag / installer instances ===
taskkill /IM photagSetup.exe /F >nul 2>&1
taskkill /IM photag.exe /F >nul 2>&1

rem Build with the Python 3.12 install that actually has fastapi/uvicorn/webview
rem installed (requirements.txt). The plain "pyinstaller" on PATH can resolve to
rem a *different* Python install with none of those -> PyInstaller then silently
rem skips them and produces an exe that crashes at startup with no visible error.
set PYEXE=py -3.12
%PYEXE% -c "import fastapi, uvicorn, webview" 2>nul
if errorlevel 1 (
  echo Python 3.12 is missing required packages. Run: py -3.12 -m pip install -r requirements.txt
  exit /b 1
)

echo === Generating version_info.txt ===
%PYEXE% tools\make_version_info.py
if errorlevel 1 (
  echo Could not generate version_info.txt.
  exit /b 1
)

echo === Building photag.exe (PyInstaller) ===
%PYEXE% -m PyInstaller photag.spec
if errorlevel 1 (
  echo PyInstaller build failed.
  exit /b 1
)

for /f %%v in ('%PYEXE% -c "from app.version import __version__; print(__version__)"') do set VER=%%v
echo === Building photag-backup.exe (small background backup program) ===
%PYEXE% -m PyInstaller photag_backup.spec
if errorlevel 1 (
  echo PyInstaller build of photag-backup failed.
  exit /b 1
)

echo === Version %VER% ===

echo === Building installer (Inno Setup) ===
set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="C:\Program Files\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
  echo Inno Setup Compiler ^(ISCC.exe^) not found. Install from https://jrsoftware.org/isdl.php
  exit /b 1
)
%ISCC% /DMyAppVersion=%VER% installer.iss
if errorlevel 1 (
  echo Inno Setup build failed.
  exit /b 1
)

echo === Building the portable ZIP (no installer) ===
%PYEXE% tools\make_portable.py
if errorlevel 1 (
  echo Portable ZIP build failed.
  exit /b 1
)

echo === Done: installer_output\photagSetup.exe and dist\windows-photag-%VER%-portable.zip ===
start "" "installer_output\photagSetup.exe"
endlocal
