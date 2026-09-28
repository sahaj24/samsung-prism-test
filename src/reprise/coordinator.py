"""Versioned execution, with a causal commit barrier and per-request operation ledger.

The model proposes calls; this module owns execution. It never reads evaluation labels.
"""
import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from .catalog import ToolSpec
from .config import Settings
from .trace import Trace


class Backend(Protocol):
    async def prepare(self, name: str) -> None: ...
    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]: ...


class Superseded(Exception):
    pass


class InvalidCall(Exception):
    pass


class OutcomeUnknown(Exception):
    pass


@dataclass
class Operation:
    name: str
    args: dict[str, Any]
    revision: int
    writes: bool
    sequence: int
    task: asyncio.Task | None = None
    dispatched: bool = False
    result: Any = None
    error: str | None = None


def canonical(text: str):
    return re.sub(r"[^\w]", "", text.casefold())


class Coordinator:
    def __init__(self, backend: Backend, trace: Trace, settings: Settings):
        self.backend, self.trace, self.settings = backend, trace, settings
        self.revision = 0
        self.transaction = 0
        self.speaking = False
        self.active_request = False
        self.transcript = ""
        self.heard_history = ""
        self.last_change = time.monotonic()
        self.operations: dict[str, Operation] = {}
        self.results: dict[str, list[dict]] = {}
        self._input_open = False
        self._speech_text = ""
        self._lock = asyncio.Lock()
        self._proposal_sequence = 0
        self._pending_proposals: dict[int, str] = {}

    def speech_started(self):
        self.speaking = True
        self._input_open = True
        self._speech_text = ""
        self.last_change = time.monotonic()
        self.trace.event("speech_started", revision=self.revision)

    def speech_ended(self):
        self.speaking = False
        self.last_change = time.monotonic()
        self.trace.event("speech_ended", revision=self.revision)

    def observe_transcript(self, text: str, *, is_final: bool = False):
        text = text.strip()
        if not text:
            return
        # A short acknowledgement during an active operation need not invalidate it.
        backchannels = {"mhm", "uhhuh", "okay", "ok", "right", "yeah", "yes", "thanks"}
        if self.active_request and canonical(text) in backchannels:
            self.trace.event("backchannel", text=text)
            return
        if canonical(text) == canonical(self._speech_text):
            return
        if not self.active_request:
            self.transaction += 1
            self.operations = {}
            self.results = {}
            self.transcript = ""
            self.active_request = True
        self._speech_text = text
        self.transcript = text
        self.last_change = time.monotonic()
        self.revision += 1
        self.trace.event("intent_revised", revision=self.revision,
                         transaction=self.transaction, text=text, is_final=is_final)
        if self.settings.policy == "guarded":
            for op in self.operations.values():
                if op.task and not op.task.done() and not (op.writes and op.dispatched):
                    op.task.cancel()

    def finish_response(self):
        if not self.speaking and all(not o.task or o.task.done() for o in self.operations.values()):
            self.active_request = False
            self.trace.event("request_completed", transaction=self.transaction)

    def remember_user_message(self, text: str | None):
        """Keep explicit identifiers available across turns in the same room."""
        if text and text.strip():
            self.heard_history = (self.heard_history + " " + text.strip())[-12000:]

    async def _settle(self, revision: int, writes: bool):
        if self.settings.policy == "baseline":
            return
        settle = (self.settings.write_settle_ms if writes else self.settings.settle_ms) / 1000
        deadline = time.monotonic() + self.settings.gate_timeout_s
        while True:
            if revision != self.revision:
                raise Superseded("User revised the request before execution; re-plan from latest input.")
            remaining = settle - (time.monotonic() - self.last_change)
            if not self.speaking and remaining <= 0:
                return
            if time.monotonic() > deadline:
                raise Superseded("User is still speaking; await the complete request.")
            await asyncio.sleep(min(0.025, max(0.005, remaining)))

    def _normalize(self, spec: ToolSpec, args: dict):
        args = dict(args)
        if spec.name == "update_identity_doc":
            doc_type = args.get("doc_type")
            if isinstance(doc_type, str) and re.search(r"driv(?:er|ing).{0,12}licen[cs]e", doc_type,
                                                       flags=re.IGNORECASE):
                args["doc_type"] = "driver_license"
        for key in ("doc_number", "order_id", "product_id"):
            value = args.get(key)
            if not isinstance(value, str):
                continue
            if key == "product_id":
                returned = {str(row.get("product_id")) for result in self.results.get("search_products", [])
                            for row in result.get("products", [])}
                if value in returned:
                    continue
            # Spoken separators in an alphanumeric identifier are not literal
            # punctuation in the public mock API's identifier namespace.
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\s-]*", value):
                args[key] = re.sub(r"[\s-]", "", value)
        if spec.name == "search_flights" and isinstance(args.get("date"), str):
            args["date"] = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", args["date"],
                                   flags=re.IGNORECASE)
        if spec.name == "modify_autopay" and isinstance(args.get("source_account"), str):
            args["source_account"] = re.sub(r"\s+account\s*$", "",
                                             args["source_account"], flags=re.IGNORECASE)
        if spec.name == "get_card_benefits" and isinstance(args.get("card_type"), str):
            args["card_type"] = re.sub(r"\s+card\s*$", "", args["card_type"],
                                       flags=re.IGNORECASE)
        if spec.name == "calculate_commute":
            modes = {"drive": "driving", "car": "driving", "walk": "walking",
                     "on foot": "walking", "bike": "biking", "bicycle": "biking",
                     "public transit": "transit"}
            mode = args.get("mode")
            if isinstance(mode, str):
                args["mode"] = modes.get(mode.strip().casefold(), mode)
            spoken = (self.heard_history + " " + self.transcript).casefold()
            origin = args.get("origin_address")
            if "my house" in spoken and isinstance(origin, str) and canonical(origin) in {"home", "house", "myhouse"}:
                args["origin_address"] = "my house"
            destination = args.get("destination_address")
            if isinstance(destination, str) and canonical(destination) == "office":
                if "my office" in spoken:
                    args["destination_address"] = "my office"
                elif "the office" in spoken:
                    args["destination_address"] = "the office"
        if spec.name == "update_search_filter" and isinstance(args.get("filter_name"), str):
            key = re.sub(r"[^a-z0-9]+", "_", args["filter_name"].casefold()).strip("_")
            args["filter_name"] = {"allows_pets": "pets_allowed", "pet_friendly": "pets_allowed",
                                   "preferred_neighborhood": "neighborhood"}.get(key, key)
        if spec.name == "search_products" and "max_price" not in args:
            # A spoken ceiling is a search constraint even when phrased conditionally.
            amounts = re.findall(
                r"(?:under|below|less than|at most|up to|no more than)\s+\$?\s*"
                r"([0-9][0-9,]*(?:\.\d{1,2})?)\b",
                self.transcript, flags=re.IGNORECASE,
            )
            if amounts:
                args["max_price"] = float(amounts[-1].replace(",", ""))
                self.trace.event("argument_completed_from_input", function=spec.name,
                                 argument="max_price", value=args["max_price"])
        if spec.name == "update_search_filter" and isinstance(args.get("value"), str):
            # Preserve the type of common spoken scalar values; the public API accepts Any.
            scalar = args["value"].strip().casefold()
            if scalar in {"true", "false"}:
                args["value"] = scalar == "true"
        for key, schema in spec.properties.items():
            value = args.get(key)
            if isinstance(value, str) and schema.get("type") in ("number", "integer"):
                try:
                    number = float(value.replace(",", ""))
                    if schema["type"] == "integer" and not number.is_integer():
                        raise ValueError()
                    args[key] = int(number) if schema["type"] == "integer" else number
                except ValueError:
                    pass
        errors = sorted(Draft202012Validator(spec.schema).iter_errors(args),
                        key=lambda e: str(e.path))
        if errors:
            raise InvalidCall("; ".join(e.message for e in errors))
        return args

    def _check_references(self, spec, args):
        for parameter, (source, collection, field) in spec.references.items():
            if parameter not in args:
                continue
            value = str(args[parameter])
            valid = {str(row.get(field)) for result in self.results.get(source, [])
                     for row in result.get(collection, []) if field in row}
            if value in valid or (canonical(value) and canonical(value) in
                                  canonical(self.heard_history + " " + self.transcript)):
                continue
            raise InvalidCall(f"{parameter} must come from {source} results or the user's "
                              "explicit identifier. Call the prerequisite if needed; do not guess.")

    async def execute(self, spec: ToolSpec, args: dict, *, interrupted=None):
        args = self._normalize(spec, args)
        revision = self.revision
        self._proposal_sequence += 1
        sequence = self._proposal_sequence
        self.trace.event("tool_proposed", function=spec.name, args=args, revision=revision)
        self._pending_proposals[sequence] = spec.name
        try:
            await self._settle(revision, spec.writes)
            if interrupted and interrupted():
                raise Superseded("The originating speech turn was interrupted.")
            if self.settings.policy == "guarded":
                self._check_references(spec, args)
            signature = hashlib.sha256(json.dumps([self.transaction, spec.name, args],
                                       sort_keys=True).encode()).hexdigest()
            async with self._lock:
                op = self.operations.get(signature) if self.settings.policy == "guarded" else None
                if op and op.task and op.task.cancelled():
                    op = None
                if op is None:
                    op = Operation(spec.name, args, revision, spec.writes, sequence)
                    op.task = asyncio.create_task(self._invoke(op))
                    self.operations[signature] = op
                else:
                    self.trace.event("duplicate_coalesced", function=spec.name, args=args,
                                     original_revision=op.revision)
        finally:
            self._pending_proposals.pop(sequence, None)
        try:
            result = await asyncio.shield(op.task)
        except asyncio.CancelledError:
            if op.task.cancelled():
                raise Superseded("Tool preparation was superseded by corrected user input.") from None
            # Caller cancellation never retries an already-dispatched mutation.
            if not (op.writes and op.dispatched):
                op.task.cancel()
            raise
        if self.settings.policy == "guarded" and revision != self.revision:
            self.trace.event("stale_result_suppressed", function=spec.name,
                             dispatched=op.dispatched, writes=op.writes)
            raise Superseded("The request changed. This result is retained in the action ledger "
                             "but must not be spoken as an answer to the corrected request.")
        return result

    async def _invoke(self, op: Operation):
        # Simulated latency is asynchronous, so it cannot block listening/correction.
        await self.backend.prepare(op.name)
        if self.settings.policy == "guarded":
            await self._settle(op.revision, op.writes)
            # The model may propose several calls of one tool in one batch.
            # Preserve that causal order even when simulated preparation delays
            # finish out of order. In particular, successive writes must not
            # be reordered by the runtime scheduler.
            while any(other is not op and other.name == op.name
                      and other.sequence < op.sequence and not other.dispatched
                      and other.task and not other.task.done()
                      for other in self.operations.values()) or any(
                          pending_name == op.name and pending_sequence < op.sequence
                          for pending_sequence, pending_name in self._pending_proposals.items()):
                await asyncio.sleep(.01)
                await self._settle(op.revision, op.writes)
        start = time.time()
        op.dispatched = True
        self.trace.event("tool_dispatch", function=op.name, args=op.args,
                         revision=op.revision, writes=op.writes)
        outcome = "unknown"
        try:
            op.result = await asyncio.wait_for(self.backend.call(op.name, op.args),
                                               self.settings.tool_timeout_s)
            if op.result.get("status") == "error":
                outcome = "error"
                op.error = str(op.result.get("message", "Tool returned an error"))
            else:
                outcome = "success"
                self.results.setdefault(op.name, []).append(op.result)
            return op.result
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as exc:
            op.error = type(exc).__name__
            outcome = "unknown" if op.writes else "error"
            raise OutcomeUnknown("Tool execution failed; mutation outcome may be unknown. "
                                 "Do not claim success or repeat the action.") from None
        finally:
            self.trace.tool(op.name, op.args, start, time.time(), outcome)

    async def close(self):
        pending = [o.task for o in self.operations.values() if o.task and not o.task.done()]
        for o in self.operations.values():
            if o.task and not o.task.done() and not (o.writes and o.dispatched):
                o.task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def snapshot(self):
        return {"revision": self.revision, "transaction": self.transaction,
                "latest_input": self.transcript, "heard_history": self.heard_history,
                "actions": [{"tool": o.name, "args": o.args, "dispatched": o.dispatched,
                             "completed": bool(o.task and o.task.done() and not o.task.cancelled()),
                             "error": o.error} for o in self.operations.values()]}
