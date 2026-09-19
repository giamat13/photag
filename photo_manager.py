"""PhotoManager entry point.

Runs the local FastAPI server and opens it in a native desktop window
(pywebview).

    python photo_manager.py
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
    log_dir = Path(os.environ.get("APPDATA") or Path.home()) / "PhotoManager"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "photo_manager.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


_ensure_std_streams()

import uvicorn

from app.server import app

HOST, PORT = "127.0.0.1", 8756
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


def main():
    threading.Thread(target=_serve, daemon=True).start()
    _wait_up()
    import webview
    webview.create_window("PhotoManager — ניהול תמונות", URL, width=1280, height=860)
    webview.start()


if __name__ == "__main__":
    main()
