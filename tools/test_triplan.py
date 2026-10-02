"""Test: app.triplan (photag x triplan, step 1 -- connect + remember the account) and the
albums.is_trip column it relies on. No real network call to Firebase: urllib is monkeypatched
to return crafted Firebase Auth REST responses, success and error alike.

    py -3.12 tools/test_triplan.py
"""
import io
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_triplan_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h"), ("HOME", "h")):
    (tmp / v).mkdir(exist_ok=True)
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import config, db, triplan  # noqa: E402

db.init_db()


class _FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _ok(_req, timeout=None):
    return _FakeResp(json.dumps({"localId": "uid123", "email": "me@example.com", "idToken": "tok"}).encode())


def _bad_password(_req, timeout=None):
    body = json.dumps({"error": {"message": "INVALID_PASSWORD"}}).encode()
    raise urllib.error.HTTPError("url", 400, "Bad Request", {}, io.BytesIO(body))


def _unknown_error(_req, timeout=None):
    body = json.dumps({"error": {"message": "SOME_NEW_FIREBASE_ERROR"}}).encode()
    raise urllib.error.HTTPError("url", 400, "Bad Request", {}, io.BytesIO(body))


_real_urlopen = urllib.request.urlopen

urllib.request.urlopen = _ok
r = triplan.sign_in("me@example.com", "right-password")
check("sign_in() on success returns uid, email and an id token",
      r == {"uid": "uid123", "email": "me@example.com", "id_token": "tok"}, r)

urllib.request.urlopen = _bad_password
try:
    triplan.sign_in("me@example.com", "wrong")
    check("sign_in() raises on a wrong password", False)
except triplan.TriplanError as e:
    check("sign_in() raises on a wrong password", str(e) == "Wrong password", str(e))

urllib.request.urlopen = _unknown_error
try:
    triplan.sign_in("me@example.com", "x")
    check("an unrecognized Firebase error code is still reported, not swallowed", False)
except triplan.TriplanError as e:
    check("an unrecognized Firebase error code is still reported, not swallowed", "SOME_NEW_FIREBASE_ERROR" in str(e), str(e))

check("not connected before connect() is called", triplan.status() == {"connected": False, "email": None})

urllib.request.urlopen = _ok
out = triplan.connect("me@example.com", "right-password")
check("connect() reports connected with the signed-in email", out == {"connected": True, "email": "me@example.com"}, out)
check("status() agrees", triplan.status() == {"connected": True, "email": "me@example.com"})
stored = config.get_triplan()
check("the password is never stored in the clear", "right-password" not in json.dumps(stored), stored)
check("the uid is stored", stored.get("uid") == "uid123", stored)

# list_trips(): re-authenticates with the stored (encrypted) password, then runs a Firestore
# structured query over /trips filtering on members.<uid> -- mock both calls.
_FS_RESPONSE = [
    {"document": {"name": "projects/x/databases/(default)/documents/trips/abc123", "fields": {
        "name": {"stringValue": "Rome"}, "startDate": {"stringValue": "2025-06-01"},
        "days": {"arrayValue": {"values": [{"mapValue": {"fields": {}}}, {"mapValue": {"fields": {}}}]}},
        "members": {"mapValue": {"fields": {"uid123": {"stringValue": "owner"}}}},
    }}},
    {"document": {"name": "projects/x/databases/(default)/documents/trips/def456", "fields": {
        "name": {"stringValue": "Untitled"},
    }}},
]
_calls = []


def _list_trips_mock(req, timeout=None):
    _calls.append(req.full_url)
    if "identitytoolkit" in req.full_url:
        return _ok(req, timeout)
    body = json.loads(req.data)
    assert body["structuredQuery"]["where"]["fieldFilter"]["field"]["fieldPath"] == "members.uid123"
    return _FakeResp(json.dumps(_FS_RESPONSE).encode())


urllib.request.urlopen = _list_trips_mock
trips = triplan.list_trips()
check("list_trips() queries Firestore filtered on the signed-in user's uid",
      len(_calls) == 2 and "runQuery" in _calls[1], _calls)
check("list_trips() returns both trips with id/name/start_date/days",
      trips == [{"id": "abc123", "name": "Rome", "start_date": "2025-06-01", "days": 2},
                {"id": "def456", "name": "Untitled", "start_date": "", "days": 0}], trips)

check("trip_url() with a trip id opens that specific trip", triplan.trip_url("abc123") == triplan.APP_URL + "?trip=abc123")
check("trip_url() with no trip id falls back to the app's home page", triplan.trip_url(None) == triplan.APP_URL)

triplan.disconnect()
check("disconnect() clears the connection", triplan.status() == {"connected": False, "email": None})

urllib.request.urlopen = _real_urlopen

con = db.connect()
aid = con.execute("INSERT INTO albums(name, kind, is_trip) VALUES('Test trip', 'album', 0)").lastrowid
check("a new album defaults to is_trip=0", con.execute("SELECT is_trip FROM albums WHERE id=?", (aid,)).fetchone()["is_trip"] == 0)
con.execute("UPDATE albums SET is_trip=1 WHERE id=?", (aid,))
con.commit()
check("is_trip can be set", con.execute("SELECT is_trip FROM albums WHERE id=?", (aid,)).fetchone()["is_trip"] == 1)
con.execute("UPDATE albums SET triplan_trip_id='abc123' WHERE id=?", (aid,))
con.commit()
check("triplan_trip_id can be set", con.execute("SELECT triplan_trip_id FROM albums WHERE id=?", (aid,)).fetchone()["triplan_trip_id"] == "abc123")

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
