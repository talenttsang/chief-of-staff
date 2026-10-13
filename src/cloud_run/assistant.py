"""The hourly run's bookkeeping: its live progress, its summary, and how a failure is reported.

The run itself (main.do_run) works the assistant's own tasks (worker.py) and re-checks every connected
system, so the Access tab stays true.
"""

import os
import threading
from datetime import datetime, timezone

from store import utcnow_iso


class ReconnectNeeded(RuntimeError):
    """Man AI's Google token was revoked or expired; Connect Google again."""


def error_text(exc, limit=300):
    """The message plus where it happened, e.g. "... (at credentials.py:101, from google_apis.py:61)".

    A failed run's one-line summary then says where to look, without the full traceback.
    """
    import traceback

    frames = traceback.extract_tb(exc.__traceback__)
    where = ""
    if frames:
        here = os.path.dirname(os.path.abspath(__file__))
        spot = lambda f: f"{os.path.basename(f.filename)}:{f.lineno}"  # noqa: E731
        ours = [f for f in frames if os.path.dirname(os.path.abspath(f.filename)) == here]
        where = f" (at {spot(frames[-1])}"
        if ours and ours[-1] is not frames[-1]:
            where += f", from {spot(ours[-1])}"
        where += ")"
    return f"{str(exc)[:limit - len(where)]}{where}"


class Progress:
    """Live status of a run, kept in the store so whichever instance serves the UI can show it."""

    def __init__(self, store, trigger="manual"):
        self.store = store
        self._lock = threading.Lock()
        self.state = {"running": True, "trigger": trigger, "phase": "Starting", "started_at": utcnow_iso()}
        self._flush()

    def _flush(self):
        self.store.set_progress(dict(self.state, updated_at=utcnow_iso()))

    def phase(self, text, **fields):
        with self._lock:
            self.state.update(fields, phase=text)
            self._flush()

    def finish(self, error=""):
        with self._lock:
            self.state.update(running=False, phase="Failed" if error else "Done", finished_at=utcnow_iso(),
                              error=error[:500])
            self._flush()


def empty_summary(now=None):
    """A run's summary before anything is done."""
    now = now or datetime.now(timezone.utc)
    return {"started_at": now.isoformat(), "tasks_worked": [], "systems_connected": 0, "errors": []}
