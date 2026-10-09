"""Find a place by name (a city, an address, a country) for "search by place" in Advanced Search.

Only the text the user typed is sent, to OpenStreetMap's Nominatim, and only when they press "Find". The answer is a
point and a bounding box; the photos are then filtered on their own GPS positions on this computer.
PHOTAG_GEOCODE_URL replaces the server (tests use a local fake).
"""
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request

from .net import ssl_context
from .version import __version__

URL = "https://nominatim.openstreetmap.org/search"


class NotFound(Exception):
    pass


class Offline(Exception):
    pass


def _km(s, n, w, e) -> float:
    """Diagonal of the box in km."""
    R = math.pi / 180
    a = math.sin((n - s) * R / 2) ** 2 + math.cos(s * R) * math.cos(n * R) * math.sin((e - w) * R / 2) ** 2
    return 12742 * math.asin(min(1, math.sqrt(a)))


def find(query: str, lang: str = "en") -> dict:
    q = " ".join((query or "").split())[:200]
    if not q:
        raise NotFound()
    url = os.environ.get("PHOTAG_GEOCODE_URL") or URL
    url += ("&" if "?" in url else "?") + urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": 1, "accept-language": lang or "en"})
    req = urllib.request.Request(url, headers={"User-Agent": f"photag/{__version__} (https://github.com/giamat13/photag)"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl_context()) as r:
            rows = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise Offline()
    if not rows:
        raise NotFound()
    r = rows[0]
    lat, lng = float(r["lat"]), float(r["lon"])
    out = {"name": str(r.get("display_name") or q)[:200], "lat": lat, "lng": lng, "box": None}
    try:
        s, n, w, e = (float(x) for x in r["boundingbox"])           # Nominatim: south, north, west, east
        if _km(s, n, w, e) > 4:                                     # a point / an address: a circle is enough; a city or a country: its box
            out["box"] = [s, n, w, e]
    except (KeyError, TypeError, ValueError):
        pass
    return out
