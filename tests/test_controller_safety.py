"""Controller regressions: actual controller/store, only Google HTTP is mocked."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
from test_productivity import backend

from reprise.config import Settings
from reprise.coordinator import Coordinator, Superseded
from reprise.productivity import PRODUCTIVITY_TOOLS
from reprise.trace import Trace

SPECS = {s.name: s for s in PRODUCTIVITY_TOOLS}


def controller(tmp_path, b, **settings):
    return Coordinator(
        b,
        Trace("room-a", tmp_path / "trace"),
        replace(Settings(settle_ms=0, write_settle_ms=0), **settings),
    )


async def prepare(c):
    c.observe_transcript("Add a task to buy coffee", is_final=True)
    return (
        await c.execute(
            SPECS["prepare_plan"], {"title": "Errands", "tasks": [{"title": "Buy coffee"}]}
        )
    )["plan"]


async def test_approval_required_is_not_cached_as_a_completed_write(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "task-1"})
    )
    c = controller(tmp_path, b)
    c.observe_transcript("Send an email to alex@example.com", is_final=True)
    plan = (await c.execute(SPECS["prepare_plan"], {"title":"Mail", "messages":[{
        "to":["alex@example.com"], "subject":"Meeting time", "body":"At five.", "delivery":"send"
    }]}))["plan"]
    args = {"plan_id": plan["plan_id"]}
    assert (await c.execute(SPECS["execute_plan"], args))["status"] == "approval_required"
    c.observe_transcript("Confirm the plan", is_final=True)
    assert (await c.execute(SPECS["execute_plan"], args))["plan"]["status"] == "completed"
    assert len(calls) == 1
    await client.aclose()


async def test_correction_revokes_dashboard_approval_without_replacement_plan(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "wrong"})
    )
    c = controller(tmp_path, b)
    plan = await prepare(c)
    b.store.approve("room-a", plan["plan_id"])
    c.observe_transcript("Actually do not add coffee", is_final=True)
    result = await c.execute(SPECS["execute_plan"], {"plan_id": plan["plan_id"]})
    assert result["plan"]["status"] == "superseded"
    assert result["plan"]["approved"] is False
    assert not calls
    await client.aclose()


async def test_interim_confirmation_cannot_authorize_write(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "wrong"})
    )
    c = controller(tmp_path, b, gate_timeout_s=0.04)
    plan = await prepare(c)
    c.observe_transcript("Confirm the plan", is_final=False)
    try:
        result = await c.execute(SPECS["execute_plan"], {"plan_id": plan["plan_id"]})
        assert result["status"] == "approval_required"
    except Superseded:
        pass
    assert not calls
    # Same text promoted to final must be observed even though its spelling is unchanged.
    c.observe_transcript("Confirm the plan", is_final=True)
    result = await c.execute(SPECS["execute_plan"], {"plan_id": plan["plan_id"]})
    assert result["plan"]["status"] == "completed"
    assert len(calls) == 1
    await client.aclose()


async def test_response_interruption_preserves_approved_current_plan(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "wrong"})
    )
    c = controller(tmp_path, b)
    plan = await prepare(c)
    b.store.approve("room-a", plan["plan_id"])
    result = await c.execute(
        SPECS["execute_plan"], {"plan_id": plan["plan_id"]}, interrupted=lambda: True
    )
    assert result["plan"]["status"] == "completed"
    assert len(calls) == 1
    await client.aclose()


async def test_typed_input_updates_controller_before_reply_generation(tmp_path):
    from types import SimpleNamespace

    from test_coordinator import make

    from reprise.agent import make_text_input_handler

    c = make(tmp_path)
    c.observe_transcript("Track order DD42", is_final=True)
    old_revision = c.revision

    class Session:
        @asynccontextmanager
        async def _claim_user_turn(self):
            yield

        async def interrupt(self):
            assert c.revision > old_revision
            assert c.transcript == "Actually cancel that order"

        def generate_reply(self, **kwargs):
            assert c.input_final
            assert kwargs == {"user_input": "Actually cancel that order"}

    await make_text_input_handler(c)(Session(), SimpleNamespace(text="Actually cancel that order"))


async def test_interrupted_response_during_preparation_preserves_intent(tmp_path):
    from test_coordinator import Backend, make

    from reprise.catalog import CATALOG

    b = Backend(prepare=0.04)
    c = make(tmp_path, b)
    c.observe_transcript("Use savings", is_final=True)
    interrupted = False
    task = asyncio.create_task(
        c.execute(
            CATALOG["modify_autopay"],
            {"bill_type": "phone", "source_account": "savings"},
            interrupted=lambda: interrupted,
        )
    )
    await asyncio.sleep(0.01)
    interrupted = True
    assert (await task)["status"] == "success"
    assert len(b.calls) == 1


async def test_backend_itself_blocks_dashboard_approval_during_interim_input(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "wrong"})
    )
    plan = (await b.call("prepare_plan", {"title": "Task", "tasks": [{"title": "One"}]}))["plan"]
    b.store.approve("room-a", plan["plan_id"])
    b.observe_input("Confirm the plan", is_final=False)
    result = await b.call("execute_plan", {"plan_id": plan["plan_id"]})
    assert result["status"] == "approval_required"
    assert not calls
    await client.aclose()


async def test_dashboard_approval_is_a_revision_bound_receipt(tmp_path):
    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "unused"}))
    c = controller(tmp_path, b)
    plan = await prepare(c)
    approved = b.store.approve("room-a", plan["plan_id"])
    assert approved["approval"]["source"] == "dashboard"
    assert approved["approval"]["revision"] == plan["revision"]
    assert approved["approval"]["id"] and approved["approval"]["at"] > 0
    c.observe_transcript("Actually do not add coffee", is_final=True)
    revoked = b.store.get("room-a", plan["plan_id"])
    assert not revoked["approved"]
    assert revoked["approval"]["id"] == approved["approval"]["id"]
    await client.aclose()


async def test_typed_handler_preserves_livekit_user_turn_serialization(tmp_path):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from test_coordinator import make

    from reprise.agent import make_text_input_handler

    c = make(tmp_path)

    class Session:
        claimed = False

        @asynccontextmanager
        async def _claim_user_turn(self):
            self.claimed = True
            try:
                yield
            finally:
                self.claimed = False

        async def interrupt(self):
            assert self.claimed

        def generate_reply(self, **kwargs):
            assert self.claimed

    await make_text_input_handler(c)(Session(), SimpleNamespace(text="Make that Friday"))
