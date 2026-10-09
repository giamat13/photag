"""A small thread pool with the same names as concurrent.futures (ThreadPoolExecutor, as_completed) that still works
after the window was closed in background mode.

Why not concurrent.futures: its pool is shut down by Python (threading._shutdown) as soon as the main program ends, and
from then on `ThreadPoolExecutor` raises "can't register atexit after shutdown" / "cannot schedule new futures after
interpreter shutdown". With the icon next to the clock (background.py) the program lives on in an atexit handler, so AI
tagging, photo analysis and backups started from there failed. Plain daemon threads have no such limit.
"""
import threading
from collections import deque


class CancelledError(Exception):
    pass


class Future:
    def __init__(self):
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._state = "pending"            # pending / running / cancelled / finished
        self._result, self._exc = None, None
        self._cbs = []

    def cancel(self) -> bool:
        with self._lock:
            if self._state in ("running", "finished"):
                return self._state == "cancelled"
            self._state = "cancelled"
        self._finish()
        return True

    def cancelled(self) -> bool:
        return self._state == "cancelled"

    def done(self) -> bool:
        return self._done.is_set()

    def result(self):
        self._done.wait()
        if self._state == "cancelled":
            raise CancelledError()
        if self._exc is not None:
            raise self._exc
        return self._result

    def _run(self, fn, args, kwargs):
        with self._lock:
            if self._state != "pending":
                return
            self._state = "running"
        try:
            self._result = fn(*args, **kwargs)
        except BaseException as e:
            self._exc = e
        with self._lock:
            self._state = "finished"
        self._finish()

    def _finish(self):
        with self._lock:
            cbs, self._cbs = self._cbs, []
        self._done.set()
        for cb in cbs:
            cb(self)

    def _on_done(self, cb):
        with self._lock:
            if not self._done.is_set():
                self._cbs.append(cb)
                return
        cb(self)


class ThreadPoolExecutor:
    def __init__(self, max_workers=None, *args, **kwargs):
        self._max = max(1, max_workers or 4)
        self._cond = threading.Condition()
        self._queue = deque()
        self._threads = []
        self._idle = 0
        self._closing = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.shutdown(wait=True)
        return False

    def _worker(self):
        while True:
            with self._cond:
                while not self._queue and not self._closing:
                    self._idle += 1
                    self._cond.wait()
                    self._idle -= 1
                if not self._queue:
                    return
                fut, fn, args, kwargs = self._queue.popleft()
            fut._run(fn, args, kwargs)

    def submit(self, fn, *args, **kwargs) -> Future:
        fut = Future()
        with self._cond:
            if self._closing:
                raise RuntimeError("cannot schedule new futures after shutdown")
            self._queue.append((fut, fn, args, kwargs))
            if self._idle == 0 and len(self._threads) < self._max:
                t = threading.Thread(target=self._worker, daemon=True, name="photag-pool")
                self._threads.append(t)
                t.start()
            self._cond.notify()
        return fut

    def map(self, fn, items):
        futs = [self.submit(fn, x) for x in items]

        def gen():
            try:
                for f in futs:
                    yield f.result()
            finally:
                for f in futs:                  # the caller stopped early: do not start what is still waiting
                    f.cancel()
        return gen()

    def shutdown(self, wait=True, cancel_futures=False):
        with self._cond:
            self._closing = True
            if cancel_futures:
                pending = [q[0] for q in self._queue]
                self._queue.clear()
            else:
                pending = []
            self._cond.notify_all()
        for f in pending:
            f.cancel()
        if wait:
            for t in list(self._threads):
                if t is not threading.current_thread():
                    t.join()


def as_completed(futures):
    """Yield the futures as they finish (a cancelled one counts as finished)."""
    futs = list(futures)
    cond = threading.Condition()
    ready = deque()

    def cb(f):
        with cond:
            ready.append(f)
            cond.notify()
    for f in futs:
        f._on_done(cb)
    for _ in futs:
        with cond:
            while not ready:
                cond.wait()
            f = ready.popleft()
        yield f
