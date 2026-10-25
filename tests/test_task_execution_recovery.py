"""Incident regressions: real controller/store, Google HTTP replaced at its boundary."""

import json
from types import SimpleNamespace

import httpx
import pytest
from test_controller_safety import SPECS, controller
from test_productivity import backend

from reprise.coordinator import InvalidCall


@pytest.mark.parametrize("followup", [None, "Six works", "In the morning, right?"])
async def test_edit_intent_rejects_disguised_creation_even_after_clarification(tmp_path, followup):
    calls = []
    b, client = backend(tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "bad"}))
    b.observe_input("Move my Exercise task from seven to six")
    if followup:
        b.observe_input(followup)
    result = await b.call("prepare_plan", {"title": "Update Exercise", "tasks": [{"title": "Exercise", "notes": "6:00 AM"}]})
    assert result["status"] == "error" and "existing" in result["message"].lower()
    assert not calls
    await client.aclose()


async def test_explicit_new_creation_is_not_confused_with_previous_edit(tmp_path):
    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "new-task"}))
    b.observe_input("Move my Exercise task to six")
    b.observe_input("Also create a new task to buy fabric")
    p = (await b.call("prepare_plan", {"title": "Errand", "tasks": [{"title": "Buy fabric"}]}))["plan"]
    result = await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert result["plan"]["actions"][0]["google_id"] == "new-task"
    await client.aclose()


async def test_existing_task_time_change_patches_real_id_and_preserves_date(tmp_path):
    remote = {"id": "real-task", "title": "Exercise", "notes": "7:00 AM", "due": "2030-11-06T00:00:00Z", "status": "needsAction", "etag": '"v1"'}
    calls = []

    def handler(r):
        calls.append(r)
        if r.url.path.endswith("/tasks"):
            return httpx.Response(200, json={"items": [remote.copy()]})
        assert r.url.path.endswith("/real-task")
        if r.method == "PATCH":
            assert r.headers["If-Match"] == '"v1"'
            remote.update(json.loads(r.content), etag='"v2"')
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    b.observe_input("Move my Exercise task from seven to six")
    item = (await b.call("list_tasks", {"query": "Exercise"}))["items"][0]
    p = (await b.call("prepare_resource_change", {"resource_ref": item["resource_ref"], "operation": "update", "changes": {"notes": "6:00 AM"}}))["plan"]
    result = await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert [r.method for r in calls] == ["GET", "GET", "PATCH", "GET"]
    action = result["plan"]["actions"][0]
    assert action["google_id"] == "real-task" and action["result"] == "updated"
    assert remote["notes"] == "6:00 AM" and remote["due"].startswith("2030-11-06")
    assert result["report"][0]["operation"] == "update"
    assert result["report"][0]["resource_id"] == "real-task"
    await client.aclose()


async def test_known_task_list_remains_a_read_locator_after_source_correction(tmp_path):
    calls = []

    def handler(r):
        calls.append(r)
        if r.url.path.endswith("users/@me/lists"):
            return httpx.Response(200, json={"items": [{"id": "test-list", "title": "My Tasks"}]})
        return httpx.Response(200, json={"items": []})

    b, client = backend(tmp_path, handler)
    c = controller(tmp_path, b)
    c.observe_transcript("Read my tasks")
    await c.execute(SPECS["list_task_lists"], {})
    p = (await c.execute(SPECS["prepare_plan"], {"title": "Tasks", "tasks": [{"title": "Exercise", "task_list_id": "test-list"}]}))["plan"]
    await c.execute(SPECS["get_plan"], {"plan_id": p["plan_id"]})
    c.observe_transcript("Actually why do I have two tasks?")
    result = await c.execute(SPECS["list_tasks"], {"task_list_id": "test-list"})
    assert result["status"] == "success"
    assert len(calls) == 2
    assert (await c.execute(SPECS["list_tasks"], {"task_list_id": "invented"}))["status"] == "error"
    assert len(calls) == 2
    await client.aclose()


async def test_explicit_number_survives_model_refresh_without_retargeting(tmp_path):
    rows = [{"id": k, "title": "Exercise", "notes": k, "status": "needsAction", "etag": '"v1"'} for k in ["first-id", "second-id"]]
    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"items": list(rows)}))
    b.observe_input("Read my tasks")
    item = (await b.call("list_tasks", {}))["items"][1]
    b.observe_input("Delete the second one")
    rows.reverse()  # Refresh has different numbering; the user's selection keeps its ID.
    await b.call("list_tasks", {})
    result = await b.call("prepare_resource_change", {"resource_ref": item["resource_ref"], "operation": "delete"})
    assert result["status"] == "success", result
    assert result["plan"]["actions"][0]["target"]["id"] == "second-id"
    assert result["plan"]["requires_confirmation"]
    await client.aclose()


async def test_backend_errors_stop_after_bounded_attempts_until_new_user_input(tmp_path):
    calls = []
    b, client = backend(tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"items": []}))
    c = controller(tmp_path, b)
    c.observe_transcript("Delete the extra task")
    for _ in range(3):
        result = await c.execute(SPECS["prepare_resource_change"], {"resource_ref": "unknown", "operation": "delete"})
        assert result["status"] == "error"
    with pytest.raises(InvalidCall, match="clarify"):
        await c.execute(SPECS["list_tasks"], {})
    assert not calls
    c.observe_transcript("Show all my tasks so I can choose")
    assert (await c.execute(SPECS["list_tasks"], {}))["status"] == "success"
    assert len(calls) == 1
    await client.aclose()


async def test_delegated_note_wording_is_not_an_undecided_action(tmp_path):
    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "unused"}))
    c = controller(tmp_path, b)
    c.observe_transcript("Add a task with warm up or something like that")
    result = await c.execute(SPECS["prepare_plan"], {"title": "Warm up", "tasks": [{"title": "Warm up"}]})
    assert result["status"] == "success"
    await client.aclose()


async def test_create_receipt_cannot_be_reported_as_an_update_by_plan_title(tmp_path):
    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "new-task"}))
    b.observe_input("Create a new task called Exercise")
    p = (await b.call("prepare_plan", {"title": "Updated Exercise", "tasks": [{"title": "Exercise"}]}))["plan"]
    result = await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert result["report"][0]["operation"] == "create"
    assert result["report"][0]["summary"] == 'Created task "Exercise".'
    assert b.response_correction("I've updated your Exercise task.")
    assert b.response_correction("I've created your Exercise task.") is None
    assert b.response_correction("I couldn't update the task.") is None
    await client.aclose()


async def test_failed_delete_does_not_emit_deleted_receipt_or_allow_false_speech(tmp_path):
    remote = {"id": "real-task", "title": "Exercise", "notes": "", "status": "needsAction", "etag": '"v1"'}
    calls = []

    def handler(r):
        calls.append(r)
        if r.url.path.endswith("/tasks"):
            return httpx.Response(200, json={"items": [remote]})
        if r.method == "DELETE":
            return httpx.Response(403, json={"error": {"message": "Denied"}})
        return httpx.Response(200, json=remote)

    b, client = backend(tmp_path, handler)
    b.observe_input("Delete my Exercise task")
    item = (await b.call("list_tasks", {}))["items"][0]
    p = (await b.call("prepare_resource_change", {"resource_ref": item["resource_ref"], "operation": "delete"}))["plan"]
    assert (await b.call("execute_plan", {"plan_id": p["plan_id"]}))["status"] == "approval_required"
    assert not any(r.method == "DELETE" for r in calls)
    b.observe_assistant('Delete Exercise?')
    b.observe_input("Yes")
    result = await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert result["report"][0]["status"] == "failed"
    assert not result["report"][0]["confirmed"]
    assert b.response_correction("I've deleted your task.")
    assert b.response_correction("I am still working on deleting the extra task.")
    assert b.response_correction("I couldn't delete your task.") is None
    await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert sum(r.method == "DELETE" for r in calls) == 1
    await client.aclose()


async def test_live_adapter_ends_validation_loop_with_one_tool_free_clarification(tmp_path):
    from livekit.agents import StopResponse
    from reprise.agent import make_tool

    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"items": []}))
    c = controller(tmp_path, b)
    c.observe_transcript("Delete the extra task")
    notices = []
    context = SimpleNamespace(
        session=SimpleNamespace(generate_reply=lambda **kw: notices.append(kw)),
        speech_handle=SimpleNamespace(interrupted=False, id="response"),
        function_call=SimpleNamespace(call_id="call"),
    )
    tool = make_tool(SPECS["prepare_resource_change"], c)
    for _ in range(3):
        assert (await tool({"resource_ref": "unknown", "operation": "delete"}, context))["status"] == "error"
    for _ in range(5):
        with pytest.raises(StopResponse):
            await tool({"resource_ref": "unknown", "operation": "delete"}, context)
    assert len(notices) == 1
    assert notices[0]["tools"] == [] and notices[0]["tool_choice"] == "none"
    assert "stopped retrying" in notices[0]["instructions"]
    await client.aclose()


async def test_unsupported_spoken_claim_is_kept_and_corrected_from_receipts(tmp_path):
    from reprise.session_events import attach_session_events

    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "unused"}))
    c = controller(tmp_path, b)
    c.observe_transcript("Delete my task")
    callbacks, replies, interrupts = {}, [], []

    class Session:
        current_speech = None

        def on(self, event):
            def register(fn):
                callbacks[event] = fn
            return register

        def interrupt(self):
            interrupts.append(True)

        def generate_reply(self, **kw):
            replies.append(kw)

    attach_session_events(Session(), c)
    callbacks["conversation_item_added"](SimpleNamespace(item=SimpleNamespace(
        role="assistant", text_content="I've deleted the task.", id="message", interrupted=False
    )))
    es = [json.loads(s) for s in c.trace.path.read_text().splitlines()]
    assert any(e["event"] == "conversation_item" and e["text"] == "I've deleted the task." for e in es)
    assert any(e["event"] == "assistant_claim_unverified" for e in es)
    assert len(replies) == len(interrupts) == 1
    assert replies[0]["tools"] == []
    assert "cannot confirm" in replies[0]["instructions"]
    await client.aclose()


@pytest.mark.parametrize("new_title", ["Buy fabric", "Buy Exercise equipment"])
async def test_mixed_create_does_not_bypass_existing_task_edit_guard(tmp_path, new_title):
    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "bad"}))
    b.observe_input("Move my Exercise task from seven to six and create a new task to " + new_title)
    result = await b.call("prepare_plan", {"title": "Mixed", "tasks": [{"title": "Exercise", "notes": "6:00"}, {"title": new_title}]})
    assert result["status"] == "error"
    valid = await b.call("prepare_plan", {"title": "New", "tasks": [{"title": new_title}]})
    assert valid["status"] == "success"
    await client.aclose()


async def test_natural_explicit_addition_after_edit_remains_creation(tmp_path):
    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "new-task"}))
    b.observe_input("Move my Exercise task to six")
    b.observe_input("Add buying fabric to my tasks")
    assert (await b.call("prepare_plan", {"title": "New", "tasks": [{"title": "Buy fabric"}]}))["status"] == "success"
    await client.aclose()


async def test_response_evidence_is_bound_to_named_resource_and_current_request(tmp_path):
    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "fabric-task"}))
    b.observe_input("Create a new task to buy fabric")
    p = (await b.call("prepare_plan", {"title": "New", "tasks": [{"title": "Buy fabric"}]}))["plan"]
    await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert b.response_correction("I've created your Pay rent task.")
    b.observe_input("Create a new task to call Mom")
    assert b.response_correction("I've created your Call Mom task.")
    assert b.response_correction("I've created the task.")
    await client.aclose()


async def test_completed_summary_and_pending_confirmation_can_coexist(tmp_path):
    remote = {"id": "old-task", "title": "Old task", "notes": "", "status": "needsAction", "etag": '"v1"'}
    def handler(r):
        return httpx.Response(200, json={"id": "fabric-task"} if r.method == "POST" else {"items": [remote]})
    b, client = backend(tmp_path, handler)
    b.observe_input("Create a new task to buy fabric")
    p = (await b.call("prepare_plan", {"title": "New", "tasks": [{"title": "Buy fabric"}]}))["plan"]
    await b.call("execute_plan", {"plan_id": p["plan_id"]})
    b.observe_input("Also delete the Old task")
    item = (await b.call("list_tasks", {}))["items"][0]
    pending = (await b.call("prepare_resource_change", {"resource_ref": item["resource_ref"], "operation": "delete"}))["plan"]
    text = "I've created Buy fabric. Shall I delete Old task?"
    assert b.response_correction(text) is None
    b.observe_assistant(text)
    b.observe_input("Yes")
    assert b.store.get(b.room, pending["plan_id"])["approved"]
    await client.aclose()


@pytest.mark.parametrize("text", ["Move Exercise to the second of November", "Move Exercise to November second."])
async def test_ordinal_date_is_not_authority_to_select_second_duplicate(tmp_path, text):
    rows = [{"id": str(i), "title": "Exercise", "status": "needsAction", "etag": '"v1"'} for i in [1, 2]]
    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"items": rows}))
    b.observe_input("Read Exercise tasks")
    items = (await b.call("list_tasks", {}))["items"]
    b.observe_input(text)
    result = await b.call("prepare_resource_change", {"resource_ref": items[1]["resource_ref"], "operation": "update", "changes": {"due_date": "2030-11-02"}})
    assert result["status"] == "error"
    await client.aclose()


async def test_old_response_cannot_interrupt_or_confirm_new_intent(tmp_path):
    from reprise.session_events import attach_session_events

    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "unused"}))
    c = controller(tmp_path, b)
    c.observe_transcript("Delete the old task")
    old_input = c.current_input_id
    c.response_contexts["old-response"] = {"input_id": old_input}
    c.observe_transcript("Actually create a new task")
    callbacks, changes = {}, []

    class Session:
        current_speech = SimpleNamespace(id="old-response")

        def on(self, event):
            def register(fn):
                callbacks[event] = fn
            return register

        def interrupt(self):
            changes.append("interrupt")

        def generate_reply(self, **kw):
            changes.append(kw)

    attach_session_events(Session(), c)
    callbacks["conversation_item_added"](SimpleNamespace(item=SimpleNamespace(
        role="assistant", text_content="I've deleted the task.", id="old-message", interrupted=False
    )))
    assert not changes
    assert b.confirmation_context is None
    await client.aclose()


async def test_resource_title_words_do_not_change_receipt_kind(tmp_path):
    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "actual-task"}))
    b.observe_input("Create a task called Email Alex")
    p = (await b.call("prepare_plan", {"title": "New", "tasks": [{"title": "Email Alex"}]}))["plan"]
    await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert b.response_correction("I've created the Email Alex task.") is None
    await client.aclose()
