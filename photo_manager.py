"""PhotoManager entry point.

Runs the local FastAPI server and opens it in a native desktop window
(pywebview).

    python photo_manager.py
"""
import threading
import time

import uvicorn

from app.server import app

HOST, PORT = "127.0.0.1", 8756
URL = f"http://{HOST}:{PORT}"


def _serve():
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def _wait_up(timeout=15):
    import urllib.request
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(f"{URL}/api/status", timeout=1)
            return True
        except Exception:
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
