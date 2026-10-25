"""Versioned execution, with a causal commit barrier and per-request operation ledger.

The model proposes calls; this module owns execution. It never reads evaluation labels.
"""

import asyncio
import hashlib
import json
import re
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from .catalog import ToolSpec
from .config import Settings
from .intent import (FOLLOWUP, GOAL_RESET, REFRESH, REVISION_MARKER, IMPLICIT_CORRECTION, IntentMemory,
                     affected_indices, identifier_candidates, spelled_identifier, undecided_read)
from .trace import Trace


class Backend(Protocol):
    async def prepare(self, name: str) -> None: ...
    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]: ...


class Superseded(Exception):
    pass


class InvalidCall(Exception):
    pass


class ClarificationRequired(InvalidCall):
    def __init__(self, message, input_id):
        super().__init__(message)
        self.input_id = input_id


class OutcomeUnknown(Exception):
    pass


UNSETTLED = {"unknown", "running", "pending", "partial", "needs_review"}


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
    outcome: str | None = None
    correlation: dict = dataclass_field(default_factory=dict)
    input_id: str | None = None
    transaction: int = 0
    superseded: bool = False
    state: str = "pending"
    spec: ToolSpec | None = None
    retained_context: dict = dataclass_field(default_factory=dict)
    owner: "Operation | None" = None
    normalized: bool = False
    validation_error: str | None = None
    dependencies: list = dataclass_field(default_factory=list)
    completed_at: float | None = None
    cache_invalidated: bool = False


def canonical(text: str):
    return re.sub(r"[^\w]", "", text.casefold())


def transcript_key(text: str):
    # Ignore presentation punctuation, but retain separators inside identifiers.
    return re.sub(r"\s+", " ", text.casefold()).strip().rstrip(".,!?;:")


CORRECTION = re.compile(
    r"\b(?:actually|instead|rather|never mind|changed my mind|scratch that|wait|"
    r"cancel|stop|do not|don't|forget that)\b",
    re.IGNORECASE,
)


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
        self._speech_text = ""
        self._proposal_sequence = 0
        self._pending_proposals: dict[int, Operation] = {}
        self._proposals: list[Operation] = []
        self._inputs: dict[str | None, str] = {None: ""}
        self.input_final = False
        self.current_input_id = None
        self.response_proposals: dict[str, list[str]] = {}
        self.response_contexts: dict[str, dict] = {}
        self._obsolete_responses: set[str] = set()
        self.last_response_id = None
        self.intent_memory = IntentMemory()
        self._input_items: dict[str, str] = {}
        self._final_items: set[str] = set()
        self._event_times: dict[str, float] = {}
        self._committed_texts: dict[str, str] = {}
        self._committed_inputs: set[str] = set()
        self._followup = False
        self._refresh = False
        self._recovery_errors = {}
        self._recovery_notices = set()
        self._grounding_notices = set()

    def speech_started(self):
        if self.speaking:
            return
        self.speaking = True
        # Acoustic activity pauses dispatch; only transcript evidence can change
        # commitment. A VAD pulse need not produce another provider transcript.
        self.last_change = time.monotonic()
        self.trace.event("speech_started", revision=self.revision)

    def speech_ended(self):
        if not self.speaking:
            return
        self.speaking = False
        self.last_change = time.monotonic()
        self.trace.event("speech_ended", revision=self.revision)

    def observe_transcript(
        self, text: str, *, is_final: bool = True, source="audio", item_id=None, event_time=None
    ):
        text = text.strip()
        if not text:
            return
        known_input = self._input_items.get(item_id)
        stale = (
            (event_time is not None and event_time < self._event_times.get(source, event_time))
            or (known_input is not None and known_input != self.current_input_id)
            or item_id in self._final_items
            or (
                source == "conversation"
                and transcript_key(text) in self._committed_texts
                and self._committed_texts[transcript_key(text)] != self.current_input_id
            )
        )
        if stale:
            self.trace.event(
                "transcript_ignored",
                source=source,
                item_id=item_id,
                reason="obsolete_or_committed_input",
            )
            return
        if event_time is not None:
            self._event_times[source] = event_time
        # A delayed conversation echo must not open a new transaction.
        if (
            not self.active_request
            and source == "conversation"
            and transcript_key(text) in self._committed_texts
        ):
            return
        duplicate = self.active_request and transcript_key(text) == transcript_key(
            self._speech_text
        )
        hypothesis_changed = self.active_request and not self.input_final and not duplicate
        input_id = (
            self.current_input_id
            if duplicate or (self.active_request and not self.input_final)
            else uuid.uuid4().hex
        )
        request_id = f"{self.trace.trace_id}:{self.transaction + (not self.active_request)}"
        self.trace.event(
            "transcript_received",
            text=text,
            is_final=is_final,
            source=source,
            input_id=input_id,
            request_id=request_id,
            item_id=item_id,
        )
        # A short acknowledgement during an active operation need not invalidate it.
        backchannels = {"mhm", "uhhuh", "okay", "ok", "right", "yeah", "thanks"}
        if not getattr(self.backend, "requires_final_input", False):
            backchannels.add("yes")
        if self.active_request and canonical(text) in backchannels:
            self.trace.event("backchannel", text=text)
            return
        if duplicate and (not is_final or self.input_final):
            return
        previous = self._speech_text
        self._followup = bool(FOLLOWUP.search(text)) and not GOAL_RESET.search(text)
        self._refresh = bool(REFRESH.search(text))
        if not self.active_request:
            self.transaction += 1
            self.results = {}
            self._proposals = []
            self.transcript = ""
            self.active_request = True
            self.last_response_id = None
            changed_intent = True
        else:
            added = (
                text[len(previous) :]
                if previous and text.casefold().startswith(previous.casefold())
                else text
            )
            # A rewritten ASR hypothesis or an implicit replacement has the same
            # authority as an explicit "actually". Additive turns still preserve work.
            rewritten = previous and hypothesis_changed
            changed_intent = not duplicate and bool(
                CORRECTION.search(added) or IMPLICIT_CORRECTION.search(added) or rewritten
            )
        self._speech_text = text
        self.transcript = text
        self.input_final = is_final
        self.current_input_id = input_id
        self._inputs[input_id] = text
        if item_id:
            self._input_items[item_id] = input_id
            if is_final:
                self._final_items.add(item_id)
        if is_final:
            self._committed_inputs.add(input_id)
            self._committed_texts[transcript_key(text)] = input_id
            self.remember_user_message(text)
        self.last_change = time.monotonic()
        if changed_intent:
            self.revision += 1
        self.trace.correlation.update(
            request_id=f"{self.trace.trace_id}:{self.transaction}",
            revision=self.revision,
            input_id=self.current_input_id,
        )
        observer = getattr(self.backend, "observe_input", None)
        if observer:
            observer(text, is_final=is_final, revision=self.revision)
        self.trace.event(
            "intent_revised" if changed_intent else "intent_extended",
            revision=self.revision,
            transaction=self.transaction,
            text=text,
            is_final=is_final,
        )
        if changed_intent and self.settings.policy == "guarded":
            for op in self._correction_targets(text, previous):
                self._supersede(op)
            if GOAL_RESET.search(text):
                self.intent_memory = IntentMemory()
        if self._refresh and self.settings.policy == "guarded":
            for op in self._correction_targets(text, previous):
                if not op.writes:
                    self._supersede(op)
        if hypothesis_changed:
            for op in self._proposals:
                if op.input_id == input_id:
                    self._supersede(op)
        if is_final:
            for op in self._proposals:
                if op.input_id == input_id and not op.superseded:
                    self._normalize_proposal(op)
        self._rebuild_results()

    def _known_operations(self):
        seen = set()
        for saved in [*self.operations.values(), *self._proposals]:
            op = saved.owner or saved
            if op.sequence not in seen:
                seen.add(op.sequence)
                yield op

    def _scope(self, op):
        spec = op.spec
        return (*spec.intent_scope, *spec.name.split('_'), *spec.retained_slots,
                *(label for key in spec.identifiers for label in self._identifier_labels(spec, op.args, key)))

    def _subject(self, op):
        get = getattr(self.backend, "proposal_subject", None)
        values, extra = get(op.name, op.args) if get else (op.args, ())
        return values, (*self._scope(op), *extra)

    def _correction_targets(self, text, previous=""):
        """Scope only with explicit entity/field evidence; ambiguous corrections
        conservatively invalidate the request. No inferred replacement values.
        """

        markers = list(REVISION_MARKER.finditer(text))
        fragment = text[markers[-1].end():] if markers else text

        def mentioned(value):
            return isinstance(value, str) and bool(
                re.search(
                    r"(?<!\w)" + re.escape(value).replace("_", r"[\s_-]+") + r"(?!\w)", fragment, re.I
                )
            )

        # Ordinary new transactions are independent, not implicit cancellation of
        # everything previously completed. Corrections can arrive after a pause.
        if not (CORRECTION.search(text) or IMPLICIT_CORRECTION.search(text)):
            candidates = [op for op in self._proposals if not op.superseded]
        else:
            candidates = [op for op in self._known_operations() if not op.superseded]
        if re.search(r"\b(?:cancel|stop|forget) (?:it |them )?all\b|\beverything\b", text, re.I):
            return candidates
        entities = {
            (key, value)
            for op in candidates
            for key, value in op.args.items()
            if (key.endswith(("_id", "_type")) or key == "filter_name") and mentioned(value)
        }
        if entities:
            return [op for op in candidates if any(op.args.get(k) == v for k, v in entities)]
        indices = affected_indices(text, previous, [self._subject(op) for op in candidates])
        return [op for i, op in enumerate(candidates) if i in indices]

    @staticmethod
    def _identifier_labels(spec, args, key):
        if key == "doc_number":
            kind = str(args.get("doc_type", "")).casefold()
            return (
                ("license", "licence")
                if re.search(r"licen[cs]e", kind)
                else ("passport",)
                if "passport" in kind
                else ("document",)
            )
        return spec.identifiers.get(key, (key.removesuffix("_id"),))

    def _supersede(self, op, *, invalidate_response=True):
        if op.superseded:
            return
        response_id = op.correlation.get("response_id")
        if response_id and invalidate_response:
            self._obsolete_responses.add(response_id)
        op.superseded = True
        op = op.owner or op
        op.superseded = True
        if not op.dispatched:
            op.state = "superseded"
            if op.task and not op.task.done():
                op.task.cancel()
        # A completed/still-running receipt remains in the ledger, but cannot
        # seed a new reference or become the answer to a corrected request.
        self.trace.event("operation_invalidated", original_proposal_id=op.correlation.get("proposal_id"),
                         function=op.name, dispatched=op.dispatched, writes=op.writes)
        for dependent in list(self._known_operations()):
            if any(source.superseded for source in dependent.dependencies):
                self._supersede(dependent)
        self._rebuild_results()

    def _valid_source(self, op, *, fresh=True):
        expired = (op.completed_at is not None and op.spec.reuse_for_s
                   and time.monotonic() - op.completed_at > op.spec.reuse_for_s)
        # An ID is a locator for a fresh read, not authority to mutate an expired
        # snapshot. Superseded intent is invalid in either case. A fresh read's
        # own result can replace the snapshot without inheriting its old TTL.
        return not (op.superseded or (fresh and (op.cache_invalidated or expired)) or any(
            not self._valid_source(source, fresh=op.writes) for source in op.dependencies))

    def _reusable_read(self, op):
        if op.writes or not self._valid_source(op) or self._refresh:
            return False
        ttl = op.spec.reuse_for_s
        if op.transaction != self.transaction and not (self._followup and ttl):
            return False
        if op.task and not op.task.done():
            return True
        if op.outcome != "success" or not (op.spec.cache_result or ttl):
            return False
        check = getattr(self.backend, "can_reuse", None)
        return check is None or check(op.name, op.args, op.result)

    def _rebuild_results(self):
        self.results = {}
        for op in self._known_operations():
            if op.outcome == "success" and self._valid_source(op) and (
                op.transaction == self.transaction or self._reusable_read(op)
            ):
                self.results.setdefault(op.name, []).append(op.result)

    def _capture_dependencies(self, proposal):
        """Exact typed ID/ref provenance, never fuzzy equality between entities."""
        def contains(value, key, expected):
            if isinstance(value, dict):
                return value.get(key) == expected or any(contains(v, key, expected) for v in value.values())
            if isinstance(value, list):
                return any(contains(v, key, expected) for v in value)
            return False
        for key, value in proposal.args.items():
            if not isinstance(value, str) or not (key.endswith(("_id", "_ref")) or key in proposal.spec.references):
                continue
            locator = getattr(self.backend, "reference_is_locator", None)
            if locator and locator(proposal.name, key, value):
                continue
            reference = proposal.spec.references.get(key)
            source_name, field = (reference[0], reference[2]) if reference else (None, key)
            sources = [op for op in self._known_operations() if op is not proposal
                       and op.sequence < proposal.sequence and op.outcome == "success"
                       and (not source_name or op.name == source_name)
                       and contains(op.result, field, value)]
            if sources:
                source = max(sources, key=lambda op: op.sequence)
                labels = proposal.spec.identifiers.get(key, (key.removesuffix('_id'),))
                explicit = identifier_candidates(self._inputs[proposal.input_id], labels)
                if reference and spelled_identifier(value) in explicit:
                    continue  # The current user supplied new authority for this exact identifier.
                # A saved plan ID is also an inspection handle. Let the durable
                # backend report its revoked status, rather than hide that receipt.
                if key == 'plan_id' and not reference and not self._valid_source(source):
                    continue
                if all(source is not existing for existing in proposal.dependencies):
                    proposal.dependencies.append(source)
                    self.trace.event("dependency_bound", function=proposal.name,
                                     source_proposal_id=source.correlation.get("proposal_id"),
                                     proposal_id=proposal.correlation.get("proposal_id"), argument=key)

    def finish_response(self):
        if (
            self.input_final
            and not self.speaking
            and not self._pending_proposals
            and all(not o.task or o.task.done() for o in self.operations.values())
        ):
            self.active_request = False
            self.trace.event("request_completed", transaction=self.transaction)

    def remember_user_message(self, text: str | None):
        """Keep explicit identifiers available across turns in the same room."""
        if text and text.strip():
            self.heard_history = (self.heard_history + " " + text.strip())[-12000:]

    async def _settle(self, op: Operation):
        if self.settings.policy == "baseline":
            return
        settle = (self.settings.write_settle_ms if op.writes else self.settings.settle_ms) / 1000
        deadline = time.monotonic() + self.settings.gate_timeout_s
        while True:
            if op.superseded:
                raise Superseded(
                    "User revised the request before execution; re-plan from latest input."
                )
            remaining = settle - (time.monotonic() - self.last_change)
            committed = self.input_final and op.input_id in self._committed_inputs
            if not self.speaking and committed and remaining <= 0:
                return
            if time.monotonic() > deadline:
                reason = "Acoustic speech is active" if self.speaking else "Input is not committed"
                raise Superseded(f"{reason}; await the complete request.")
            await asyncio.sleep(min(0.025, max(0.005, remaining)))

    def _normalize(
        self, spec: ToolSpec, args: dict, *, text, input_id, revision, retained_context, sequence
    ):
        args, carried = self.intent_memory.enrich(
            spec, args, text, input_id, prior=retained_context
        )
        for key, provenance in carried.items():
            self.trace.event("argument_retained", function=spec.name, argument=key, **provenance)
        if self.input_final:
            for key in spec.identifiers:
                labels = self._identifier_labels(spec, args, key)
                candidates = identifier_candidates(text, labels)
                for correction in CORRECTION.finditer(text):
                    prefix = text[: correction.start()]
                    tail = text[correction.end() :].lstrip(" ,:")
                    previous = identifier_candidates(prefix, labels)
                    replacement = identifier_candidates(tail, labels)
                    if not replacement:
                        # An unlabelled replacement belongs only to the immediately
                        # preceding entity, never another document or resource.
                        all_labels = spec.identifiers[key]
                        local_prefix = re.split(r"\b(?:and|then|also)\b|[;—]", prefix, flags=re.I)[-1]
                        anchors = re.findall(
                            r"\b(" + "|".join(map(re.escape, all_labels)) + r")s?\b", local_prefix, re.I
                        )
                        if anchors and anchors[-1].casefold() in labels:
                            replacement = identifier_candidates(labels[0] + " " + tail, labels)
                    if previous and replacement:
                        candidates = list(
                            dict.fromkeys(
                                [value for value in candidates if value != previous[-1]]
                                + replacement
                            )
                        )
                value = args.get(key)
                if candidates and isinstance(value, str):
                    if spelled_identifier(value) not in candidates:
                        raise InvalidCall(
                            f"{key} conflicts with the explicit identifier; clarify before execution."
                        )
                    if len(candidates) > 1 and re.search(r"\b(?:or|either|not sure)\b", text, re.I):
                        raise InvalidCall(f"{key} is ambiguous; clarify before execution.")
        if spec.name == "update_identity_doc":
            doc_type = args.get("doc_type")
            if isinstance(doc_type, str) and re.search(
                r"driv(?:er|ing).{0,12}licen[cs]e", doc_type, flags=re.IGNORECASE
            ):
                args["doc_type"] = "driver_license"
        for key in ("doc_number", "order_id", "product_id"):
            value = args.get(key)
            if not isinstance(value, str):
                continue
            if key == "product_id":
                returned = {
                    str(row.get("product_id"))
                    for result in self.results.get("search_products", [])
                    for row in result.get("products", [])
                }
                if value in returned:
                    continue
            # Join individually spelled characters only. Preserve punctuation
            # inside literal/returned resource IDs (e.g. a prefix plus a number).
            args[key] = spelled_identifier(value)
        if spec.name == "search_flights" and isinstance(args.get("date"), str):
            args["date"] = re.sub(
                r"(?<=\d)(?:st|nd|rd|th)\b", "", args["date"], flags=re.IGNORECASE
            )
        if spec.name == "modify_autopay" and isinstance(args.get("source_account"), str):
            args["source_account"] = re.sub(
                r"\s+account\s*$", "", args["source_account"], flags=re.IGNORECASE
            )
        if spec.name == "get_card_benefits" and isinstance(args.get("card_type"), str):
            args["card_type"] = re.sub(r"\s+card\s*$", "", args["card_type"], flags=re.IGNORECASE)
        if spec.name == "calculate_commute":
            modes = {
                "drive": "driving",
                "car": "driving",
                "walk": "walking",
                "on foot": "walking",
                "bike": "biking",
                "bicycle": "biking",
                "public transit": "transit",
            }
            mode = args.get("mode")
            if isinstance(mode, str):
                args["mode"] = modes.get(mode.strip().casefold(), mode)
            spoken = (self.heard_history + " " + text).casefold()
            origin = args.get("origin_address")
            if (
                "my house" in spoken
                and isinstance(origin, str)
                and canonical(origin) in {"home", "house", "myhouse"}
            ):
                args["origin_address"] = "my house"
            destination = args.get("destination_address")
            if isinstance(destination, str) and canonical(destination) == "office":
                if "my office" in spoken:
                    args["destination_address"] = "my office"
                elif "the office" in spoken:
                    args["destination_address"] = "the office"
        if spec.name == "update_search_filter" and isinstance(args.get("filter_name"), str):
            key = re.sub(r"[^a-z0-9]+", "_", args["filter_name"].casefold()).strip("_")
            args["filter_name"] = {
                "allows_pets": "pets_allowed",
                "pet_friendly": "pets_allowed",
                "preferred_neighborhood": "neighborhood",
            }.get(key, key)
        if spec.name == "search_products" and "max_price" not in args:
            # A spoken ceiling is a search constraint even when phrased conditionally.
            amounts = re.findall(
                r"(?:under|below|less than|at most|up to|no more than)\s+\$?\s*"
                r"([0-9][0-9,]*(?:\.\d{1,2})?)\b",
                text,
                flags=re.IGNORECASE,
            )
            if amounts:
                args["max_price"] = float(amounts[-1].replace(",", ""))
                self.trace.event(
                    "argument_completed_from_input",
                    function=spec.name,
                    argument="max_price",
                    value=args["max_price"],
                )
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
        errors = sorted(
            Draft202012Validator(spec.schema).iter_errors(args), key=lambda e: str(e.path)
        )
        if errors:
            raise InvalidCall("; ".join(e.message for e in errors))
        if self.input_final:
            self.intent_memory.record(spec, args, text, input_id, revision, sequence=sequence)
        return args

    def _check_references(self, spec, args, text):
        for parameter, (source, collection, field) in spec.references.items():
            if parameter not in args:
                continue
            value = str(args[parameter])
            valid = {
                str(row.get(field))
                for result in self.results.get(source, [])
                for row in result.get(collection, [])
                if field in row
            }
            labels = spec.identifiers.get(parameter, (parameter.removesuffix("_id"),))
            explicit = identifier_candidates(self.heard_history + " " + text, labels)
            if value in valid or spelled_identifier(value) in explicit:
                continue
            raise InvalidCall(
                f"{parameter} must come from {source} results or the user's "
                "explicit identifier. Call the prerequisite if needed; do not guess."
            )

    def _normalize_proposal(self, proposal):
        if proposal.normalized:
            return
        with self.trace.bind(**proposal.correlation):
            try:
                proposal.args = self._normalize(
                    proposal.spec,
                    proposal.args,
                    text=self._inputs[proposal.input_id],
                    input_id=proposal.input_id,
                    revision=proposal.revision,
                    retained_context=proposal.retained_context,
                    sequence=proposal.sequence,
                )
                self._capture_dependencies(proposal)
            except InvalidCall as exc:
                proposal.validation_error = str(exc)
                proposal.state = "rejected"
        proposal.normalized = True

    def execute(
        self,
        spec: ToolSpec,
        args: dict,
        *,
        interrupted=None,
        provider_call_id=None,
        response_id=None,
    ):
        # Reserve proposal order and input ownership before coroutine scheduling.
        # `interrupted` describes assistant playback, not the user's intent.
        self._proposal_sequence += 1
        proposal_id = uuid.uuid4().hex
        correlation = dict(self.response_contexts.get(response_id, self.trace.correlation))
        correlation.setdefault("transaction", self.transaction)
        correlation.update(
            proposal_id=proposal_id, provider_call_id=provider_call_id, response_id=response_id
        )
        proposal = Operation(
            spec.name,
            deepcopy(args),
            correlation.get("revision", self.revision),
            spec.writes,
            self._proposal_sequence,
            correlation=correlation,
            input_id=correlation.get("input_id", self.current_input_id),
            transaction=correlation["transaction"],
            spec=spec,
            retained_context=deepcopy(self.intent_memory.slots.get(spec.name, {})),
        )
        with self.trace.bind(**correlation):
            self.trace.event("tool_raw_proposed", function=spec.name, args=proposal.args)
        if response_id in self._obsolete_responses or proposal.transaction != self.transaction:
            proposal.superseded = True
        if self.input_final and not proposal.superseded:
            self._normalize_proposal(proposal)
        # A valid, richer proposal from this same input supersedes an uncommitted
        # partial read at registration, before either caller can be scheduled.
        if (
            self.settings.policy == "guarded"
            and spec.retained_slots
            and not spec.writes
            and proposal.normalized
            and not proposal.validation_error
            and not proposal.superseded
        ):
            for prior in self._proposals:
                if (
                    prior.name == spec.name
                    and prior.input_id == proposal.input_id
                    and not (prior.owner or prior).dispatched
                    and not prior.superseded
                    and prior.args.items() < proposal.args.items()
                ):
                    self._supersede(prior, invalidate_response=False)
                    self.trace.event(
                        "read_refinement",
                        function=spec.name,
                        replaced_args=prior.args,
                        args=proposal.args,
                    )
        self._proposals.append(proposal)
        self._pending_proposals[proposal.sequence] = proposal
        if response_id:
            self.last_response_id = response_id
            self.response_contexts.setdefault(response_id, correlation)
            self.response_proposals.setdefault(response_id, []).append(proposal_id)
        return self._deliver(spec, proposal)

    async def _deliver(self, spec, proposal):
        with self.trace.bind(**proposal.correlation):
            try:
                result = await self._execute(spec, proposal)
                if result.get("status") == "error":
                    self._recovery_errors[proposal.input_id] = self._recovery_errors.get(proposal.input_id, 0) + 1
                self.trace.event(
                    "tool_result_delivered", function=spec.name, status=result.get("status")
                )
                return result
            except InvalidCall as exc:
                if not isinstance(exc, ClarificationRequired):
                    self._recovery_errors[proposal.input_id] = self._recovery_errors.get(proposal.input_id, 0) + 1
                proposal.state = "rejected"
                self.trace.event("tool_validation_failed", function=spec.name, reason=str(exc))
                raise
            except Superseded as exc:
                if not proposal.dispatched:
                    proposal.state = "superseded"
                self.trace.event("tool_superseded", function=spec.name, reason=str(exc))
                raise
            except OutcomeUnknown:
                self.trace.event("tool_outcome_unknown", function=spec.name, writes=spec.writes)
                raise
            except asyncio.CancelledError:
                if proposal.task is None:
                    proposal.state = "cancelled"
                self.trace.event("tool_caller_detached", function=spec.name)
                raise
            finally:
                self._pending_proposals.pop(proposal.sequence, None)

    async def _execute(self, spec, proposal):
        limit = getattr(self.backend, "recovery_limit", 0)
        if (self.settings.policy == "guarded" and limit
            and self._recovery_errors.get(proposal.input_id, 0) >= limit):
            self.trace.event("recovery_paused", function=spec.name, input_id=proposal.input_id,
                             failed_attempts=self._recovery_errors[proposal.input_id])
            raise ClarificationRequired(
                "I couldn't complete that request safely. Please clarify which task or event you mean and the change you want. I have stopped retrying; completed actions remain recorded.",
                proposal.input_id,
            )
        gate_start = time.monotonic()
        await self._settle(proposal)
        text = self._inputs[proposal.input_id]
        self._normalize_proposal(proposal)
        if proposal.validation_error:
            raise InvalidCall(proposal.validation_error)
        if self.settings.policy == "guarded" and any(
            not self._valid_source(source, fresh=proposal.writes) for source in proposal.dependencies
        ):
            raise InvalidCall("A prerequisite result was invalidated; obtain a current result before acting.")
        args = proposal.args
        if self.settings.policy == "guarded" and undecided_read(text, args, self._scope(proposal)):
            raise InvalidCall("The requested alternative is undecided; clarify before execution.")
        self.trace.event("tool_proposed", function=spec.name, args=args, revision=proposal.revision)
        if self.settings.policy == "guarded":
            self._check_references(spec, args, text)
        self.trace.event(
            "controller_decision",
            decision="admitted",
            function=spec.name,
            gate_duration_s=time.monotonic() - gate_start,
        )
        signature = hashlib.sha256(
            json.dumps([proposal.transaction, spec.name, args], sort_keys=True).encode()
        ).hexdigest()
        # This transition has no await: validation, lookup and ownership are atomic
        # on the session event loop. No lock or scheduler-dependent revision read.
        op = self.operations.get(signature) if self.settings.policy == "guarded" else None
        if self.settings.policy == "guarded" and not spec.writes and (op is None or not self._reusable_read(op)):
            op = next((saved for saved in reversed(list(self._known_operations()))
                       if saved.name == spec.name and saved.args == args and self._reusable_read(saved)), None)
        if op and op.superseded:
            if op.writes and op.dispatched:
                raise Superseded(
                    "This earlier action is recorded; it cannot be replayed or presented as current."
                )
            op = None
        if (
            op
            and op.task
            and op.task.done()
            and (
                op.outcome not in {"success", *UNSETTLED}
                or (op.outcome not in UNSETTLED and not spec.cache_result and not self._reusable_read(op))
            )
        ):
            op = None
        if op is None:
            if (
                self.settings.policy == "guarded"
                and spec.writes
                and any(
                    saved.dispatched
                    and saved.outcome in UNSETTLED
                    and self._conflicts(saved, proposal)
                    for saved in self.operations.values()
                )
            ):
                proposal.state = "rejected"
                raise OutcomeUnknown(
                    "An earlier write has an uncertain outcome; reconcile it before another write to that resource."
                )
            # Registration order owns a duplicate operation, even when a later
            # waiter is scheduled first. Otherwise predecessor waits can cycle.
            op = proposal
            if self.settings.policy == "guarded":
                op = next(
                    (
                        p
                        for p in self._proposals
                        if p.transaction == proposal.transaction
                        and p.name == spec.name
                        and p.args == args
                        and p.normalized
                        and not p.validation_error
                        and not p.superseded
                        and p.task is None
                        and p.state == "pending"
                    ),
                    proposal,
                )
            with self.trace.bind(**op.correlation):
                op.task = asyncio.create_task(self._invoke(op))
            op.task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            self.operations[signature] = op
            self._pending_proposals.pop(op.sequence, None)
        if op is not proposal:
            proposal.owner = op
            proposal.task = op.task
            proposal.state = "coalesced"
            self.trace.event(
                "duplicate_coalesced",
                function=spec.name,
                args=args,
                original_revision=op.revision,
                original_proposal_id=op.correlation.get("proposal_id"),
            )
            if not op.writes:
                self.trace.event("read_result_reused", function=spec.name, args=args,
                                 in_flight=not op.task.done(), source_revision=op.revision,
                                 source_proposal_id=op.correlation.get("proposal_id"))
        self._pending_proposals.pop(proposal.sequence, None)
        try:
            result = await asyncio.shield(op.task)
        except asyncio.CancelledError:
            if op.task.cancelled():
                raise Superseded(
                    "Tool preparation was superseded by corrected user input."
                ) from None
            raise
        if self.settings.policy == "guarded" and (
            op.superseded or proposal.superseded or (not op.writes and not self._valid_source(op))
        ):
            self.trace.event(
                "stale_result_suppressed",
                function=spec.name,
                dispatched=op.dispatched,
                writes=op.writes,
            )
            raise Superseded(
                "The request changed. The actual outcome remains in the ledger, not the current answer."
            )
        return deepcopy(result)

    async def _invoke(self, op: Operation):
        start = time.time()
        monotonic_start = time.monotonic()
        outcome = "error"
        try:
            # Simulated latency is asynchronous, so it cannot block listening/correction.
            preparation_start = time.monotonic()
            await self.backend.prepare(op.name)
            self.trace.event(
                "tool_prepared", function=op.name, duration_s=time.monotonic() - preparation_start
            )
            if self.settings.policy == "guarded":
                await self._settle(op)
                # Only writes to the same resource share a causal order. Wait for
                # their outcome, not merely the instant their coroutine dispatched.
                deadline = (
                    time.monotonic() + self.settings.gate_timeout_s + self.settings.tool_timeout_s
                )
                for earlier in self._proposals:
                    if earlier.sequence >= op.sequence or not self._conflicts(earlier, op):
                        continue
                    while (
                        earlier.sequence in self._pending_proposals
                        or (earlier.task and not earlier.task.done())
                    ) and not (earlier.superseded and not earlier.dispatched):
                        if earlier.task is op.task:
                            break
                        if time.monotonic() >= deadline:
                            raise Superseded(
                                "Preceding action has not settled; no subsequent write dispatched."
                            )
                        await asyncio.sleep(0.005)
                        await self._settle(op)
                    if earlier.dispatched and earlier.outcome in UNSETTLED:
                        raise OutcomeUnknown(
                            "A preceding write has an uncertain outcome; do not dispatch another write to that resource."
                        )
            if self.settings.policy == "guarded":
                if any(not self._valid_source(source, fresh=op.writes) for source in op.dependencies):
                    raise InvalidCall("A prerequisite changed before dispatch; re-plan from current results.")
                self._check_references(op.spec, op.args, self._inputs[op.input_id])
            op.dispatched = True
            op.state = "dispatched"
            self.trace.event(
                "tool_dispatch",
                function=op.name,
                args=op.args,
                revision=op.revision,
                writes=op.writes,
            )
            invalidate = getattr(self.backend, "invalidates_reads", None)
            if op.writes and invalidate:
                names = invalidate(op.name, op.args)
                for read in self._known_operations():
                    if not read.writes and read.name in names:
                        read.cache_invalidated = True
                self._rebuild_results()
            start = time.time()
            monotonic_start = time.monotonic()
            op.result = await asyncio.wait_for(
                self.backend.call(op.name, op.args), self.settings.tool_timeout_s
            )
            if not isinstance(op.result, dict) or not isinstance(op.result.get("status"), str):
                raise ValueError("Backend did not return a recognized outcome")
            if op.result.get("status") == "error":
                outcome = "error"
                op.error = str(op.result.get("message", "Tool returned an error"))
            elif op.result.get("status") in {"success", "completed", "ok"}:
                outcome = "success"
                if not op.superseded and (
                    self.settings.policy == "baseline" or op.writes or self._valid_source(op)
                ):
                    self.results.setdefault(op.name, []).append(op.result)
            elif op.result.get("status") in UNSETTLED | {
                "approval_required",
                "prepared",
                "superseded",
                "cancelled",
            }:
                outcome = op.result.get("status", "unrecognized")
            else:
                raise ValueError("Backend did not return a recognized outcome")
            return op.result
        except asyncio.CancelledError:
            outcome = (
                "unknown"
                if op.writes and op.dispatched
                else "superseded"
                if op.superseded
                else "cancelled"
            )
            raise
        except Exception as exc:
            if not op.dispatched and isinstance(exc, (Superseded, InvalidCall, OutcomeUnknown)):
                outcome = "superseded" if op.superseded else "rejected"
                raise
            op.error = type(exc).__name__
            outcome = "unknown" if op.writes and op.dispatched else "error"
            raise OutcomeUnknown(
                "Tool execution failed; inspect the recorded outcome before retrying."
            ) from None
        finally:
            op.outcome = outcome
            op.completed_at = time.monotonic()
            op.state = (
                "successful"
                if outcome == "success"
                else "failed"
                if outcome == "error"
                else outcome
            )
            if op.dispatched:
                self.trace.tool(
                    op.name,
                    op.args,
                    start,
                    time.time(),
                    outcome,
                    duration_s=time.monotonic() - monotonic_start,
                    result_summary=op.result,
                    error_type=op.error,
                )
            else:
                self.trace.event(
                    "controller_decision",
                    function=op.name,
                    decision=outcome,
                    error_type=op.error,
                    duration_s=time.monotonic() - monotonic_start,
                )

    def _conflicts(self, first, second):
        if not (first.writes and second.writes):
            return False
        if first.input_id == second.input_id and re.search(
            r"\b(?:then|after that|afterwards)\b", self._inputs[first.input_id], re.I
        ):
            return True
        if first.name != second.name:
            return False
        identity = {
            k
            for k in first.args.keys() & second.args.keys()
            if k.endswith(("_id", "_type")) or k == "filter_name"
        }
        return not identity or all(first.args[k] == second.args[k] for k in identity)

    async def close(self):
        pending = [o.task for o in self.operations.values() if o.task and not o.task.done()]
        for o in self.operations.values():
            if o.task and not o.task.done() and not o.dispatched:
                o.task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def snapshot(self):
        return {
            "revision": self.revision,
            "transaction": self.transaction,
            "trace_id": self.trace.trace_id,
            "latest_input": self.transcript,
            "heard_history": self.heard_history,
            "actions": [
                {
                    "tool": o.name,
                    "args": o.args,
                    "dispatched": o.dispatched,
                    "completed": o.outcome == "success",
                    "state": o.state,
                    "superseded": o.superseded,
                    "outcome": o.outcome,
                    "result_summary": o.result,
                    "error": o.error,
                    "dependencies": [p.correlation.get("proposal_id") for p in o.dependencies],
                    **o.correlation,
                }
                for o in self.operations.values()
            ],
        }
