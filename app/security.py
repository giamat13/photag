"""Keep photag's local web server for photag's own window.

The app talks to itself over http://127.0.0.1:<port>. Any web page open in the user's browser could also try to reach that
address, so every request is checked:

  * Host must be this machine (127.0.0.1, localhost or ::1)         -> stops "DNS rebinding" (an attacker's name that points at 127.0.0.1)
  * a request that changes something (POST / PUT / PATCH / DELETE) must come from our own origin, never from another site
  * requests the browser marks "cross-site" are refused for the API

Programs that are not browsers (the scheduled backup, tests, curl) send no Origin and are allowed on the same machine as before.
"""
from urllib.parse import urlsplit

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def host_name(host_header: str) -> str:
    h = (host_header or "").strip().lower()
    if h.startswith("["):                    # [::1]:8756
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def origin_is_ours(origin: str, host_header: str) -> bool:
    try:
        o = urlsplit(origin)
    except ValueError:
        return False
    return o.scheme == "http" and (o.netloc or "").lower() == (host_header or "").strip().lower() and host_name(o.netloc) in ALLOWED_HOSTS


class LocalOnlyMiddleware:
    """Pure ASGI middleware (no extra dependency, streams untouched)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        problem = None
        if host_name(host) not in ALLOWED_HOSTS:
            problem = (400, "Invalid host")
        elif scope["method"] not in SAFE_METHODS:
            origin = headers.get("origin")
            if origin is not None and not origin_is_ours(origin, host):
                problem = (403, "Cross-site request refused")
            elif headers.get("sec-fetch-site") == "cross-site":
                problem = (403, "Cross-site request refused")
        elif scope["path"].startswith("/api/") and headers.get("sec-fetch-site") == "cross-site":
            problem = (403, "Cross-site request refused")
        if problem:
            body = problem[1].encode()
            await send({"type": "http.response.start", "status": problem[0],
                        "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        return await self.app(scope, receive, send)
