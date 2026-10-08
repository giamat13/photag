"""An icon in the notification area next to the clock (Windows), made with ctypes only -- no library to install, so code updates keep working.

    t = Tray("photag", [("Open photag", open_it), None, ("Exit", quit_it)], on_open=open_it)   # None = a separator line
    t.start()                       # runs its own thread; the icon is there when start() returns (False if Windows would not take it)
    t.notify("Title", "Text")       # a balloon / notification from the icon, from any thread
    t.stop()

balloon() shows one notification and removes the icon again (the background backup task uses it: no window, no icon left behind).
Everything is guarded: when anything is wrong (not Windows, no desktop, a call that fails) the Tray simply is not there.
"""
import ctypes
import sys
import threading
import time
from pathlib import Path

WM_NULL, WM_DESTROY, WM_CLOSE, WM_QUERYENDSESSION, WM_ENDSESSION = 0x0, 0x2, 0x10, 0x11, 0x16
WM_LBUTTONDBLCLK, WM_RBUTTONUP, WM_CONTEXTMENU = 0x203, 0x205, 0x7B
WM_APP = 0x8000
CB_MSG, CMD_NOTIFY, CMD_STOP = WM_APP + 1, WM_APP + 2, WM_APP + 3
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO, NIIF_WARNING = 0x1, 0x2
MENU_BASE = 1000


def supported() -> bool:
    return sys.platform == "win32"


_API = None


def _api():
    """The Win32 functions with exact argument types (a wrong type on 64-bit Windows corrupts handles)."""
    global _API
    if _API:
        return _API
    from ctypes import wintypes as wt
    u32, s32, k32 = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                    ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class NID(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
                    ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                    ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                    ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]

    def sig(fn, res, *args):
        fn.restype, fn.argtypes = res, list(args)
    sig(u32.DefWindowProcW, LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    sig(u32.RegisterClassW, wt.ATOM, ctypes.POINTER(WNDCLASSW))
    sig(u32.CreateWindowExW, wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)
    sig(u32.DestroyWindow, wt.BOOL, wt.HWND)
    sig(u32.PostMessageW, wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    sig(u32.PostQuitMessage, None, ctypes.c_int)
    sig(u32.GetMessageW, ctypes.c_int, ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT)
    sig(u32.TranslateMessage, wt.BOOL, ctypes.POINTER(wt.MSG))
    sig(u32.DispatchMessageW, LRESULT, ctypes.POINTER(wt.MSG))
    sig(u32.RegisterWindowMessageW, wt.UINT, wt.LPCWSTR)
    sig(u32.CreatePopupMenu, wt.HMENU)
    sig(u32.AppendMenuW, wt.BOOL, wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR)
    sig(u32.TrackPopupMenu, ctypes.c_int, wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID)
    sig(u32.DestroyMenu, wt.BOOL, wt.HMENU)
    sig(u32.SetForegroundWindow, wt.BOOL, wt.HWND)
    sig(u32.GetCursorPos, wt.BOOL, ctypes.POINTER(wt.POINT))
    sig(u32.LoadImageW, wt.HANDLE, wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT)
    sig(u32.LoadIconW, wt.HICON, wt.HINSTANCE, ctypes.c_void_p)
    sig(s32.Shell_NotifyIconW, wt.BOOL, wt.DWORD, ctypes.POINTER(NID))
    sig(k32.GetModuleHandleW, wt.HMODULE, wt.LPCWSTR)
    _API = dict(wt=wt, u32=u32, s32=s32, k32=k32, WNDPROC=WNDPROC, WNDCLASSW=WNDCLASSW, NID=NID)
    return _API


class Tray:
    def __init__(self, tip: str, menu: list, icon_path: str | None = None, on_open=None, on_end=None):
        self.tip, self.menu, self.icon_path, self.on_open, self.on_end = tip, menu, icon_path, on_open, on_end
        self.hwnd = None
        self.added = False
        self._notes: list = []                            # (title, text, warning); append / pop(0) are atomic enough for one writer and one reader
        self._ready, self._done = threading.Event(), threading.Event()
        self._thread = None
        self._keep = []                                   # the callback object must outlive the window

    # ---- public
    def start(self, timeout: float = 5.0) -> bool:
        if not supported():
            return False
        self._thread = threading.Thread(target=self._run, name="photag-tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self.added

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self.added)

    def notify(self, title: str, text: str, warning: bool = False):
        if not self.alive():
            return
        self._notes.append((title, text, warning))
        _api()["u32"].PostMessageW(self.hwnd, CMD_NOTIFY, 0, 0)

    def stop(self, timeout: float = 3.0):
        if self._thread and self._thread.is_alive() and self.hwnd:
            _api()["u32"].PostMessageW(self.hwnd, CMD_STOP, 0, 0)
            self._done.wait(timeout)

    # ---- the window thread
    def _nid(self):
        a = _api()
        nid = a["NID"]()
        nid.cbSize = ctypes.sizeof(a["NID"])
        nid.hWnd, nid.uID = self.hwnd, 1
        return nid

    def _icon(self):
        a = _api()
        h = None
        if self.icon_path and Path(self.icon_path).is_file():
            h = a["u32"].LoadImageW(None, str(self.icon_path), 1, 0, 0, 0x10 | 0x40)       # IMAGE_ICON, LR_LOADFROMFILE | LR_DEFAULTSIZE
        return h or a["u32"].LoadIconW(None, 32512)                                          # the standard application icon

    def _add(self) -> bool:
        a = _api()
        nid = self._nid()
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = CB_MSG
        nid.hIcon = self._icon()
        nid.szTip = self.tip[:127]
        return bool(a["s32"].Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _balloon(self):
        a = _api()
        if not self._notes:
            return
        title, text, warning = self._notes.pop(0)
        nid = self._nid()
        nid.uFlags = NIF_INFO
        nid.szInfoTitle, nid.szInfo = title[:63], text[:255]
        nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        a["s32"].Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _popup(self):
        a = _api()
        u32, wt = a["u32"], a["wt"]
        m = u32.CreatePopupMenu()
        for i, item in enumerate(self.menu):
            u32.AppendMenuW(m, 0x800 if item is None else 0, MENU_BASE + i, None if item is None else item[0])
        pt = wt.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        u32.SetForegroundWindow(self.hwnd)                  # without this the menu does not close when the user clicks elsewhere
        cmd = u32.TrackPopupMenu(m, 0x100 | 0x2, pt.x, pt.y, 0, self.hwnd, None)      # TPM_RETURNCMD | TPM_RIGHTBUTTON
        u32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        u32.DestroyMenu(m)
        i = cmd - MENU_BASE
        if cmd and 0 <= i < len(self.menu) and self.menu[i]:
            self._call(self.menu[i][1])

    @staticmethod
    def _call(fn):
        if fn:
            threading.Thread(target=lambda: _safe(fn), daemon=True).start()          # never run user code inside the window procedure

    def _run(self):
        try:
            a = _api()
            u32, k32 = a["u32"], a["k32"]
            taskbar_created = u32.RegisterWindowMessageW("TaskbarCreated")            # Explorer restarted: the icon must be put back

            def proc(hwnd, msg, wparam, lparam):
                try:
                    if msg == CB_MSG:
                        if lparam == WM_LBUTTONDBLCLK:
                            self._call(self.on_open)
                        elif lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                            self._popup()
                        return 0
                    if msg == CMD_NOTIFY:
                        self._balloon()
                        return 0
                    if msg == CMD_STOP:
                        u32.DestroyWindow(hwnd)
                        return 0
                    if msg == taskbar_created:
                        self.added = self._add()
                        return 0
                    if msg == WM_QUERYENDSESSION:
                        return 1
                    if msg in (WM_CLOSE, WM_ENDSESSION) and (msg == WM_CLOSE or wparam):
                        self._call(self.on_end)                                       # an installer / Windows asks the program to end
                        return 0
                    if msg == WM_DESTROY:
                        u32.PostQuitMessage(0)
                        return 0
                except Exception:
                    pass
                return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

            wndproc = a["WNDPROC"](proc)
            self._keep.append(wndproc)
            cls = a["WNDCLASSW"]()
            cls.lpfnWndProc = wndproc
            cls.hInstance = k32.GetModuleHandleW(None)
            cls.lpszClassName = f"photag-tray-{id(self)}"
            if not u32.RegisterClassW(ctypes.byref(cls)):
                return
            self.hwnd = u32.CreateWindowExW(0, cls.lpszClassName, "photag", 0, 0, 0, 0, 0, None, None, cls.hInstance, None)
            if not self.hwnd:
                return
            self.added = self._add()
            self._ready.set()
            if not self.added:
                u32.DestroyWindow(self.hwnd)
            msg = a["wt"].MSG()
            while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass
        finally:
            try:
                if self.added and self.hwnd:
                    nid = self._nid()
                    _api()["s32"].Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            except Exception:
                pass
            self.added = False
            self._ready.set()
            self._done.set()


def _safe(fn):
    try:
        fn()
    except Exception:
        pass


def balloon(title: str, text: str, warning: bool = False, seconds: float = 10.0, icon_path: str | None = None) -> bool:
    """One notification from a short-lived icon (for a program with no window of its own, e.g. the background backup task)."""
    t = Tray("photag", [], icon_path)
    if not t.start():
        return False
    t.notify(title, text, warning)
    time.sleep(seconds)                                 # the balloon needs the icon to stay for as long as it is shown
    t.stop()
    return True
