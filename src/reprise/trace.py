import json
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

_context = ContextVar("reprise_trace_context", default=None)
_SECRETS = {"authorization", "access_token", "refresh_token", "id_token", "api_key",
            "client_secret", "cookie", "password", "code_verifier", "mail_body"}


def safe_payload(value, depth=0):
    """Bound private diagnostic payloads; credentials never belong in events."""
    if depth > 12:
        return "[truncated]"
    if isinstance(value, dict):
        return {str(k): "[redacted]" if str(k).casefold() in _SECRETS else safe_payload(v, depth + 1)
                for k, v in list(value.items())[:80]}
    if isinstance(value, (list, tuple)):
        return [safe_payload(v, depth + 1) for v in value[:60]]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "[truncated]"
    return value


class Trace:
    """Append-only audit. Official telemetry includes every dispatched benchmark API call."""

    def __init__(self, room: str, directory: Path, official_path: Path | None = None):
        self.room = room
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "events.jsonl"
        self.official_path = official_path
        self.started = time.monotonic()
        self.trace_id = uuid.uuid4().hex
        self.correlation = {}
        self.sequence = 0

    @contextmanager
    def bind(self, **fields):
        token = _context.set({**(_context.get() or {}), **fields})
        try:
            yield
        finally:
            _context.reset(token)

    def fields(self):
        return {**self.correlation, **(_context.get() or {})}

    def write_snapshot(self, value):
        path = self.path.with_name("snapshot.json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(safe_payload(value), stream, indent=2)

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
        self.sequence += 1
        record = {"schema_version": 2, "trace_id": self.trace_id,
                  "event_id": uuid.uuid4().hex, "sequence": self.sequence,
                  "event": kind, "room": self.room,
                                "elapsed_s": round(time.monotonic()-self.started, 6),
                  "monotonic_ns": time.monotonic_ns(), "wall_time": time.time(),
                  **self.correlation, **(_context.get() or {}), **safe_payload(data)}
        self._append(self.path, record)
        return record

    def tool(self, name, args, start, end, outcome, **details):
        record = {"function": name, "args": args, "timestamp_start": start,
                  "timestamp_end": end, "outcome": outcome}
        self.event("tool_executed", **record, **details)
        if self.official_path:
            self._append(self.official_path, {"room": self.room, "call": record})
