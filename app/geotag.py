"""Places for photos that have none (the camera has no GPS).

Two sources, both only SUGGEST: the user looks at the list and confirms it.
  * a GPX track (the trail an app on the phone recorded while walking / driving): the place where you were at the time of the picture
  * the pictures taken just before / after, that do have a place (the phone was in the same spot)

`taken_at` of a photo is the camera's wall-clock time written as if it were UTC (see images.py); a GPX file has real UTC times. So the
track is compared at `taken_at - offset`, where offset is the camera's distance from UTC (by default the one of this computer).
"""
import datetime
import re
import time

MAX_TRACK_POINTS = 2_000_000
_PT = re.compile(r"<(trkpt|wpt|rtept)\b([^>]*)>(.*?)</\1>", re.S)
_TIME = re.compile(r"<time>\s*([^<\s]+)\s*</time>")
_LAT = re.compile(r'\blat\s*=\s*["\']\s*(-?[0-9.]+)')
_LON = re.compile(r'\blon\s*=\s*["\']\s*(-?[0-9.]+)')


def _iso(s: str) -> float | None:
    s = s.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    m = re.match(r"(.*?)(\.\d+)?([+-]\d\d:?\d\d)?$", s)
    if not m:
        return None
    base, frac, tz = m.group(1), m.group(2) or "", m.group(3) or "+00:00"
    if re.fullmatch(r"[+-]\d{4}", tz):
        tz = tz[:3] + ":" + tz[3:]
    try:
        return datetime.datetime.fromisoformat(base + (frac[:7] if frac else "") + tz).timestamp()
    except ValueError:
        return None


def parse_gpx(text: str) -> list[tuple[float, float, float]]:
    """[(utc seconds, lat, lng)] of every track point (and way point) that has a time, oldest first."""
    pts = []
    for m in _PT.finditer(text or ""):
        a, b = _LAT.search(m.group(2)), _LON.search(m.group(2))
        t = _TIME.search(m.group(3))
        if not (a and b and t):
            continue
        ts = _iso(t.group(1))
        lat, lng = float(a.group(1)), float(b.group(1))
        if ts is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        pts.append((ts, lat, lng))
        if len(pts) >= MAX_TRACK_POINTS:
            break
    pts.sort()
    return pts


def local_offset_s(taken_at: int) -> int:
    """How far this computer's clock is from UTC around that time (the default for a camera set to the local time)."""
    try:
        return int(time.localtime(max(0, taken_at)).tm_gmtoff or 0)
    except (OSError, OverflowError, ValueError):
        return 0


def _search(times: list[float], x: float) -> int:      # first index with times[i] >= x
    lo, hi = 0, len(times)
    while lo < hi:
        mid = (lo + hi) // 2
        if times[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def from_track(photos, track, offset_min: int | None = None, max_gap_s: int = 900) -> list[dict]:
    """photos: [(id, taken_at)]. A place when the track has a point within max_gap_s of the picture (a straight line between two
    points when the picture lies between them, the nearer point otherwise)."""
    if not track:
        return []
    times = [p[0] for p in track]
    out = []
    for pid, taken in photos:
        off = local_offset_s(taken) if offset_min is None else int(offset_min) * 60
        t = taken - off
        i = _search(times, t)
        lo, hi = (track[i - 1] if i > 0 else None), (track[i] if i < len(track) else None)
        gap_lo = t - lo[0] if lo else None
        gap_hi = hi[0] - t if hi else None
        if lo and hi and gap_lo <= max_gap_s and gap_hi <= max_gap_s and hi[0] > lo[0]:
            f = (t - lo[0]) / (hi[0] - lo[0])
            out.append({"id": pid, "lat": round(lo[1] + (hi[1] - lo[1]) * f, 6), "lng": round(lo[2] + (hi[2] - lo[2]) * f, 6),
                        "gap_s": int(min(gap_lo, gap_hi)), "source": "gpx"})
            continue
        best = min([(g, p) for g, p in ((gap_lo, lo), (gap_hi, hi)) if p and g <= max_gap_s], key=lambda x: x[0], default=None)
        if best:
            out.append({"id": pid, "lat": round(best[1][1], 6), "lng": round(best[1][2], 6), "gap_s": int(best[0]), "source": "gpx"})
    return out


def from_neighbors(photos, placed, max_gap_s: int = 1800) -> list[dict]:
    """photos: [(id, taken_at)] without a place; placed: [(id, taken_at, lat, lng)] with one. The nearest in time wins."""
    placed = sorted(placed, key=lambda x: x[1])
    times = [p[1] for p in placed]
    out = []
    for pid, taken in photos:
        i = _search(times, taken)
        cand = [(abs(taken - placed[j][1]), placed[j]) for j in (i - 1, i) if 0 <= j < len(placed)]
        if not cand:
            continue
        gap, p = min(cand, key=lambda x: x[0])
        if gap <= max_gap_s:
            out.append({"id": pid, "lat": p[2], "lng": p[3], "gap_s": int(gap), "source": "photo", "from_id": p[0]})
    return out
