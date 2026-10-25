"""Native Google semantic judge, isolated from the agent and upstream files."""

import ast
import hashlib
import json
import math
import os
import random
import threading
import time
import uuid
from email.utils import parsedate_to_datetime
from pathlib import Path

from .config import ROOT


class JudgeFailure(RuntimeError):
    """A case is unjudged; never replace this with an exact-match decision."""


def retry_delay(exc, attempt, *, jitter=lambda: random.uniform(0, 2)):
    """Bounded retry policy for transport/quota failures; never retry bad judgments."""
    import httpx

    status = getattr(exc, "code", None)
    if attempt >= 3 or not (
        status in {429, 500, 502, 503, 504}
        or isinstance(exc, (httpx.TransportError, ConnectionError, TimeoutError))
    ):
        return None
    delay = min(60, 20 * 2**attempt) + jitter()
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    header = headers.get("Retry-After") or headers.get("retry-after")
    if header:
        try:
            wait = float(header)
        except ValueError:
            try:
                wait = parsedate_to_datetime(header).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                wait = 0
        if wait > 120:
            return None  # Pause instead of violating Retry-After or waiting indefinitely.
        delay = max(delay, wait)
    return delay


def read_diagnostics(path, *, repair=False):
    """Quarantine only an incomplete final append; interior corruption is fatal."""
    path = Path(path)
    if not path.exists():
        return []
    data = path.read_bytes()
    records, offset = [], 0
    lines = data.splitlines(keepends=True)
    for index, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError):
            if index != len(lines) - 1 or line.endswith(b"\n"):
                raise
            if repair:
                backup = path.with_name("judge-diagnostics.partial-" + uuid.uuid4().hex)
                backup.write_bytes(line)
                backup.chmod(0o600)
                path.write_bytes(data[:offset])
            break
        offset += len(line)
    if repair and data and records and offset == len(data) and not data.endswith(b"\n"):
        with path.open("ab") as stream:
            stream.write(b"\n")
    return records


def parse_decision(kind, raw):
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[-1].strip() != "```":
            raise ValueError("Unclosed JSON fence")
        raw = "\n".join(lines[1:-1])
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    if kind in {"argument", "response"}:
        if type(value.get("correct")) is not bool or not isinstance(value.get("explanation"), str):
            raise ValueError("Expected boolean correct and string explanation")
    elif kind == "key_information":
        for key in ("filler_sentence", "key_info_sentence"):
            if not isinstance(value.get(key), str):
                raise ValueError(f"Missing string {key}")
        for prefix in ("filler", "key_info"):
            start, end = value.get(f"{prefix}_start_time"), value.get(f"{prefix}_end_time")
            if (
                prefix == "filler"
                and not value["filler_sentence"]
                and start is None
                and end is None
            ):
                continue
            if any(type(t) not in (int, float) or not math.isfinite(t) for t in (start, end)):
                raise ValueError("Invalid information timestamps")
            if start < 0 or end < start:
                raise ValueError("Invalid information timestamp order")
    else:
        raise ValueError("Unknown judgment criteria")
    return value


class RateBudget:
    """One serial queue; rolling request AND input+maximum-output token budget."""

    def __init__(self, rpm=28, tpm=14000, *, clock=time.time, sleep=time.sleep, history=()):
        self.rpm, self.tpm = rpm, tpm
        self.clock, self.sleep = clock, sleep
        self.history = list(history)
        self.lock = threading.Lock()

    def acquire(self, reserved):
        with self.lock:
            self._acquire(reserved)

    def _acquire(self, reserved):
        if reserved > self.tpm:
            raise ValueError("Single request exceeds conservative token budget")
        while True:
            now = self.clock()
            self.history = [(t, n) for t, n in self.history if now - t < 86400]
            recent = [(t, n) for t, n in self.history if now - t < 60.1]
            if len(self.history) >= 14400:
                raise JudgeFailure("Daily request budget exhausted; checkpoint retained")
            if len(recent) < self.rpm and sum(n for _, n in recent) + reserved <= self.tpm:
                self.history.append((now, reserved))
                return
            self.sleep(max(0.01, min(t for t, _ in recent) + 60.1 - now))


class GemmaJudge:
    def __init__(self, directory, *, model="gemma-4-31b-it", client=None, upstream=None):
        if model not in {"gemma-4-31b-it", "gemma-4-26b-a4b-it"}:
            raise ValueError("Only the explicitly authorized Gemma models are allowed")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "judge-diagnostics.jsonl"
        self.model, self.client = model, client
        self.upstream = Path(upstream or ROOT / "third_party/fdb/v3")
        self.cache, history = {}, []
        self.key_locks = {}
        self.key_lock_guard = threading.Lock()
        for record in read_diagnostics(self.path, repair=True):
            history.append((record["started_at"], record["reserved_tokens"]))
            if not record["error"]:
                self.cache[record["cache_key"]] = record
                # Reuse earlier records from the same pinned upstream criteria,
                # even when the former key included JSON object insertion order.
                canonical = self._cache_key(
                    record["kind"],
                    record["inputs"],
                    record["model"],
                    500 if record["kind"] == "key_information" else 200,
                )
                self.cache.setdefault(canonical, record)
        self.budget = RateBudget(history=history)

    def _expression(self, filename, function, variable, variables):
        """Read only the original prompt expression; don't invoke upstream clients."""
        tree = ast.parse((self.upstream / filename).read_text())
        nodes = (
            tree.body
            if function is None
            else next(
                n.body for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function
            )
        )
        assignment = next(
            n
            for n in ast.walk(ast.Module(body=nodes, type_ignores=[]))
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == variable for t in n.targets)
        )
        expression = ast.Expression(body=assignment.value)
        return eval(
            compile(expression, str(self.upstream / filename), "eval"),
            {"__builtins__": {}, "json": json},
            variables,
        )

    def argument(self, expected, actual, function):
        prompt = self._expression(
            "evaluate_tool_calls.py",
            "llm_judge_argument",
            "prompt",
            {"expected_args": expected, "actual_args": actual, "function_name": function},
        )
        return self._judge(
            "argument", prompt, {"function": function, "expected": expected, "actual": actual}
        )

    def response(self, expected, transcript):
        prompt = self._expression(
            "evaluate_tool_calls.py",
            "llm_judge_response",
            "prompt",
            {"expected_intent": expected, "actual_transcript": transcript},
        )
        return self._judge("response", prompt, {"expected": expected, "transcript": transcript})

    def key_information(self, user_end, chunks, calls):
        system = self._expression("analyze_tool_latency.py", None, "SYSTEM_PROMPT", {})
        prompt = self._expression(
            "analyze_tool_latency.py",
            "identify_key_info",
            "user_prompt",
            {"user_end": user_end, "asr_chunks": chunks, "tool_calls": calls},
        )
        result = self._judge(
            "key_information",
            prompt,
            {"user_end": user_end, "chunks": chunks, "calls": calls},
            system=system,
            max_output=500,
        )
        decision = result["decision"]
        starts = {c["timestamp"][0] for c in chunks}
        ends = {c["timestamp"][1] for c in chunks}
        if (
            decision["key_info_start_time"] not in starts
            or decision["key_info_end_time"] not in ends
        ):
            raise JudgeFailure("Judge timestamps do not match supplied ASR boundaries")
        return result

    def _judge(self, kind, prompt, inputs, *, system=None, max_output=200):
        criteria_sha = hashlib.sha256(
            json.dumps({"prompt": prompt, "system": system}, sort_keys=True).encode()
        ).hexdigest()
        key = self._cache_key(kind, inputs, self.model, max_output)
        with self.key_lock_guard:
            lock = self.key_locks.setdefault(key, threading.Lock())
        with lock:
            return self._request(key, criteria_sha, kind, prompt, inputs, system, max_output)

    def _cache_key(self, kind, inputs, model, max_output):
        source = (
            "analyze_tool_latency.py" if kind == "key_information" else "evaluate_tool_calls.py"
        )
        criteria_source = hashlib.sha256((self.upstream / source).read_bytes()).hexdigest()
        return hashlib.sha256(
            json.dumps(
                {
                    "kind": kind,
                    "model": model,
                    "inputs": inputs,
                    "criteria_source_sha256": criteria_source,
                    "temperature": 0,
                    "thinking": "minimal",
                    "max_output_tokens": max_output,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def _request(self, key, criteria_sha, kind, prompt, inputs, system, max_output):
        if key in self.cache:
            return {**self.cache[key], "cache_hit": True}
        if self.client is None:
            from google import genai

            self.client = genai.Client(
                api_key=os.environ["GOOGLE_API_KEY_2"],
                http_options={
                    "api_version": "v1beta",
                    "timeout": 60000,
                    "retry_options": {"attempts": 1},
                },
            )
        # UTF-8 byte count upper-bounds text tokens; reserve full output cap too.
        reserved = len(prompt.encode()) + len((system or "").encode()) + 64 + max_output
        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=max_output,
            thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            system_instruction=system,
        )
        for attempt in range(4):
            self.budget.acquire(reserved)
            started, tick = time.time(), time.monotonic()
            record = {
                "judgment_id": uuid.uuid4().hex,
                "cache_key": key,
                "kind": kind,
                "model": self.model,
                "criteria_sha256": criteria_sha,
                "inputs": inputs,
                "prompt": prompt,
                "system_instruction": system,
                "started_at": started,
                "reserved_tokens": reserved,
                "attempt": attempt,
                "response": None,
                "decision": None,
                "usage": None,
                "error": None,
                "cache_hit": False,
            }
            delay = None
            try:
                response = self.client.models.generate_content(
                    model=self.model, contents=prompt, config=config
                )
                record["response"] = response.text
                record["response_model"] = response.model_version
                usage = response.usage_metadata
                record["usage"] = usage.model_dump(mode="json") if usage else None
                if (
                    response.model_version
                    and response.model_version.removeprefix("models/") != self.model
                ):
                    raise ValueError("Response model differs from requested model")
                record["decision"] = parse_decision(kind, response.text or "")
            except Exception as exc:
                delay = retry_delay(exc, attempt)
                record["error"] = {
                    "type": type(exc).__name__,
                    "status_code": getattr(exc, "code", None),
                }
                message = str(getattr(exc, "message", "") or "")[:2000]
                for name in (
                    "GOOGLE_API_KEY",
                    "GOOGLE_API_KEY_2",
                    "LIVEKIT_API_KEY",
                    "LIVEKIT_API_SECRET",
                ):
                    secret = os.getenv(name)
                    if secret:
                        message = message.replace(secret, "[redacted]")
                record["error"]["message"] = message
                record["retry_delay_s"] = delay
            record["latency_s"] = time.monotonic() - tick
            with self.path.open("a") as stream:
                stream.write(json.dumps(record) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.path.chmod(0o600)
            if not record["error"]:
                self.cache[key] = record
                return record
            if delay is None:
                raise JudgeFailure(
                    f"{kind} judge failed: {record['error']}; id={record['judgment_id']}"
                )
            time.sleep(delay)
        raise AssertionError("Unreachable")
