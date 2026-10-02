"""photag x triplan, step 1: connect to a triplan account and open triplan in the browser for an
album marked as a trip. Nothing is read from or written to triplan's Firestore yet -- this only
signs in (to prove the account works) and remembers the connection for next time.

Uses triplan's own public Firebase Web API key (the same one its web app ships with; Firebase Web
API keys are not secret -- access is governed by Firestore security rules, not by hiding this key)
to call Firebase Auth's REST API directly, the same call triplan's own signInWithEmailAndPassword()
makes in the browser.
"""
import json
import urllib.error
import urllib.request

from . import config, keystore

API_KEY = "AIzaSyDQLgYhR0IzbeJ6WiFW6Pt-7C1yyTweVOk"          # triplan's public Firebase Web API key (config.js)
SIGN_IN_URL = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={API_KEY}"
APP_URL = "https://triplan-giamat13.web.app"

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
    """Verifies the credentials against triplan's own Firebase project. Returns {uid, email}."""
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
    return {"uid": data["localId"], "email": data["email"]}


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
