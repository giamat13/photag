"""Smart collections: saved rules that are evaluated against the catalog every time they are opened, so a collection
such as "everyone called X + 2024 + 4 stars and up" fills itself as photos are imported or rated.

A rule set is the JSON `criteria` of a saved search (table saved_searches). The older Advanced Search keys (from, to, kind,
exts, minMB, maxMB, place) keep working; the smart keys are:
  people [ids] + peopleAll   photos of these people (detected faces or Google people tags); any of them, or all
  tags [names] + tagsAll     keywords (any / all), case-insensitive
  years [n, ...]             capture year
  minRating, favorite, flag ('pick'|'reject'), labels [..], minScore, hasPlace, text
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
    pl = c.get("place")
    if pl:
        where.append("p.lat IS NOT NULL AND p.lng IS NOT NULL"); ruled = True
    if not ruled:
        return []
    rows = con.execute(f"SELECT p.id, p.lat, p.lng FROM photos p WHERE {' AND '.join(where)} ORDER BY p.taken_at DESC, p.id DESC", args).fetchall()
    if pl:
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
