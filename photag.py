"""photag entry point.

photag - a local photo manager.
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
    log_dir = Path(os.environ.get("APPDATA") or Path.home()) / "photag"   # windowed exe = Windows only
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "photag.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


_ensure_std_streams()

import codeboot                      # code updates: a newer copy of the `app` package next to the exe wins over the built-in one
_CODE = codeboot.activate(count="--backup" not in sys.argv)

if "--backup" in sys.argv:       # headless: used by the Windows scheduled task, never opens a window
    os.environ["PHOTAG_BACKGROUND"] = "1"      # a background run never moves the library folder
    from app import backup_cli
    _rc = backup_cli.main(sys.argv[1:])
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(_rc)                # leave at once: nothing stays in memory after a background backup

from app import platform_dirs
_T0 = time.time()
LOG = platform_dirs.roaming_base() / "photag" / "startup.log"


def _log(msg: str):
    """Start-up diary (%APPDATA%\\photag\\startup.log): when each step happened, so a start that
    hangs or fails can be diagnosed."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if msg.startswith("photag starting") and LOG.exists() and LOG.stat().st_size > 200_000:
            LOG.write_text("", "utf-8")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} +{time.time() - _T0:6.1f}s  {msg}\n")
    except OSError:
        pass


def _fatal(title: str, detail: str):
    """The EXE has no console: say what went wrong in a message box and keep the details in the log."""
    import traceback
    _log(f"FATAL: {title}\n{detail}\n{traceback.format_exc()}")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, f"{title}\n\n{detail}\n\nDetails: {LOG}", "photag", 0x10)
    except Exception:
        pass
    sys.exit(1)


_log(f"photag starting (frozen={getattr(sys, 'frozen', False)}, python {sys.version.split()[0]}, pid {os.getpid()}, code={_CODE})")
try:
    import uvicorn
    from app.server import app
except Exception as e:
    if codeboot.active() and not os.environ.get("PHOTAG_CODE_RETRIED"):
        # the downloaded code does not load: drop it and start again with the previous / built-in code
        _log(f"downloaded code failed to load ({type(e).__name__}: {e}); rolling back: {codeboot.rollback()}")
        import subprocess
        subprocess.Popen([sys.executable] + sys.argv[1:], env={**os.environ, "PHOTAG_CODE_RETRIED": "1"},
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)
        sys.exit(0)
    _fatal("photag could not load its components", f"{type(e).__name__}: {e}")
_log("components loaded")

def _view_files(args) -> list[str]:
    """Pictures named on the command line ("Open with photag", photag.exe "C:\\pictures\\a.jpg"): shown in the viewer, not imported."""
    from app import viewer
    out = []
    for a in args:
        if a.startswith("-"):
            continue
        try:
            p = Path(a)
            if p.is_file() and viewer.is_picture(p):
                out.append(str(p.resolve()))
        except OSError:
            pass
    return out


VIEW_FILES = _view_files(sys.argv[1:])

HOST = "127.0.0.1"
BASE_PORT = int(os.environ.get("PHOTAG_BASE_PORT", 8756))


def _photag_on(port: int) -> bool:
    """Is photag itself already answering on this port (another window of the app is open)?"""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/status", timeout=3) as r:
            d = json.loads(r.read())
        return "library_root" in d and "version" in d
    except Exception:
        return False


def _port_free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def _choose_port():
    """(port, already_running). The usual port when it is free; the running photag when it is that; otherwise the
    next free port, so another program using 8756 never stops photag from starting."""
    forced = os.environ.get("PHOTAG_PORT")
    if forced:
        return int(forced), False
    if _photag_on(BASE_PORT):
        return BASE_PORT, True
    for port in range(BASE_PORT, BASE_PORT + 50):
        if _port_free(port):
            if port != BASE_PORT:
                _log(f"port {BASE_PORT} is used by another program, using {port} instead")
            return port, False
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:     # last resort: any free port
        s.bind((HOST, 0))
        port = s.getsockname()[1]
    _log(f"ports {BASE_PORT}-{BASE_PORT + 49} are all used, using {port}")
    return port, False


PORT, ALREADY_RUNNING = _choose_port()
URL = f"http://{HOST}:{PORT}"


def _serve():
    try:
        _log(f"starting the local server on {HOST}:{PORT}")
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    except BaseException as e:           # e.g. the port is taken by another program
        _log(f"server stopped: {type(e).__name__}: {e}")
        raise


def _wait_up(timeout=15):
    # Only wait for the port to accept connections. /api/status can take several
    # seconds (it probes Ollama), so polling it with a short timeout never succeeds.
    import socket
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((HOST, PORT), timeout=1):
                _log("server is listening")
                codeboot.confirm()               # a good start: the downloaded code (if any) is kept
                return True
        except OSError:
            time.sleep(0.2)
    _log(f"server did not start listening within {timeout}s")
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


def _viewer_url() -> str | None:
    """The address of the viewer page for the first picture on the command line (the server makes a token for it), or None."""
    if not VIEW_FILES:
        return None
    import json
    import urllib.request
    try:
        req = urllib.request.Request(f"{URL}/api/viewer/open", data=json.dumps({"path": VIEW_FILES[0]}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return f"{URL}/viewer.html?t={json.loads(r.read())['token']}"
    except Exception as e:
        _log(f"could not open {VIEW_FILES[0]} in the viewer: {e!r}")
        return None


def _linger():
    """This program started the server for a picture: stay until the last viewer window is gone (other viewer windows may be using it),
    and, if the main window of photag was opened on it meanwhile, until that is idle too."""
    from app import viewer
    time.sleep(1)
    while True:
        v, m = viewer.idle_seconds()
        if v > 12 and m > 150:
            return
        time.sleep(2)


def _browser_mode(target: str | None = None):
    """No native window: open photag's page in the default browser and keep the server running until Ctrl+C."""
    import webbrowser
    target = target or URL
    print(f"photag is running at {target}  (press Ctrl+C to stop)", flush=True)
    try:
        webbrowser.open(target)
    except Exception:
        pass
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


def main():
    _own_taskbar_identity()
    if ALREADY_RUNNING:                          # photag is already open: just show another window of it, no second server
        _log(f"photag is already running on port {PORT}")
        if os.environ.get("PHOTAG_NO_WINDOW"):
            return
    elif os.environ.get("PHOTAG_NO_WINDOW"):     # server only, no window (tests of the packaged EXE)
        threading.Thread(target=_wait_up, daemon=True).start()
        _serve()
        return
    else:
        threading.Thread(target=_serve, daemon=True).start()
    if not ALREADY_RUNNING and not _wait_up(60):                         # a cold first start unpacks and loads a lot: allow a minute
        _fatal("photag could not start its local server",
               f"Nothing answered on port {PORT}. Another program may be using it, or antivirus is blocking photag.")
    if ALREADY_RUNNING:                          # this window is a process of its own: close it when an update replaces the program it shows
        from app import updater, windowwatch
        threading.Thread(target=windowwatch.watch, args=(PORT, windowwatch.server_version(PORT), updater.restarting_recently, updater.hard_exit),
                         daemon=True, name="photag-window-watch").start()
    view_url = _viewer_url()
    try:
        import webview
        if view_url:
            webview.create_window(f"{Path(VIEW_FILES[0]).name} - photag", view_url, width=1100, height=780)
        else:
            webview.create_window("photag", URL, width=1280, height=860)
        _log("opening the window")
        webview.start(icon=str(ICON) if ICON.exists() else None)
        if view_url and not ALREADY_RUNNING:
            _linger()
    except Exception as e:
        if sys.platform != "win32":                    # Linux / macOS without a usable window toolkit (GTK / Qt): use the browser
            _log(f"no window ({type(e).__name__}: {e}); opening {view_url or URL} in the default browser instead")
            _browser_mode(view_url)
            return
        _fatal("photag could not open its window", f"{type(e).__name__}: {e} (is the Microsoft Edge WebView2 runtime installed?)")
    _log("window closed, exiting")


if __name__ == "__main__":
    main()
