"""photag entry point.

photag - a local photo manager in the spirit of Lightroom Classic.
Copyright (C) the photag authors. Free software under the GNU General Public License version 3
(or, at your option, any later version); see the file LICENSE. There is NO WARRANTY.

Runs the local FastAPI server and opens it in a native desktop window
(pywebview).

    python photag.py
"""
import os
import sys
import threading
import time
from pathlib import Path


def _ensure_std_streams():
    """The windowed EXE (console=False) starts with sys.stdout/stderr = None,
    which makes uvicorn's logging setup crash (sys.stdout.isatty()) before the
    server ever listens. Send them to a log file instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = Path(os.environ.get("APPDATA") or Path.home()) / "photag"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "photag.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


_ensure_std_streams()

if "--backup" in sys.argv:       # headless: used by the Windows scheduled task, never opens a window
    from app import backup_cli
    _rc = backup_cli.main(sys.argv[1:])
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(_rc)                # leave at once: nothing stays in memory after a background backup

import uvicorn

from app.server import app

HOST, PORT = "127.0.0.1", int(os.environ.get("PHOTAG_PORT", 8756))
URL = f"http://{HOST}:{PORT}"


def _serve():
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def _wait_up(timeout=15):
    # Only wait for the port to accept connections. /api/status can take several
    # seconds (it probes Ollama), so polling it with a short timeout never succeeds.
    import socket
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((HOST, PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


ICON = Path(__file__).resolve().parent / "app" / "ui" / "icon.ico"  # bundled next to the UI in the EXE too


def _own_taskbar_identity():
    """Give the process its own AppUserModelID so Windows shows photag's icon
    (not python.exe's) and doesn't group the window with other Python apps."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("photag.app")
    except Exception:
        pass


def main():
    _own_taskbar_identity()
    if os.environ.get("PHOTAG_NO_WINDOW"):       # server only, no window (tests of the packaged EXE)
        _serve()
        return
    threading.Thread(target=_serve, daemon=True).start()
    _wait_up()
    import webview
    webview.create_window("photag", URL, width=1280, height=860)
    webview.start(icon=str(ICON) if ICON.exists() else None)


if __name__ == "__main__":
    main()
