"""Smart collections: saved rules that are evaluated against the catalog every time they are opened, so a collection
such as "everyone called X + 2024 + 4 stars and up" fills itself as photos are imported or rated.

A rule set is the JSON `criteria` of a saved search (table saved_searches). The older Advanced Search keys (from, to, kind,
exts, minMB, maxMB, place, cameras, lenses, minFocal, maxFocal) keep working, as do the ones that mirror the Library Filter
(q + qf 'any'|'name', flags ['pick'|'none'|'rej'], rating + ratingOp, colors, edited, months ['01'..'12'], orients
['landscape'|'portrait'|'square'|'unknown']), and more: keywords (comma text) + keywordsAll, kw / gps / faces ('has'|'none'),
fav, persons [ids], albumIds [ids], weekdays [0 = Sunday .. 6], hourFrom / hourTo, minMP / maxMP (megapixels), addedFrom /
addedTo (when it came into the library), score (quality, at least). None of them makes a search a smart collection. A place is a circle
(lat, lng, km) or a box [south, north, west, east] (a city / country found by name, or an area drawn on the map). The smart keys are:
  people [ids] + peopleAll   photos of these people (detected faces or Google people tags); any of them, or all
  tags [names] + tagsAll     keywords (any / all), case-insensitive
  years [n, ...]             capture year
  minRating, favorite, flag ('pick'|'reject'), labels [..], minScore, hasPlace, text
camera/lens come from EXIF (app/images.py exif_info): cameras/lenses match the model text exactly (case-insensitive),
minFocal/maxFocal compare the real focal length in mm.
"""
import json
import math
import time

SMART_KEYS = ("people", "tags", "years", "minRating", "favorite", "flag", "labels", "minScore", "hasPlace", "text")


def is_smart(c: dict) -> bool:
    return bool(c.get("smart")) or any(c.get(k) for k in SMART_KEYS)


def _km(a_lat, a_lng, b_lat, b_lng) -> float:
    R = math.pi / 180
    a = math.sin((b_lat - a_lat) * R / 2) ** 2 + math.cos(a_lat * R) * math.cos(b_lat * R) * math.sin((b_lng - a_lng) * R / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(a)))


def _ts(day: str, end: bool):
    try:
        t = time.strptime(day, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    return int(time.mktime(t)) + (86399 if end else 0)


def query_ids(con, c: dict) -> list[int]:
    """Ids (newest first) of the photos that satisfy every rule. An empty rule set matches nothing (never 'everything')."""
    where, args = ["p.trashed=0"], []
    ruled = False
    cameras = [str(x).strip() for x in (c.get("cameras") or []) if str(x).strip()]
    if cameras:
        where.append(f"p.camera_model COLLATE NOCASE IN ({','.join('?' * len(cameras))})"); args += cameras; ruled = True
    lenses = [str(x).strip() for x in (c.get("lenses") or []) if str(x).strip()]
    if lenses:
        where.append(f"p.lens COLLATE NOCASE IN ({','.join('?' * len(lenses))})"); args += lenses; ruled = True
    if c.get("minFocal"):
        where.append("p.focal_length>=?"); args.append(float(c["minFocal"])); ruled = True
    if c.get("maxFocal"):
        where.append("p.focal_length<=?"); args.append(float(c["maxFocal"])); ruled = True
    if c.get("from") and _ts(c["from"], False) is not None:
        where.append("p.taken_at>=?"); args.append(_ts(c["from"], False)); ruled = True
    if c.get("to") and _ts(c["to"], True) is not None:
        where.append("p.taken_at<=?"); args.append(_ts(c["to"], True)); ruled = True
    if c.get("kind") in ("photo", "video"):
        where.append("p.is_video=?"); args.append(1 if c["kind"] == "video" else 0); ruled = True
    exts = [str(x).lower().lstrip(".") for x in (c.get("exts") or []) if str(x).strip()]
    if exts:
        where.append("(" + " OR ".join("LOWER(p.filename) LIKE ?" for _ in exts) + ")")
        args += ["%." + e.replace("%", "").replace("_", "") for e in exts]; ruled = True
    if c.get("minMB"):
        where.append("p.bytes>=?"); args.append(float(c["minMB"]) * 1048576); ruled = True
    if c.get("maxMB"):
        where.append("p.bytes<=?"); args.append(float(c["maxMB"]) * 1048576); ruled = True
    people = [int(x) for x in (c.get("people") or [])]
    if people:
        conds = ["(p.id IN (SELECT photo_id FROM faces WHERE person_id=?) OR p.id IN (SELECT photo_id FROM photo_people WHERE person_id=?))"] * len(people)
        where.append("(" + (" AND " if c.get("peopleAll") else " OR ").join(conds) + ")")
        for pid in people:
            args += [pid, pid]
        ruled = True
    tags = [str(x).strip() for x in (c.get("tags") or []) if str(x).strip()]
    if tags:
        conds = ["p.id IN (SELECT pt.photo_id FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id WHERE t.name=? COLLATE NOCASE)"] * len(tags)
        where.append("(" + (" AND " if c.get("tagsAll") else " OR ").join(conds) + ")")
        args += tags; ruled = True
    years = [int(y) for y in (c.get("years") or [])]
    if years:
        where.append(f"CAST(strftime('%Y', p.taken_at, 'unixepoch', 'localtime') AS INT) IN ({','.join('?' * len(years))})")
        args += years; ruled = True
    if c.get("minRating"):
        where.append("p.rating>=?"); args.append(int(c["minRating"])); ruled = True
    if c.get("favorite"):
        where.append("p.favorited=1"); ruled = True
    if c.get("flag") in ("pick", "reject"):
        where.append("p.flag=?"); args.append(1 if c["flag"] == "pick" else -1); ruled = True
    labels = [x for x in (c.get("labels") or []) if x]
    if labels:
        where.append(f"p.label IN ({','.join('?' * len(labels))})"); args += labels; ruled = True
    if c.get("minScore"):
        where.append("p.id IN (SELECT photo_id FROM photo_analysis WHERE score>=?)"); args.append(int(c["minScore"])); ruled = True
    if c.get("hasPlace"):
        where.append("p.lat IS NOT NULL AND p.lng IS NOT NULL"); ruled = True
    if (c.get("text") or "").strip():
        where.append("(p.filename LIKE ? OR p.description LIKE ?)"); args += [f"%{c['text'].strip()}%"] * 2; ruled = True
    q = (c.get("q") or "").strip()
    if q:
        if c.get("qf") == "name":
            where.append("p.filename LIKE ?"); args.append(f"%{q}%")
        else:                                                    # any searchable field: the same as the Library Filter's text search
            where.append("(p.filename LIKE ? OR p.description LIKE ? "
                         "OR p.id IN (SELECT photo_id FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id WHERE t.name LIKE ?) "
                         "OR p.id IN (SELECT pp.photo_id FROM photo_people pp JOIN people pe ON pe.id=pp.person_id WHERE pe.name LIKE ?))")
            args += [f"%{q}%"] * 4
        ruled = True
    flags = [x for x in (c.get("flags") or []) if x in ("pick", "none", "rej")]
    if flags:
        conds = [{"pick": "p.flag=1", "rej": "p.flag=-1", "none": "(p.flag IS NULL OR p.flag=0)"}[x] for x in flags]
        where.append("(" + " OR ".join(conds) + ")"); ruled = True
    if c.get("rating") and c.get("ratingOp") in (">=", "<=", "="):
        where.append(f"COALESCE(p.rating,0){c['ratingOp']}?"); args.append(int(c["rating"])); ruled = True
    colors = [x for x in (c.get("colors") or []) if x]
    if colors:
        real = [x for x in colors if x != "none"]
        conds = ([f"p.label IN ({','.join('?' * len(real))})"] if real else []) + (["(p.label IS NULL OR p.label='')"] if "none" in colors else [])
        where.append("(" + " OR ".join(conds) + ")"); args += real; ruled = True
    if c.get("edited"):
        where.append("p.edited=1"); ruled = True
    months = [int(m) for m in (c.get("months") or []) if str(m).isdigit()]
    if months:
        where.append(f"CAST(strftime('%m', p.taken_at, 'unixepoch', 'localtime') AS INT) IN ({','.join('?' * len(months))})")
        args += months; ruled = True
    orients = [x for x in (c.get("orients") or []) if x in ("landscape", "portrait", "square", "unknown")]
    if orients:
        conds = [{"landscape": "p.width>p.height*1.05", "portrait": "p.height>p.width*1.05",
                  "square": "(p.width>0 AND p.height>0 AND p.width<=p.height*1.05 AND p.height<=p.width*1.05)",
                  "unknown": "(p.width IS NULL OR p.height IS NULL OR p.width=0 OR p.height=0)"}[x] for x in orients]
        where.append("(" + " OR ".join(conds) + ")"); ruled = True
    kws = [x.strip() for x in str(c.get("keywords") or "").split(",") if x.strip()]
    if kws:
        conds = ["p.id IN (SELECT pt.photo_id FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id WHERE t.name=? COLLATE NOCASE)"] * len(kws)
        where.append("(" + (" AND " if c.get("keywordsAll") else " OR ").join(conds) + ")"); args += kws; ruled = True
    if c.get("kw") in ("has", "none"):
        where.append(("" if c["kw"] == "has" else "NOT ") + "EXISTS(SELECT 1 FROM photo_tags pt WHERE pt.photo_id=p.id)"); ruled = True
    if c.get("gps") in ("has", "none"):
        where.append("p.lat IS NOT NULL AND p.lng IS NOT NULL" if c["gps"] == "has" else "(p.lat IS NULL OR p.lng IS NULL)"); ruled = True
    if c.get("faces") in ("has", "none"):
        where.append(("" if c["faces"] == "has" else "NOT ") + "EXISTS(SELECT 1 FROM faces f WHERE f.photo_id=p.id)"); ruled = True
    if c.get("fav"):
        where.append("p.favorited=1"); ruled = True
    persons = [int(x) for x in (c.get("persons") or []) if str(x).lstrip("-").isdigit()]
    if persons:
        where.append("(" + " OR ".join(["(p.id IN (SELECT photo_id FROM faces WHERE person_id=?) OR p.id IN (SELECT photo_id FROM photo_people WHERE person_id=?))"] * len(persons)) + ")")
        for pid in persons:
            args += [pid, pid]
        ruled = True
    albums = [int(x) for x in (c.get("albumIds") or []) if str(x).lstrip("-").isdigit()]
    if albums:
        where.append(f"p.id IN (SELECT photo_id FROM photo_albums WHERE album_id IN ({','.join('?' * len(albums))}))"); args += albums; ruled = True
    weekdays = [int(x) for x in (c.get("weekdays") or []) if str(x).isdigit() and 0 <= int(x) <= 6]
    if weekdays:
        where.append(f"CAST(strftime('%w', p.taken_at, 'unixepoch', 'localtime') AS INT) IN ({','.join('?' * len(weekdays))})"); args += weekdays; ruled = True
    h1, h2 = c.get("hourFrom"), c.get("hourTo")
    h1 = int(h1) if str(h1 if h1 is not None else "").isdigit() else None
    h2 = int(h2) if str(h2 if h2 is not None else "").isdigit() else None
    if h1 is not None or h2 is not None:
        hour = "CAST(strftime('%H', p.taken_at, 'unixepoch', 'localtime') AS INT)"
        if h1 is not None and h2 is not None and h1 > h2:           # 22 -> 5: over midnight
            where.append(f"({hour}>=? OR {hour}<=?)"); args += [h1, h2]
        else:
            if h1 is not None:
                where.append(f"{hour}>=?"); args.append(h1)
            if h2 is not None:
                where.append(f"{hour}<=?"); args.append(h2)
        where.append("p.taken_at IS NOT NULL"); ruled = True
    if c.get("minMP"):
        where.append("p.width*p.height>=?"); args.append(float(c["minMP"]) * 1e6); ruled = True
    if c.get("maxMP"):
        where.append("p.width*p.height<=?"); args.append(float(c["maxMP"]) * 1e6); ruled = True
    if c.get("addedFrom") and _ts(c["addedFrom"], False) is not None:
        where.append("p.imported_at>=?"); args.append(_ts(c["addedFrom"], False)); ruled = True
    if c.get("addedTo") and _ts(c["addedTo"], True) is not None:
        where.append("p.imported_at<=?"); args.append(_ts(c["addedTo"], True)); ruled = True
    if c.get("score"):
        where.append("p.id IN (SELECT photo_id FROM photo_analysis WHERE score>=?)"); args.append(int(c["score"])); ruled = True
    pl = c.get("place")
    if pl:
        where.append("p.lat IS NOT NULL AND p.lng IS NOT NULL"); ruled = True
    if not ruled:
        return []
    rows = con.execute(f"SELECT p.id, p.lat, p.lng FROM photos p WHERE {' AND '.join(where)} ORDER BY p.taken_at DESC, p.id DESC", args).fetchall()
    if pl and pl.get("box"):
        s, n, w, e = (float(x) for x in pl["box"])
        rows = [r for r in rows if s <= r["lat"] <= n and (w <= r["lng"] <= e if w <= e else r["lng"] >= w or r["lng"] <= e)]
    elif pl:
        rows = [r for r in rows if _km(pl["lat"], pl["lng"], r["lat"], r["lng"]) <= float(pl["km"])]
    return [r["id"] for r in rows]


def parse(raw) -> dict | None:
    try:
        c = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return c if isinstance(c, dict) else None


def google_people_rules(con, min_photos: int = 5) -> list[dict]:
    """One rule per person Google Photos tagged in the Takeout (people with at least `min_photos` photos)."""
    out = []
    for r in con.execute(
            "SELECT pe.id, pe.name, COUNT(DISTINCT pp.photo_id) n FROM people pe JOIN photo_people pp ON pp.person_id=pe.id "
            "WHERE pp.source='takeout' GROUP BY pe.id HAVING n>=? ORDER BY n DESC", (min_photos,)):
        out.append({"name": r["name"], "criteria": {"smart": 1, "people": [r["id"]], "peopleAll": 0}, "n": r["n"]})
    return out
