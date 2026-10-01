"""HTTPS for the packaged app.

A freshly installed Windows may not have downloaded every root certificate yet (Windows fetches them on demand when a
browser or system component needs one), and Python only sees the ones already in the store -- which made the update
check fail with CERTIFICATE_VERIFY_FAILED on a clean machine. The bundled certifi list is added to the system store.
"""
import ssl

_ctx = None


def ssl_context() -> ssl.SSLContext:
    global _ctx
    if _ctx is None:
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except Exception:
            pass                       # fall back to the system store alone
        _ctx = ctx
    return _ctx
