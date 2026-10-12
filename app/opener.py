"""Open a file, or show it in the file manager, on any operating system (standard library only)."""
import os
import subprocess
import sys
from pathlib import Path


def _focus_explorer(folder_name: str) -> None:
    """Windows: Explorer opens behind the program that asked for it (the server has no right to take the focus).
    A background thread waits for the Explorer window that shows this folder and brings it to the front."""
    import threading

    def work():
        import ctypes
        import time
        u32 = ctypes.windll.user32
        found = []
        cb_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def each(hwnd, _):
            if not u32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(hwnd, cls, 64)
            if cls.value != "CabinetWClass":
                return True
            title = ctypes.create_unicode_buffer(512)
            u32.GetWindowTextW(hwnd, title, 512)
            if title.value.lower() == folder_name.lower():
                found.append(hwnd)
            return True

        cb = cb_type(each)
        for _ in range(30):                                  # up to ~3 seconds
            time.sleep(0.1)
            found.clear()
            u32.EnumWindows(cb, 0)
            if found:
                hwnd = found[0]
                u32.keybd_event(0x12, 0, 0, 0)               # a tap on Alt lets this process take the foreground
                u32.keybd_event(0x12, 0, 2, 0)
                if u32.IsIconic(hwnd):
                    u32.ShowWindow(hwnd, 9)                  # SW_RESTORE
                u32.SetForegroundWindow(hwnd)
                return

    try:
        threading.Thread(target=work, daemon=True).start()
    except Exception:
        pass


def reveal(path) -> None:
    """Show the file selected in the file manager (Explorer / Finder); on Linux, open its folder."""
    p = str(path)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", p])
        _focus_explorer(Path(p).parent.name or p)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", p])
    else:
        subprocess.Popen(["xdg-open", str(Path(p).parent)])


def open_default(path) -> bool:
    """Open the file with the program the system associates with it. False when the system has none to offer."""
    p = str(path)
    if sys.platform == "win32":
        os.startfile(p)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])
    return True
