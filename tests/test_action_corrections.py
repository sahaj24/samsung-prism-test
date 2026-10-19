"""Action policy and correction regressions; only the remote HTTP boundary is simulated."""

import asyncio
import json

import httpx
import pytest
from test_productivity import backend, request_plan


def task_plan():
    return {"title": "Errand", "tasks": [{"title": "Collect parcel"}]}


def send_plan():
    return {"title": "Mail", "messages": request_plan()["messages"]}


async def test_personal_task_executes_without_confirmation_and_duplicate_dispatch(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "task-real"})
    )
    b.observe_input("Add a task to collect the parcel")
    p = (await b.call("prepare_plan", task_plan()))["plan"]
    results = await asyncio.gather(
        *(b.call("execute_plan", {"plan_id": p["plan_id"]}) for _ in range(2))
    )
    assert b.store.get(b.room, p["plan_id"])["status"] == "completed"
    assert len(calls) == 1
    assert not results[0]["plan"]["requires_confirmation"]
    await client.aclose()


async def test_mixed_risk_executes_task_but_holds_send_until_contextual_yes(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": f"id-{len(calls)}"})
    )
    b.observe_input("Add a task and send the email")
    p = (await b.call("prepare_plan", {**send_plan(), "tasks": task_plan()["tasks"]}))["plan"]
    result = await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert [a["status"] for a in result["plan"]["actions"]] == ["completed", "approval_required"]
    assert len(calls) == 1 and result["status"] == "approval_required"
    b.observe_input("yeah")  # no assistant confirmation question: cannot send
    await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert len(calls) == 1
    b.observe_assistant("Send the email Meeting time to alex@example.com?")
    b.observe_input("sure")
    result = await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert result["plan"]["status"] == "completed" and len(calls) == 2
    await client.aclose()


async def test_correction_revokes_contextual_consent_and_skips_old_action(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "bad"})
    )
    p = (await b.call("prepare_plan", send_plan()))["plan"]
    b.observe_assistant("Send the email Meeting time to alex@example.com?")
    b.observe_input("yes but change the recipient")
    b.observe_input("yeah")
    result = await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert result["plan"]["status"] == "superseded" and not calls
    await client.aclose()


async def test_low_risk_correction_during_token_refresh_prevents_dispatch(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(r):
        calls.append(r)
        if "oauth2" in str(r.url):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        return httpx.Response(200, json={"id": "bad"})

    b, client = backend(tmp_path, handler)
    b.google.save_tokens({"refresh_token": "refresh"})
    p = (await b.call("prepare_plan", task_plan()))["plan"]
    job = asyncio.create_task(b.call("execute_plan", {"plan_id": p["plan_id"]}))
    await asyncio.wait_for(entered.wait(), 1)
    b.observe_input("Actually cancel the parcel task")
    release.set()
    result = await job
    assert len(calls) == 1 and result["plan"]["actions"][0]["status"] == "not_executed"
    await client.aclose()


def event_plan():
    return {
        "title": "Coffee",
        "calendar_events": [
            {
                "title": "Coffee",
                "start": "2030-10-01T17:00:00+05:30",
                "end": "2030-10-01T17:30:00+05:30",
            }
        ],
    }


async def created_event(b):
    p = (await b.call("prepare_plan", event_plan()))["plan"]
    return (await b.call("execute_plan", {"plan_id": p["plan_id"]}))["plan"]


async def test_calendar_correction_patches_original_id_and_preserves_duration(tmp_path):
    calls = []
    remote = {}

    def handler(r):
        calls.append(r)
        if r.method == "POST":
            remote.update(json.loads(r.content), id="returned-event", etag='"v1"')
        elif r.method == "PATCH":
            assert r.url.path.endswith("/returned-event")
            assert r.headers["If-Match"] == '"v1"'
            remote.update(json.loads(r.content), etag='"v2"')
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    p = await created_event(b)
    b.observe_input("Actually move that coffee to six")
    change = await b.call(
        "prepare_change",
        {
            "plan_id": p["plan_id"],
            "action_index": 0,
            "operation": "update",
            "changes": {"start": "2030-10-01T18:00:00+05:30"},
        },
    )
    assert change["status"] == "success", change
    result = await b.call("execute_plan", {"plan_id": change["plan"]["plan_id"]})
    action = result["plan"]["actions"][0]
    assert action["result"] == "updated" and action["google_id"] == "returned-event"
    assert remote["start"]["dateTime"] == "2030-10-01T18:00:00+05:30"
    assert remote["end"]["dateTime"] == "2030-10-01T18:30:00+05:30"
    await b.call("execute_plan", {"plan_id": change["plan"]["plan_id"]})
    assert [r.method for r in calls] == ["POST", "GET", "PATCH", "GET"]
    await client.aclose()


@pytest.mark.parametrize("kind", ["calendar", "task"])
async def test_undo_deletes_actual_resource_only_after_confirmation_and_verifies_absence(
    tmp_path, kind
):
    calls = []
    remote = {}
    deleted = False

    def handler(r):
        nonlocal deleted
        calls.append(r)
        if r.method == "POST":
            remote.update(json.loads(r.content), id="real-id", etag='"v1"')
        if r.method == "DELETE":
            deleted = True
            return httpx.Response(204)
        if deleted:
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND"}})
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    p = (await b.call("prepare_plan", event_plan() if kind == "calendar" else task_plan()))["plan"]
    await b.call("execute_plan", {"plan_id": p["plan_id"]})
    b.observe_input("Undo that")
    change = await b.call(
        "prepare_change", {"plan_id": p["plan_id"], "action_index": 0, "operation": "delete"}
    )
    assert change["status"] == "success", change
    cid = change["plan"]["plan_id"]
    assert (await b.call("execute_plan", {"plan_id": cid}))["status"] == "approval_required"
    assert not deleted
    b.store.approve(b.room, cid)
    result = await b.call("execute_plan", {"plan_id": cid})
    assert result["plan"]["actions"][0]["result"] == "undone"
    assert result["plan"]["actions"][0]["google_id"] == "real-id"
    await b.call("execute_plan", {"plan_id": cid})
    assert [r.method for r in calls] == ["POST", "GET", "DELETE", "GET"]
    await client.aclose()


async def test_failed_undo_is_failed_and_preserves_original_completion(tmp_path):
    remote = {}
    calls = []

    def handler(r):
        calls.append(r)
        if r.method == "POST":
            remote.update(json.loads(r.content), id="real-id", etag='"v1"')
        if r.method == "DELETE":
            return httpx.Response(403, json={"error": {"status": "FORBIDDEN"}})
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    p = await created_event(b)
    change = await b.call(
        "prepare_change", {"plan_id": p["plan_id"], "action_index": 0, "operation": "delete"}
    )
    cid = change["plan"]["plan_id"]
    b.store.approve(b.room, cid)
    result = await b.call("execute_plan", {"plan_id": cid})
    assert result["plan"]["actions"][0]["status"] == "failed"
    assert result["plan"]["actions"][0].get("result") != "undone"
    assert b.store.get(b.room, p["plan_id"])["actions"][0]["status"] == "completed"
    await client.aclose()


async def test_lost_update_response_is_read_back_not_replayed(tmp_path):
    remote = {}
    calls = []

    def handler(r):
        calls.append(r)
        if r.method == "POST":
            remote.update(json.loads(r.content), id="real-id", etag='"v1"')
        if r.method == "PATCH":
            remote.update(json.loads(r.content), etag='"v2"')
            raise httpx.ReadTimeout("lost receipt", request=r)
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    p = await created_event(b)
    change = await b.call(
        "prepare_change",
        {
            "plan_id": p["plan_id"],
            "action_index": 0,
            "operation": "update",
            "changes": {"title": "Tea"},
        },
    )
    cid = change["plan"]["plan_id"]
    result = await b.call("execute_plan", {"plan_id": cid})
    assert result["plan"]["actions"][0]["status"] == "unknown"
    result = await b.call("reconcile_plan", {"plan_id": cid})
    assert result["plan"]["actions"][0]["result"] == "updated"
    await b.call("execute_plan", {"plan_id": cid})
    assert sum(r.method == "PATCH" for r in calls) == 1
    assert sum(r.method == "POST" for r in calls) == 1
    await client.aclose()


async def test_unresolved_task_creation_cannot_be_compensated_with_guessed_id(tmp_path):
    calls = []

    def handler(r):
        calls.append(r)
        raise httpx.ReadTimeout("lost create", request=r)

    b, client = backend(tmp_path, handler)
    p = (await b.call("prepare_plan", task_plan()))["plan"]
    await b.call("execute_plan", {"plan_id": p["plan_id"]})
    result = await b.call(
        "prepare_change", {"plan_id": p["plan_id"], "action_index": 0, "operation": "delete"}
    )
    assert result["status"] == "error" and len(calls) == 1
    assert "ID" in result["message"]
    await client.aclose()


async def test_sent_email_has_no_automatic_inverse(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "sent-real"})
    )
    p = (await b.call("prepare_plan", send_plan()))["plan"]
    b.store.approve(b.room, p["plan_id"])
    await b.call("execute_plan", {"plan_id": p["plan_id"]})
    result = await b.call(
        "prepare_change", {"plan_id": p["plan_id"], "action_index": 0, "operation": "delete"}
    )
    assert result["status"] == "error" and len(calls) == 1
    await client.aclose()


async def test_failed_readback_after_dispatch_remains_unknown(tmp_path):
    remote = {}
    patched = False

    def handler(r):
        nonlocal patched
        if r.method == "POST":
            remote.update(json.loads(r.content), id="real-id", etag='"v1"')
        if r.method == "PATCH":
            remote.update(json.loads(r.content))
            patched = True
        if r.method == "GET" and patched:
            raise httpx.ReadTimeout("verification unavailable", request=r)
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    p = await created_event(b)
    change = await b.call(
        "prepare_change",
        {
            "plan_id": p["plan_id"],
            "action_index": 0,
            "operation": "update",
            "changes": {"title": "Tea"},
        },
    )
    result = await b.call("execute_plan", {"plan_id": change["plan"]["plan_id"]})
    assert result["plan"]["actions"][0]["status"] == "unknown"
    assert not result["plan"]["actions"][0].get("result")
    await client.aclose()


async def test_task_update_retains_id_and_rejects_stale_remote_version(tmp_path):
    remote = {}
    calls = []

    def handler(r):
        calls.append(r)
        if r.method == "POST":
            remote.update(json.loads(r.content), id="task-real", etag='"v1"')
        if r.method == "PATCH":
            remote.update(json.loads(r.content), etag='"v2"')
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    p = (await b.call("prepare_plan", task_plan()))["plan"]
    await b.call("execute_plan", {"plan_id": p["plan_id"]})
    change = await b.call(
        "prepare_change",
        {
            "plan_id": p["plan_id"],
            "action_index": 0,
            "operation": "update",
            "changes": {"notes": "Bring ID"},
        },
    )
    result = await b.call("execute_plan", {"plan_id": change["plan"]["plan_id"]})
    assert result["plan"]["actions"][0]["google_id"] == "task-real"
    assert remote["notes"] == "Bring ID"
    b.observe_input("Change that task title")
    change = await b.call(
        "prepare_change",
        {
            "plan_id": p["plan_id"],
            "action_index": 0,
            "operation": "update",
            "changes": {"title": "Collect box"},
        },
    )
    remote["title"] = "Externally changed"
    result = await b.call("execute_plan", {"plan_id": change["plan"]["plan_id"]})
    assert result["plan"]["actions"][0]["status"] == "failed"
    assert sum(r.method == "PATCH" for r in calls) == 1
    await client.aclose()


async def test_correction_cannot_recreate_dispatched_action(tmp_path):
    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "real-id"}))
    await created_event(b)
    b.observe_input("Actually make that six")
    changed = event_plan()
    changed["calendar_events"][0]["start"] = "2030-10-01T18:00:00+05:30"
    changed["calendar_events"][0]["end"] = "2030-10-01T18:30:00+05:30"
    result = await b.call("prepare_plan", changed)
    assert result["status"] == "error" and "prepare_change" in result["message"]
    await client.aclose()


async def test_invitations_wait_but_personal_calendar_and_draft_run(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": str(len(calls))})
    )
    args = event_plan()
    args["calendar_events"].append(
        {**args["calendar_events"][0], "title": "Guest meeting", "attendees": ["alex@example.com"]}
    )
    args["messages"] = [{**request_plan()["messages"][0], "delivery": "draft"}]
    p = (await b.call("prepare_plan", args))["plan"]
    r = await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert [a["status"] for a in r["plan"]["actions"]] == [
        "completed",
        "approval_required",
        "completed",
    ]
    assert len(calls) == 2
    b.observe_assistant("Invite alex@example.com to Guest meeting?")
    b.observe_input("go ahead")
    r = await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert r["status"] == "success" and len(calls) == 3
    assert calls[-1].url.params["sendUpdates"] == "all"
    await client.aclose()


async def test_no_second_change_dispatch_while_prior_resource_outcome_is_unknown(tmp_path):
    remote = {}
    calls = []

    def handler(r):
        calls.append(r)
        if r.method == "POST":
            remote.update(json.loads(r.content), id="real-id", etag='"v1"')
        if r.method == "PATCH":
            raise httpx.ReadTimeout("unknown", request=r)
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    p = await created_event(b)
    c1 = (
        await b.call(
            "prepare_change",
            {
                "plan_id": p["plan_id"],
                "action_index": 0,
                "operation": "update",
                "changes": {"title": "Tea"},
            },
        )
    )["plan"]
    await b.call("execute_plan", {"plan_id": c1["plan_id"]})
    # A changed payload must not bypass the uncertain operation on the same ID.
    c2 = (
        await b.call(
            "prepare_change",
            {
                "plan_id": p["plan_id"],
                "action_index": 0,
                "operation": "update",
                "changes": {"title": "Lunch"},
            },
        )
    )["plan"]
    r = await b.call("execute_plan", {"plan_id": c2["plan_id"]})
    assert r["plan"]["actions"][0]["status"] == "not_executed"
    assert sum(req.method == "PATCH" for req in calls) == 1
    await client.aclose()


async def test_post_dispatch_correction_preserves_inflight_receipt_then_updates(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    remote = {}
    calls = []

    async def handler(r):
        calls.append(r)
        if r.method == "POST":
            entered.set()
            await release.wait()
            remote.update(json.loads(r.content), id="real-id", etag='"v1"')
        if r.method == "PATCH":
            remote.update(json.loads(r.content), etag='"v2"')
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    p = (await b.call("prepare_plan", event_plan()))["plan"]
    job = asyncio.create_task(b.call("execute_plan", {"plan_id": p["plan_id"]}))
    await asyncio.wait_for(entered.wait(), 1)
    b.observe_input("Actually make that six")
    early = await b.call(
        "prepare_change",
        {
            "plan_id": p["plan_id"],
            "action_index": 0,
            "operation": "update",
            "changes": {"start": "2030-10-01T18:00:00+05:30"},
        },
    )
    assert early["status"] == "error"
    release.set()
    created = await job
    assert created["plan"]["actions"][0]["google_id"] == "real-id"
    c = (
        await b.call(
            "prepare_change",
            {
                "plan_id": p["plan_id"],
                "action_index": 0,
                "operation": "update",
                "changes": {"start": "2030-10-01T18:00:00+05:30"},
            },
        )
    )["plan"]
    result = await b.call("execute_plan", {"plan_id": c["plan_id"]})
    assert result["plan"]["actions"][0]["result"] == "updated"
    assert sum(req.method == "POST" for req in calls) == 1
    await client.aclose()


async def test_delete_timeout_is_verified_absent_before_being_called_undone(tmp_path):
    remote = {}
    deleted = False
    calls = []

    def handler(r):
        nonlocal deleted
        calls.append(r)
        if r.method == "POST":
            remote.update(json.loads(r.content), id="real-id", etag='"v1"')
        if r.method == "DELETE":
            deleted = True
            raise httpx.ReadTimeout("lost delete", request=r)
        if deleted:
            return httpx.Response(410, json={"error": {}})
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    p = await created_event(b)
    c = (
        await b.call(
            "prepare_change", {"plan_id": p["plan_id"], "action_index": 0, "operation": "delete"}
        )
    )["plan"]
    b.store.approve(b.room, c["plan_id"])
    r = await b.call("execute_plan", {"plan_id": c["plan_id"]})
    assert r["plan"]["actions"][0]["status"] == "unknown"
    r = await b.call("reconcile_plan", {"plan_id": c["plan_id"]})
    assert r["plan"]["actions"][0]["result"] == "undone"
    assert sum(req.method == "DELETE" for req in calls) == 1
    await client.aclose()


async def test_interrupted_or_unrelated_assistant_question_does_not_arm_send(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "bad"})
    )
    p = (await b.call("prepare_plan", send_plan()))["plan"]
    for question, interrupted in [
        ("Send the email Meeting time to alex@example.com?", True),
        ("Are you ready?", False),
    ]:
        b.observe_assistant(question, interrupted=interrupted)
        b.observe_input("yes")
        assert (await b.call("execute_plan", {"plan_id": p["plan_id"]}))[
            "status"
        ] == "approval_required"
    assert not calls
    await client.aclose()


@pytest.mark.parametrize("answer", ["yes", "yeah", "sure", "go ahead", "okay", "yes, do it"])
async def test_natural_affirmative_is_bound_to_presented_email(tmp_path, answer):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "sent-id"})
    )
    p = (await b.call("prepare_plan", send_plan()))["plan"]
    b.observe_assistant("Send the email Meeting time to alex@example.com?")
    b.observe_input(answer)
    r = await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert r["status"] == "success" and len(calls) == 1
    await client.aclose()


def test_late_old_receipt_does_not_overwrite_more_recent_correction(tmp_path):
    from reprise.productivity import PlanStore, action_values

    store = PlanStore(tmp_path / "store.sqlite3")
    p = store.prepare("room-a", "Task", [{"kind": "task", "title": "First"}])
    target = {"room": "room-a", "plan_id": p["plan_id"], "action_index": 0}
    initial = action_values(p["actions"][0])
    second = {**initial, "title": "Second"}
    third = {**initial, "title": "Third"}
    store.remember_resource(target, second, expected=initial)
    store.remember_resource(target, third, expected=second)
    store.remember_resource(target, second, expected=initial)  # delayed receipt from first update
    assert store.get("room-a", p["plan_id"])["actions"][0]["current_values"] == third


async def test_acknowledging_reply_cannot_erase_already_observed_contextual_consent(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "sent-id"})
    )
    p = (await b.call("prepare_plan", send_plan()))["plan"]
    b.observe_assistant("Send the email Meeting time to alex@example.com?")
    b.observe_input("yeah", is_final=True)
    # Gemini may finish its acknowledgment while the controller settlement task is waiting.
    b.observe_assistant("Sending it now.")
    result = await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert result["status"] == "success" and len(calls) == 1
    await client.aclose()
