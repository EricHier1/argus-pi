"""Trim uvicorn access-log spam from the endpoints the web UI polls on a timer.

The live view re-fetches /frame several times a second per camera, and the
dashboard polls /api/status; their 200s bury everything useful in the log. This
installs a filter on uvicorn's access logger that drops (or samples) those lines
while leaving every other request — navigation, mutations, errors — logged
normally. Tune with ARGUS_ACCESS_LOG_EVERY (see config.ACCESS_LOG_EVERY)."""
import logging

from . import config

# Endpoints the browser hits on a repeating timer (see web/feeds.js, web/main.js).
NOISY_PREFIXES = ("/frame", "/stream", "/api/status")


class _SampleAccessLog(logging.Filter):
    def __init__(self, every):
        super().__init__()
        self.every = every
        self._counts = {}

    def filter(self, record):
        # uvicorn.access log args: (client_addr, method, path, http_version, status).
        # If that shape ever changes, fail open (log the line) rather than hide it.
        args = record.args
        if not args or len(args) < 3:
            return True
        path = str(args[2])
        for p in NOISY_PREFIXES:
            if path.startswith(p):
                if self.every <= 0:
                    return False                 # drop entirely
                n = self._counts.get(p, 0) + 1
                self._counts[p] = n
                return n % self.every == 0       # keep 1 of every N
        return True                              # everything else logs


def install():
    """Attach the sampling filter to uvicorn's access logger (idempotent)."""
    log = logging.getLogger("uvicorn.access")
    if any(isinstance(f, _SampleAccessLog) for f in log.filters):
        return
    log.addFilter(_SampleAccessLog(config.ACCESS_LOG_EVERY))
