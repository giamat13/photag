"""Test: importer.run_digikam_import() reads a digiKam SQLite database (digikam4.db) and imports the
photos it references, along with rating, caption, GPS, tags and person tags.

    py -3.12 tools/test_digikam_import.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_digikam_test_"))
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

photos_dir = tmp / "Pictures"
(photos_dir / "Trip").mkdir(parents=True)
p1 = photos_dir / "Trip" / "a.jpg"
Image.new("RGB", (60, 40), (1, 2, 3)).save(p1, "JPEG")
p2 = photos_dir / "Trip" / "missing.jpg"   # referenced in the DB but not actually on disk

dk_path = tmp / "digikam4.db"
dk = sqlite3.connect(dk_path)
dk.executescript("""
CREATE TABLE AlbumRoots(id INTEGER PRIMARY KEY, label TEXT, status INTEGER, type INTEGER, identifier TEXT, specificPath TEXT);
CREATE TABLE Albums(id INTEGER PRIMARY KEY, albumRoot INTEGER, relativePath TEXT, date TEXT, caption TEXT, collection TEXT, icon INTEGER);
CREATE TABLE Images(id INTEGER PRIMARY KEY, album INTEGER, name TEXT, status INTEGER, category INTEGER, modificationDate TEXT, fileSize INTEGER, uniqueHash TEXT);
CREATE TABLE ImageInformation(imageid INTEGER PRIMARY KEY, rating INTEGER, creationDate TEXT, digitizationDate TEXT, orientation INTEGER, width INTEGER, height INTEGER, format TEXT, colorDepth INTEGER, colorModel INTEGER);
CREATE TABLE ImageComments(id INTEGER PRIMARY KEY, imageid INTEGER, type INTEGER, language TEXT, author TEXT, date TEXT, comment TEXT);
CREATE TABLE Tags(id INTEGER PRIMARY KEY, pid INTEGER, name TEXT, icon TEXT, iconKDE TEXT);
CREATE TABLE ImageTags(imageid INTEGER, tagid INTEGER);
CREATE TABLE TagProperties(tagid INTEGER, property TEXT, value TEXT);
CREATE TABLE ImagePositions(imageid INTEGER PRIMARY KEY, latitudeNumber REAL, longitudeNumber REAL);
""")
dk.execute("INSERT INTO AlbumRoots VALUES(1,'Pictures',0,1,?,?)", (f"volumeid:?path={photos_dir.as_posix()}", str(photos_dir)))
dk.execute("INSERT INTO Albums VALUES(1,1,'/Trip','2024-01-01','',NULL,NULL)")
dk.execute("INSERT INTO Images VALUES(1,1,'a.jpg',1,1,'2024-01-01',?,'h1')", (p1.stat().st_size,))
dk.execute("INSERT INTO Images VALUES(2,1,'missing.jpg',1,1,'2024-01-01',0,'h2')")
dk.execute("INSERT INTO ImageInformation VALUES(1,4,'2024-06-01T10:30:00',NULL,1,60,40,'JPG',8,1)")
dk.execute("INSERT INTO ImageComments VALUES(1,1,1,'x','','2024-06-01','A day at the lake')")
dk.execute("INSERT INTO Tags VALUES(1,0,'Nature',NULL,NULL)")
dk.execute("INSERT INTO Tags VALUES(2,0,'Danny',NULL,NULL)")
dk.execute("INSERT INTO TagProperties VALUES(2,'person','')")
dk.execute("INSERT INTO ImageTags VALUES(1,1)")
dk.execute("INSERT INTO ImageTags VALUES(1,2)")
dk.execute("INSERT INTO ImagePositions VALUES(1,32.05,34.78)")
dk.commit()
dk.close()

config.PATHS.root.mkdir(parents=True, exist_ok=True)
con = db.init_db()

info = importer.dkdb_info(str(dk_path))
check("info reports both referenced images", info["images"] == 2, info)
check("info reports the one missing file", info["missing"] == 1, info)
check("info reports tags and albums", info["tags"] == 2 and info["albums"] == 1, info)

prog_type = type("P", (), {"done": 0, "total": 0, "cancel": False, "state": None, "error": None,
                            "say": lambda self, *a, **k: None,
                            "say_parts": lambda self, *a, **k: None,
                            "fail": lambda self, msg, **v: setattr(self, "error", msg.format(**v))})
prog = prog_type()
importer.run_digikam_import(str(dk_path), prog)

check("import finished without failing", prog.state != "error", prog.error)
r = con.execute("SELECT rating, description, lat, lng FROM photos WHERE filename='a.jpg'").fetchone()
check("the photo was imported", r is not None)
check("rating was imported", r and r["rating"] == 4, dict(r) if r else None)
check("caption was imported", r and r["description"] == "A day at the lake")
check("GPS was imported", r and abs(r["lat"] - 32.05) < 1e-6 and abs(r["lng"] - 34.78) < 1e-6)
tags = {x["name"] for x in con.execute(
    "SELECT t.name FROM tags t JOIN photo_tags pt ON pt.tag_id=t.id JOIN photos p ON p.id=pt.photo_id WHERE p.filename='a.jpg'")}
check("the regular tag was imported as a tag, not a person", tags == {"Nature"}, tags)
people = {x["name"] for x in con.execute(
    "SELECT pe.name FROM people pe JOIN photo_people pp ON pp.person_id=pe.id JOIN photos p ON p.id=pp.photo_id WHERE p.filename='a.jpg'")}
check("the person tag became a person, not a regular tag", people == {"Danny"}, people)
check("the missing file was not imported", con.execute("SELECT 1 FROM photos WHERE filename='missing.jpg'").fetchone() is None)

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
