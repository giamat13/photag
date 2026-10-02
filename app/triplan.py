"""photag x triplan: connect to a triplan account, list the trips that account belongs to (read-only,
via Firestore's REST API using the signed-in user's own ID token -- governed by the same security
rules as the triplan web app itself, nothing special-cased for photag), and open a specific trip in
the browser for an album marked as a trip.

Uses triplan's own public Firebase Web API key (the same one its web app ships with; Firebase Web
API keys are not secret -- access is governed by Firestore security rules, not by hiding this key)
to call Firebase Auth's REST API directly, the same call triplan's own signInWithEmailAndPassword()
makes in the browser.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from . import config, keystore

API_KEY = "AIzaSyDQLgYhR0IzbeJ6WiFW6Pt-7C1yyTweVOk"          # triplan's public Firebase Web API key (config.js)
PROJECT_ID = "triplan-giamat13"                               # triplan's Firebase project id (config.js)
SIGN_IN_URL = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={API_KEY}"
FIRESTORE_QUERY_URL = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents:runQuery"
APP_URL = "https://giamat13.github.io/triplan/"   # GitHub Pages -- triplan's Firebase Hosting site was never deployed

_ERROR_MESSAGES = {
    "EMAIL_NOT_FOUND": "No triplan account with that email",
    "INVALID_PASSWORD": "Wrong password",
    "INVALID_LOGIN_CREDENTIALS": "Wrong email or password",
    "USER_DISABLED": "This triplan account was disabled",
    "TOO_MANY_ATTEMPTS_TRY_LATER": "Too many attempts -- try again later",
}


class TriplanError(Exception):
    pass


def sign_in(email: str, password: str, timeout: float = 20) -> dict:
    """Verifies the credentials against triplan's own Firebase project. Returns {uid, email, id_token}."""
    body = json.dumps({"email": email, "password": password, "returnSecureToken": True}).encode()
    req = urllib.request.Request(SIGN_IN_URL, body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            code = json.loads(e.read())["error"]["message"]
        except Exception:
            code = ""
        raise TriplanError(_ERROR_MESSAGES.get(code, code or f"triplan sign-in failed ({e.code})"))
    except urllib.error.URLError as e:
        raise TriplanError(str(e.reason)[:200])
    return {"uid": data["localId"], "email": data["email"], "id_token": data["idToken"]}


def status() -> dict:
    """{"connected": bool, "email": str | None} -- the password itself is never returned."""
    t = config.get_triplan()
    return {"connected": bool(t.get("email")), "email": t.get("email")}


def connect(email: str, password: str) -> dict:
    email = email.strip()
    info = sign_in(email, password)          # raises TriplanError on bad credentials
    config.set_triplan({"email": info["email"], "uid": info["uid"], "password": keystore.protect(password)})
    return status()


def disconnect():
    config.set_triplan({})


def _fs_value(v: dict):
    """A Firestore REST API typed value -> a plain Python value (only the types trip documents use)."""
    if "stringValue" in v:
        return v["stringValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "booleanValue" in v:
        return v["booleanValue"]
    if "arrayValue" in v:
        return [_fs_value(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v:
        return {k: _fs_value(x) for k, x in v["mapValue"].get("fields", {}).items()}
    return None


def _reauth() -> dict:
    """Re-signs-in with the stored credentials to get a fresh ID token (they expire after an hour,
    and photag doesn't keep a long-lived session -- this is only called when actually needed)."""
    t = config.get_triplan()
    if not t.get("email"):
        raise TriplanError("Not connected to triplan")
    password = keystore.unprotect(t["password"])
    return sign_in(t["email"], password)


def list_trips(timeout: float = 20) -> list[dict]:
    """The trips the connected account belongs to, same query triplan's own fbListMyTrips() runs --
    [{"id", "name", "start_date", "days"}, ...], newest first by trip id (Firestore default order)."""
    info = _reauth()
    body = json.dumps({
        "structuredQuery": {
            "from": [{"collectionId": "trips"}],
            "where": {"fieldFilter": {
                "field": {"fieldPath": f"members.{info['uid']}"},
                "op": "IN",
                "value": {"arrayValue": {"values": [{"stringValue": r} for r in ("owner", "editor", "viewer")]}},
            }},
        }
    }).encode()
    req = urllib.request.Request(FIRESTORE_QUERY_URL, body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {info['id_token']}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rows = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise TriplanError(f"Could not read your trips from triplan ({e.code})")
    except urllib.error.URLError as e:
        raise TriplanError(str(e.reason)[:200])
    out = []
    for row in rows:
        doc = row.get("document")
        if not doc:
            continue
        fields = {k: _fs_value(v) for k, v in doc.get("fields", {}).items()}
        out.append({
            "id": doc["name"].rsplit("/", 1)[-1],
            "name": fields.get("name") or "",
            "start_date": fields.get("startDate") or "",
            "days": len(fields.get("days") or []),
        })
    return out


def trip_url(trip_id: str | None) -> str:
    """The URL to open for a trip -- a specific trip (triplan reads ?trip=<id> and opens it directly
    if the signed-in account is a member) or just the app's home page if none is linked yet."""
    return f"{APP_URL}?trip={urllib.parse.quote(trip_id)}" if trip_id else APP_URL
