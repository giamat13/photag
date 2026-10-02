"""Test: run_folder_import(..., recover_xmp=True) recovers rating/label/keywords/caption from an
XMP sidecar file or an XMP packet embedded in the image -- for importing plain Lightroom originals
when there's no .lrcat catalog left (e.g. a lapsed subscription).

    py -3.12 tools/test_import_xmp_recovery.py
"""
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_import_xmp_test_"))
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

src_dir = tmp / "src"
src_dir.mkdir()

sidecar_xmp = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="5" xmp:Label="Green">\n'
    '   <dc:subject><rdf:Bag><rdf:li>Sunset</rdf:li><rdf:li>Beach</rdf:li></rdf:Bag></dc:subject>'
    '<dc:description><rdf:Alt><rdf:li xml:lang="x-default">Evening at the shore</rdf:li></rdf:Alt></dc:description>\n'
    '  </rdf:Description>\n'
    ' </rdf:RDF>\n'
    '</x:xmpmeta>\n'
    '<?xpacket end="w"?>\n'
)

p1 = src_dir / "beach.jpg"
Image.new("RGB", (120, 80), (10, 20, 30)).save(p1, "JPEG")
(src_dir / "beach.jpg.xmp").write_text(sidecar_xmp, "utf-8")

p2 = src_dir / "plain.jpg"
Image.new("RGB", (120, 80), (40, 50, 60)).save(p2, "JPEG")   # no sidecar, no embedded XMP

config.PATHS.root.mkdir(parents=True, exist_ok=True)
con = db.init_db()

prog_type = type("P", (), {"done": 0, "total": 0, "cancel": False, "state": None, "error": None,
                            "say": lambda self, *a, **k: None,
                            "say_parts": lambda self, *a, **k: None,
                            "fail": lambda self, msg, **v: setattr(self, "error", msg.format(**v))})
prog = prog_type()
importer.run_folder_import([str(p1), str(p2)], [], None, True, prog)

check("import finished without failing", prog.state != "error", prog.error)
r1 = con.execute("SELECT rating, label, description FROM photos WHERE filename='beach.jpg'").fetchone()
check("rating was recovered from the sidecar", r1 and r1["rating"] == 5, dict(r1) if r1 else None)
check("color label was recovered (and normalized to lowercase)", r1 and r1["label"] == "green", r1["label"] if r1 else None)
check("caption was recovered", r1 and r1["description"] == "Evening at the shore", r1["description"] if r1 else None)
tags = {x["name"] for x in con.execute(
    "SELECT t.name FROM tags t JOIN photo_tags pt ON pt.tag_id=t.id JOIN photos p ON p.id=pt.photo_id WHERE p.filename='beach.jpg'")}
check("keywords were recovered as tags", tags == {"Sunset", "Beach"}, tags)

r2 = con.execute("SELECT rating, label FROM photos WHERE filename='plain.jpg'").fetchone()
check("a file with no XMP at all gets no rating", r2 and not r2["rating"])
check("a file with no XMP at all gets no label", r2 and not r2["label"])

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
