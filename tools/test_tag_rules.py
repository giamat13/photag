"""Linked keywords (#39: "HP bus = Knight bus") and automatic keywords by the hour of the day (#40: 7-17 = daytime).
Throw-away profile, the real server.

    py -3.12 tools/test_tag_rules.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_tagrules_test_"))
for d in ("appdata", "local", "home"):
    (tmp / d).mkdir()
os.environ.update({"APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
                   "HOME": str(tmp / "home"), "PHOTAG_NO_OPEN": "1"})
sys.path.insert(0, str(ROOT))

import _client  # noqa: E402  (tools/_client.py)

from app import db  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def at(hour):
    """A capture time whose LOCAL hour is `hour` (the rules use the local time of the machine)."""
    return int(time.mktime((2024, 5, 14, hour, 30, 0, 0, 0, -1)))


db.init_db()
con = db.connect()


def add(sha, hour):
    con.execute("INSERT INTO photos(sha256,filename,rel_path,mime,is_video,width,height,bytes,taken_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (sha, sha + ".jpg", sha + ".jpg", "image/jpeg", 0, 10, 10, 1, at(hour)))
    con.commit()
    return con.execute("SELECT id FROM photos WHERE sha256=?", (sha,)).fetchone()["id"]


p3am, p12, p20 = add("p3am", 3), add("p12", 12), add("p20", 20)

srv, cl = _client.start_server(8873, {**os.environ, "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999"})


def names(pid):
    return sorted(r["name"] for r in db.connect().execute("SELECT t.name FROM tags t JOIN photo_tags pt ON pt.tag_id=t.id WHERE pt.photo_id=?", (pid,)))


def tag_id(name):
    return db.connect().execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()["id"]


def patch(body):
    return cl._do("PATCH", "/api/photos", json.dumps(body).encode(), "application/json")


try:
    # ---- #39 linked keywords
    r = cl.post("/api/tag-links", json={"a": "HP bus", "b": "Knight bus"})
    check("two keywords can be linked", r.status_code == 200 and len(r.json()["links"]) == 1, r.text[:150])
    patch({"ids": [p3am], "add_tags": ["HP bus"]})
    check("a photo that gets one of them gets the other too", names(p3am) == ["HP bus", "Knight bus"], names(p3am))
    patch({"ids": [p3am], "remove_tag_ids": [tag_id("Knight bus")]})
    check("...and loses both when one is removed", names(p3am) == [], names(p3am))

    patch({"ids": [p12], "add_tags": ["red"]})
    cl.post("/api/tag-links", json={"a": "red", "b": "crimson"})
    check("linking two keywords gives the other one to photos that already have one", names(p12) == ["crimson", "red"], names(p12))
    cl.post("/api/tag-links", json={"a": "crimson", "b": "scarlet"})
    check("a = b and b = c: the photo has all three", names(p12) == ["crimson", "red", "scarlet"], names(p12))
    patch({"ids": [p20], "add_tags": ["scarlet"]})
    check("...in whichever one it was added", names(p20) == ["crimson", "red", "scarlet"], names(p20))
    bad = cl.post("/api/tag-links", json={"a": "same", "b": "SAME"})
    check("a keyword cannot be linked to itself", bad.status_code == 400, bad.status_code)
    links = cl.get("/api/tag-links").json()["links"]
    check("the list of links has them with their names", {(x["a_name"], x["b_name"]) for x in links} >= {("HP bus", "Knight bus")} and len(links) == 3, links)
    one = next(x for x in links if {x["a_name"], x["b_name"]} == {"red", "crimson"})
    cl.post("/api/tag-links/delete", json={"a": one["a"], "b": one["b"]})
    check("a link can be removed", len(cl.get("/api/tag-links").json()["links"]) == 2)

    # ---- #40 automatic keywords by the hour
    r = cl.post("/api/time-rules", json={"rules": [{"from": 7, "to": 17, "tag": "daytime"}, {"from": 22, "to": 5, "tag": "night"}]})
    check("rules are saved", r.status_code == 200 and len(r.json()["rules"]) == 2, r.text[:150])
    check("a photo taken at 12:30 got 'daytime'", "daytime" in names(p12), names(p12))
    check("a photo taken at 03:30 got 'night' (22 -> 5 goes over midnight)", "night" in names(p3am) and "daytime" not in names(p3am), names(p3am))
    check("a photo taken at 20:30 got neither", "daytime" not in names(p20) and "night" not in names(p20), names(p20))
    late = add("late", 23)
    cl.get("/api/tags")
    check("a photo that comes in later gets its keyword when the list is loaded", names(late) == ["night"], names(late))
    check("the keywords of the rules show in the keyword list", {"daytime", "night"} <= {x["name"] for x in cl.get("/api/tags").json()})
    cl.post("/api/time-rules", json={"rules": [{"from": 7, "to": 17, "tag": "day"}]})
    check("changing a rule takes the old keywords away and gives the new one", "daytime" not in names(p12) and "night" not in names(late) and "day" in names(p12), (names(p12), names(late)))
    check("the rules can be read back", cl.get("/api/time-rules").json()["rules"] == [{"from": 7, "to": 17, "tag": "day"}])
    bad = cl.post("/api/time-rules", json={"rules": [{"from": 25, "to": 3, "tag": "x"}]})
    check("an hour outside 0-23 is refused", bad.status_code == 400, bad.status_code)
    bad = cl.post("/api/time-rules", json={"rules": [{"from": 1, "to": 3, "tag": " "}]})
    check("a rule without a keyword is refused", bad.status_code == 400, bad.status_code)
    cl.post("/api/time-rules", json={"rules": []})
    check("no rules: no automatic keywords are left", "day" not in names(p12) and names(late) == [], (names(p12), names(late)))
    check("keywords the user typed are untouched by all of this", "red" in names(p12), names(p12))
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
