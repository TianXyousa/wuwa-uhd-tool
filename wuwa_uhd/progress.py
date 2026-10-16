"""Throttled byte progress for long local reads and copies."""
import time


class FileProgress:
    def __init__(self, event, phase, path, total):
        self.event, self.phase, self.name, self.total = event, phase, path.name, total
        self.started = time.monotonic()
        self.last = self.started
        self.emit(0)

    def emit(self, current):
        now = time.monotonic()
        self.last = now
        self.event({"kind": "io_progress", "phase": self.phase, "file": self.name,
                    "current": current, "total": self.total,
                    "rate": current / max(0.001, now - self.started)})

    def __call__(self, current):
        if current == self.total or time.monotonic() - self.last >= 0.2:
            self.emit(current)
