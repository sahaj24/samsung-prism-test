import json
import os
import time
from pathlib import Path
from typing import Any


class Trace:
    """Append-only audit. Official telemetry includes every dispatched benchmark API call."""

    def __init__(self, room: str, directory: Path, official_path: Path | None = None):
        self.room = room
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "events.jsonl"
        self.official_path = official_path
        self.started = time.monotonic()

    @staticmethod
    def _append(path: Path, item: dict[str, Any]):
        # One append write avoids interleaving records from concurrent rooms.
        data = (json.dumps(item, ensure_ascii=False, default=str) + "\n").encode()
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    def event(self, kind: str, **data):
        self._append(self.path, {"event": kind, "room": self.room,
                                "elapsed_s": round(time.monotonic()-self.started, 6),
                                "wall_time": time.time(), **data})

    def tool(self, name, args, start, end, outcome):
        record = {"function": name, "args": args, "timestamp_start": start,
                  "timestamp_end": end, "outcome": outcome}
        self.event("tool_executed", **record)
        if self.official_path:
            self._append(self.official_path, {"room": self.room, "call": record})
