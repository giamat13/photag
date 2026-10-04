"""Open a file, or show it in the file manager, on any operating system (standard library only)."""
import os
import subprocess
import sys
from pathlib import Path


def reveal(path) -> None:
    """Show the file selected in the file manager (Explorer / Finder); on Linux, open its folder."""
    p = str(path)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", p])
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
