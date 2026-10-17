"""Test: one search box for people, year and meaning ("Dana 2023 beach"). The usual search splits what is typed into the people it
names (a whole name or one word of it), years and plain words; every part must match. /api/search/smart adds the meaning search
for the words left (here without the CLIP model, so it falls back to the text match). Throw-away profile.

    py -3.12 tools/test_smart_query.py
"""
import os
import sqlite3
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
PORT = 8797
tmp = Path(tempfile.mkdtemp(prefix="photag_squery_test_"))
for v in ("appdata", "local", "home", "lib"):
    (tmp / v).mkdir()
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999"}
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from _client import start_server  # noqa: E402

srv, cl = start_server(PORT, env)
try:
    cl.post("/api/settings/library", json={"path": str(tmp / "lib")})
    files = [str(p) for p in sorted((ROOT / "tools" / "sandbox" / "photos").iterdir()) if p.suffix.lower() == ".jpg"]
    cl.post("/api/import-folder", json={"paths": files, "keywords": [], "album": None})
    while cl.get("/api/job/import").json()["state"] not in ("done", "error"):
        time.sleep(0.3)
    photos = cl.get("/api/photos?limit=999").json()
    ids = [p["id"] for p in photos]
    check("photos imported", len(ids) >= 6, len(ids))

    dbs = list(tmp.rglob("*.db")) + list(tmp.rglob("*.sqlite*"))
    dbf = next((d for d in dbs if "photag" in d.name.lower() or "catalog" in d.name.lower()), dbs[0] if dbs else None)
    con = sqlite3.connect(str(dbf))
    con.execute("INSERT INTO people(id,name,source) VALUES(1,'Dana Levi','manual'),(2,'Ron','manual'),(3,'Sam Cohen','manual'),(4,'Sam Mor','manual')")
    for i in ids[:4]:
        con.execute("INSERT INTO photo_people(photo_id,person_id,source) VALUES(?,1,'manual')", (i,))
    for i in ids[3:6]:
        con.execute("INSERT INTO photo_people(photo_id,person_id,source) VALUES(?,2,'manual')", (i,))
    con.execute("INSERT INTO photo_people(photo_id,person_id,source) VALUES(?,3,'manual')", (ids[0],))
    con.execute("INSERT INTO photo_people(photo_id,person_id,source) VALUES(?,4,'manual')", (ids[1],))
    con.commit()
    con.close()

    def q(text):
        return {p["id"] for p in cl.get("/api/photos?limit=999&q=" + urllib.parse.quote(text)).json()}

    check("a first name finds the person's photos", q("Dana") == set(ids[:4]), q("Dana"))
    check("a whole name finds them too", q("Dana Levi") == set(ids[:4]))
    check("the last name alone (one person has it) works", q("levi") == set(ids[:4]))
    check("another person", q("Ron") == set(ids[3:6]))
    check("two people: only the photos with both", q("Dana Ron") == {ids[3]}, q("Dana Ron"))
    check("a word shared by two people is not taken as one person, but still matches both through the text",
          q("Sam") == {ids[0], ids[1]}, q("Sam"))
    year = next(p for p in photos if p["id"] == ids[0] and p["taken_at"])
    import time as _t
    y = _t.gmtime(year["taken_at"]).tm_year
    in_year = {p["id"] for p in photos if p["taken_at"] and _t.gmtime(p["taken_at"]).tm_year == y}
    check("a year alone", q(str(y)) == in_year, (y, len(in_year)))
    check("a person and a year", q(f"Dana {y}") == set(ids[:4]) & in_year, q(f"Dana {y}"))
    check("a person and a year with no photos", q("Dana 1905") == set())
    name0 = photos[[p["id"] for p in photos].index(ids[0])]["filename"].rsplit(".", 1)[0]
    check("a file name word still works", ids[0] in q(name0))
    check("a person and a word that is not in the photo's text: nothing", q("Ron " + name0) == (set(ids[3:6]) & q(name0)))
    check("an unknown word matches nothing", q("zzzqqq") == set())
    check("an empty search has no filter", len(q("")) == len(ids))

    r = cl.get("/api/search/smart?q=" + urllib.parse.quote(f"Dana {y}")).json()
    check("smart search: people and years are understood", r["people"] == ["Dana Levi"] and r["years"] == [y] and r["rest"] == [], r)
    check("...and its results are the photos of the person in that year", {x["id"] for x in r["results"]} == set(ids[:4]) & in_year)
    r = cl.get("/api/search/smart?q=" + urllib.parse.quote(f"Dana {name0}")).json()
    check("smart search with a word and no model: the text match, meaning off", r["meaning"] is False and {x["id"] for x in r["results"]} <= set(ids[:4]), r)
    check("smart search needs a query", cl.get("/api/search/smart?q=%20").status_code == 400)
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
