"""A synchronous stand-in for the handful of concurrent.futures names this app uses
(ThreadPoolExecutor, as_completed), for when that stdlib module isn't available.

Why it can be missing: a code update (see codeboot.py) can only swap pure .py source -- it can't add
a module to an old install's frozen executable that never bundled it. Rather than crash a background
job (backup, photo analysis, AI tagging) with an unhandled exception, those modules fall back to this
and just run the same work sequentially, one item at a time, in the calling thread.
"""


class _Future:
    def __init__(self, fn, *args, **kwargs):
        try:
            self._result, self._exc = fn(*args, **kwargs), None
        except Exception as e:
            self._result, self._exc = None, e

    def result(self):
        if self._exc is not None:
            raise self._exc
        return self._result


class ThreadPoolExecutor:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.shutdown()

    def submit(self, fn, *args, **kwargs):
        return _Future(fn, *args, **kwargs)

    def map(self, fn, items):
        return (fn(x) for x in items)

    def shutdown(self, wait=True, cancel_futures=False):
        pass


def as_completed(futures):
    """Every future here already ran synchronously inside submit() -- just hand them back."""
    return iter(futures)
