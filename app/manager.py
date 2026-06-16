"""Manages one or more CameraWorkers (multi-camera support).

Workers are keyed by their source string. The first configured source is the
"primary" (used by endpoints that don't specify a source). Each worker loads its
own model, so memory scales with the number of cameras — fine for a handful."""
import threading

from . import config
from .capture import CameraWorker


class CameraManager:
    def __init__(self):
        self._workers = {}          # source(str) -> CameraWorker (insertion-ordered)
        self._lock = threading.Lock()

    def start(self, sources=None):
        for s in (sources if sources is not None else config.SOURCES):
            self.add(str(s))

    def add(self, source):
        source = str(source)
        with self._lock:
            if source in self._workers:
                return self._workers[source]
            w = CameraWorker(source)
            self._workers[source] = w
        w.start()
        return w

    def remove(self, source):
        source = str(source)
        with self._lock:
            w = self._workers.pop(source, None)
        if w:
            w.stop()
        return w is not None

    def get(self, source=None):
        """A specific worker, or the primary (first) when source is None."""
        with self._lock:
            if source is None:
                return next(iter(self._workers.values()), None)
            return self._workers.get(str(source))

    def sources(self):
        with self._lock:
            return list(self._workers.keys())

    def statuses(self):
        with self._lock:
            return [w.status for w in self._workers.values()]

    def pause_all(self):
        for w in list(self._workers.values()):
            w.pause()

    def resume_all(self):
        for w in list(self._workers.values()):
            w.resume()

    def stop_all(self):
        for w in list(self._workers.values()):
            w.stop()


manager = CameraManager()
