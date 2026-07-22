"""PhotoManager entry point.

Runs the local FastAPI server and opens it in a native desktop window
(pywebview). Fallbacks: `--browser` just serves and prints the URL.

    python photo_manager.py            # desktop window
    python photo_manager.py --browser  # serve only, open in your browser
"""
import sys
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
    if "--browser" in sys.argv:
        print(f"PhotoManager → {URL}")
        _serve()
        return

    threading.Thread(target=_serve, daemon=True).start()
    _wait_up()
    try:
        import webview
        webview.create_window("PhotoManager — ניהול תמונות", URL, width=1280, height=860)
        webview.start()
    except Exception as e:
        print(f"[חלון לא זמין: {e}] פותח בדפדפן: {URL}")
        import webbrowser
        webbrowser.open(URL)
        _serve()


if __name__ == "__main__":
    main()
