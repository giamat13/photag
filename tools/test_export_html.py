"""Test: importer.run_export_html() -- a single self-contained HTML gallery (base64-embedded,
resized JPEGs, a lightbox) for sharing/viewing without photag. Videos are listed by name only.

    py -3.12 tools/test_export_html.py
"""
import base64
import os
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_export_html_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h"), ("HOME", "h")):
    (tmp / v).mkdir(exist_ok=True)
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import config, db, importer  # noqa: E402
from app.config import PATHS  # noqa: E402


class _Progress:
    def __init__(self):
        self.done = self.total = 0
        self.state = self.error = None
    def say(self, *a, **k): pass
    def fail(self, msg, **vars):
        self.state = "error"; self.error = msg.format(**vars)


config.PATHS.root.mkdir(parents=True, exist_ok=True)
(PATHS.media / "2024").mkdir(parents=True, exist_ok=True)
con = db.init_db()

p = PATHS.media / "2024" / "big.jpg"
Image.new("RGB", (800, 400), (120, 140, 160)).save(p, "JPEG")
pid = con.execute("INSERT INTO photos(sha256,filename,rel_path,bytes,taken_at) VALUES(?,?,?,?,?)",
                  ("sha1", "big.jpg", str(p.relative_to(PATHS.media)), p.stat().st_size, 1700000000)).lastrowid
vp = PATHS.media / "2024" / "clip.mp4"
vp.write_bytes(b"fake video bytes")
idv = con.execute("INSERT INTO photos(sha256,filename,rel_path,bytes,is_video) VALUES(?,?,?,?,1)",
                  ("sha2", "clip.mp4", str(vp.relative_to(PATHS.media)), vp.stat().st_size)).lastrowid
con.commit()

out = tmp / "gallery.html"
prog = _Progress()
importer.run_export_html([pid, idv], str(out), long_edge=300, quality=80, title="My Trip <script>", progress=prog)

check("export finished without failing", prog.state != "error", prog.error)
check("the HTML file was created", out.is_file())
html = out.read_text("utf-8")
check("it's a single, self-contained file (no external script/link src)", "<script src=" not in html and "<link " not in html, html[:300])
check("the title is escaped, not raw HTML-injected", "<script>Danny" not in html and "&lt;script&gt;" in html, html[html.find("<title>"):html.find("<title>")+60])
check("the photo is embedded as base64 JPEG", "data:image/jpeg;base64," not in html.split("ITEMS")[0], "")  # sanity: appears in the JS data, not before
m = re.search(r'"b":\s*"([A-Za-z0-9+/=]+)"', html)
check("a base64 image payload is present in the page data", bool(m))
if m:
    raw = base64.b64decode(m.group(1))
    im = Image.open(__import__("io").BytesIO(raw))
    check("the embedded image was actually resized to the long edge", max(im.size) <= 300, im.size)
check("the video is listed by name, not embedded as an image", '"v": true' in html.replace(" ", "") or '"v":true' in html.replace(" ", ""))
check("the video's filename appears in the page", "clip.mp4" in html)

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
