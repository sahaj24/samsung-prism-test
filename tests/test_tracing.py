import json
from types import SimpleNamespace

import httpx
import pytest
from test_controller_safety import SPECS, controller, prepare
from test_productivity import backend

from reprise.catalog import CATALOG
from reprise.coordinator import InvalidCall
from reprise.trace import Trace


async def test_raw_invalid_proposal_is_logged_before_validation(tmp_path):
    from test_coordinator import make

    c = make(tmp_path)
    c.observe_transcript("Track parcel 7", is_final=True)
    with pytest.raises(InvalidCall):
        await c.execute(
            CATALOG["track_order"],
            {"order_id": "Q-7", "extra": True},
            provider_call_id="gemini-call-9",
            response_id="speech-2",
        )
    events = [json.loads(line) for line in c.trace.path.read_text().splitlines()]
    raw = next(e for e in events if e["event"] == "tool_raw_proposed")
    rejected = next(e for e in events if e["event"] == "tool_validation_failed")
    assert raw["args"] == {"order_id": "Q-7", "extra": True}
    assert rejected["proposal_id"] == raw["proposal_id"]
    assert raw["provider_call_id"] == "gemini-call-9"
    assert raw["response_id"] == "speech-2"
    assert not any(e["event"] == "tool_dispatch" for e in events)


async def test_workspace_trace_links_input_dispatch_http_receipt_and_response(tmp_path):
    b, client = backend(
        tmp_path,
        lambda r: httpx.Response(
            200,
            json={"id": "task-9", "etag": "receipt-tag"},
            headers={"x-request-id": "google-request-3"},
        ),
    )
    c = controller(tmp_path, b)
    b.trace = c.trace
    b.google.trace = c.trace
    plan = await prepare(c)
    c.observe_transcript("Confirm the plan", is_final=True)
    await c.execute(
        SPECS["execute_plan"],
        {"plan_id": plan["plan_id"]},
        provider_call_id="call-2",
        response_id="speech-3",
    )
    from reprise.session_events import attach_session_events

    class Session:
        def __init__(self):
            self.callbacks = {}

        def on(self, name):
            def register(fn):
                self.callbacks[name] = fn
                return fn

            return register

    session = Session()
    attach_session_events(session, c)
    session.callbacks["conversation_item_added"](
        SimpleNamespace(
            item=SimpleNamespace(
                role="assistant",
                id="message-7",
                text_content="The task was added.",
                interrupted=False,
            )
        )
    )
    events = [json.loads(line) for line in c.trace.path.read_text().splitlines()]
    dispatch = next(
        e for e in events if e["event"] == "tool_dispatch" and e["function"] == "execute_plan"
    )
    http = next(e for e in events if e["event"] == "external_api_completed")
    outcome = next(
        e for e in events if e["event"] == "tool_executed" and e["function"] == "execute_plan"
    )
    response = next(e for e in events if e["event"] == "assistant_response")
    assert dispatch["proposal_id"] == http["proposal_id"] == outcome["proposal_id"]
    assert http["plan_id"] == plan["plan_id"] and http["operation_id"]
    assert http["google_id"] == "task-9" and http["status_code"] == 200
    assert http["provider_request_id"] == "google-request-3"
    assert http["duration_s"] >= 0 and outcome["duration_s"] >= 0
    assert outcome["result_summary"]["plan"]["actions"][0]["receipt"]["id"] == "task-9"
    assert response["related_proposal_ids"] == [dispatch["proposal_id"]]
    assert all(e["trace_id"] == dispatch["trace_id"] and e["event_id"] for e in events)
    assert dispatch["input_id"] and dispatch["request_id"] == http["request_id"]
    serialized = c.trace.path.read_text()
    assert "test-token" not in serialized and "refresh" not in serialized
    await client.aclose()


def test_trace_redacts_secrets_and_keeps_usage_counts(tmp_path):
    trace = Trace("room", tmp_path)
    trace.event(
        "test",
        payload={
            "authorization": "Bearer secret",
            "access_token": "secret",
            "input_tokens": 12,
            "body": "x" * 3000,
        },
    )
    event = json.loads(trace.path.read_text())
    assert event["payload"]["authorization"] == "[redacted]"
    assert event["payload"]["access_token"] == "[redacted]"
    assert event["payload"]["input_tokens"] == 12
    assert len(event["payload"]["body"]) < 2100


def test_sdk_metrics_and_usage_snapshots_are_recorded_without_inventing_cost(tmp_path):
    from test_coordinator import make

    from reprise.session_events import attach_session_events

    c = make(tmp_path)
    callbacks = {}

    class Session:
        def on(self, name):
            def register(fn):
                callbacks[name] = fn
                return fn

            return register

    attach_session_events(Session(), c)
    callbacks["metrics_collected"](
        SimpleNamespace(
            metrics={
                "type": "realtime_model_metrics",
                "request_id": "provider-1",
                "input_tokens": 10,
                "output_tokens": 3,
                "ttft": 0.21,
                "input_token_details": {"audio_tokens": 8, "text_tokens": 2},
            }
        )
    )
    callbacks["session_usage_updated"](SimpleNamespace(usage={"total_tokens": 13}))
    events = [json.loads(line) for line in c.trace.path.read_text().splitlines()]
    assert events[0]["metrics"]["input_token_details"]["audio_tokens"] == 8
    assert events[1]["usage"]["total_tokens"] == 13
    assert not any("cost" in e for e in events)


async def test_overlapping_api_calls_keep_their_own_correlation(tmp_path):
    import asyncio

    trace = Trace("room", tmp_path)

    async def emit(number):
        with trace.bind(proposal_id=number):
            await asyncio.sleep(0.001 if number == "first" else 0)
            trace.event("external_api_completed")

    await asyncio.gather(emit("first"), emit("second"))
    events = [json.loads(line) for line in trace.path.read_text().splitlines()]
    assert [e["proposal_id"] for e in events] == ["second", "first"]
    assert [e["sequence"] for e in events] == [1, 2]


def test_real_sdk_usage_dataclass_retains_model_token_details(tmp_path):
    from livekit.agents.metrics.usage import AgentSessionUsage, LLMModelUsage
    from test_coordinator import make

    from reprise.session_events import attach_session_events

    c = make(tmp_path)
    callbacks = {}

    class Session:
        def on(self, name):
            def register(fn):
                callbacks[name] = fn
                return fn

            return register

    attach_session_events(Session(), c)
    callbacks["session_usage_updated"](
        SimpleNamespace(
            usage=AgentSessionUsage(
                model_usage=[
                    LLMModelUsage(
                        provider="google",
                        model="gemini-live",
                        input_audio_tokens=17,
                        output_audio_tokens=9,
                    )
                ]
            )
        )
    )
    event = json.loads(c.trace.path.read_text())
    assert event["usage"]["model_usage"][0]["input_audio_tokens"] == 17
    assert event["usage"]["model_usage"][0]["output_audio_tokens"] == 9


def test_snapshot_retains_outcome_evidence_with_private_permissions(tmp_path):
    trace = Trace("room", tmp_path)
    trace.write_snapshot({"actions": [{"outcome": "unknown", "args": {"access_token": "secret"}}]})
    path = tmp_path / "snapshot.json"
    assert path.stat().st_mode & 0o777 == 0o600
    value = json.loads(path.read_text())
    assert value["actions"][0]["outcome"] == "unknown"
    assert value["actions"][0]["args"]["access_token"] == "[redacted]"
