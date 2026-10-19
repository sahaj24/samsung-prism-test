import json

import httpx
import pytest
from test_productivity import backend


async def test_tasks_lookup_filters_dates_status_and_keeps_actual_ids(tmp_path):
    calls = []

    def handler(r):
        calls.append(r)
        assert r.method == "GET"
        assert r.url.params["showHidden"] == "true"
        assert r.url.params["dueMin"] == "2030-10-02T00:00:00Z"
        assert r.url.params["dueMax"] == "2030-10-03T00:00:00Z"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "actual-task",
                        "title": "Review draft",
                        "status": "needsAction",
                        "due": "2030-10-02T00:00:00Z",
                    },
                    {"id": "done-task", "title": "Review old draft", "status": "completed"},
                ],
                "nextPageToken": "next",
            },
        )

    b, client = backend(tmp_path, handler)
    result = await b.call(
        "list_tasks",
        {
            "query": "Review",
            "due_from": "2030-10-02",
            "due_before": "2030-10-03",
            "task_status": "needsAction",
        },
    )
    assert result["status"] == "success", result
    assert [i["id"] for i in result["items"]] == ["actual-task"]
    assert result["items"][0]["resource_ref"]
    assert result["next_page_token"] == "next"
    assert len(calls) == 1
    await client.aclose()


async def test_calendar_uses_native_search_and_expands_instances(tmp_path):
    def handler(r):
        assert r.url.params["q"] == "Coffee"
        assert r.url.params["singleEvents"] == "true"
        assert r.url.params["timeMin"] == "2030-10-02T00:00:00+05:30"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "actual-event",
                        "summary": "Coffee",
                        "start": {"dateTime": "2030-10-02T17:00:00+05:30"},
                        "end": {"dateTime": "2030-10-02T17:30:00+05:30"},
                        "etag": '"v1"',
                    }
                ]
            },
        )

    b, client = backend(tmp_path, handler)
    result = await b.call(
        "list_calendar_events", {"query": "Coffee", "time_min": "2030-10-02T00:00:00+05:30"}
    )
    assert result["status"] == "success", result
    assert result["items"][0]["id"] == "actual-event"
    await client.aclose()


async def test_duplicate_names_cannot_be_modified_until_user_selects(tmp_path):
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": str(i), "title": "Coffee", "status": "needsAction", "etag": '"v1"'}
                    for i in [1, 2]
                ]
            },
        )

    b, client = backend(tmp_path, handler)
    b.observe_input("Complete the coffee task")
    result = await b.call("list_tasks", {"query": "Coffee"})
    assert result["status"] == "success", result
    chosen = result["items"][1]["resource_ref"]
    request = {
        "resource_ref": chosen,
        "operation": "update",
        "changes": {"task_status": "completed"},
    }
    denied = await b.call("prepare_resource_change", request)
    assert denied["status"] == "error" and len(calls) == 1
    b.observe_input("The second one")
    allowed = await b.call("prepare_resource_change", request)
    assert allowed["status"] == "success", allowed
    assert allowed["plan"]["actions"][0]["target"]["id"] == "2"
    bad = await b.call("prepare_resource_change", {**request, "resource_ref": "made-up"})
    assert bad["status"] == "error"
    await client.aclose()


async def test_lookup_completion_and_reopening_patch_same_task_id(tmp_path):
    calls = []
    remote = {
        "id": "task-A",
        "title": "Review",
        "notes": "Keep notes",
        "status": "needsAction",
        "due": "2030-10-02T00:00:00Z",
        "etag": '"v1"',
    }

    def handler(r):
        calls.append(r)
        if r.url.path.endswith("/tasks"):
            return httpx.Response(200, json={"items": [remote.copy()]})
        assert r.url.path.endswith("/task-A")
        if r.method == "PATCH":
            body = json.loads(r.content)
            remote.update(body, etag='"v2"')
            if body["status"] == "needsAction":
                assert body["completed"] is None
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    lookup = await b.call("list_tasks", {})
    ref = lookup["items"][0]["resource_ref"]
    for status in ["completed", "needsAction"]:
        b.observe_input("Mark that task " + status)
        proposal = await b.call(
            "prepare_resource_change",
            {"resource_ref": ref, "operation": "update", "changes": {"task_status": status}},
        )
        assert proposal["status"] == "success", proposal
        result = await b.call("execute_plan", {"plan_id": proposal["plan"]["plan_id"]})
        assert result["status"] == "success", result
        assert remote["status"] == status and remote["notes"] == "Keep notes"
    assert sum(r.method == "PATCH" for r in calls) == 2 and not any(
        r.method == "POST" for r in calls
    )
    await client.aclose()


async def test_lookup_details_cannot_bypass_name_ambiguity_and_cross_room_access(tmp_path):
    from reprise.productivity import ProductivityBackend

    rows = [
        {"id": str(i), "title": "Coffee", "status": "needsAction", "etag": '"v1"'} for i in [1, 2]
    ]

    def handler(r):
        return (
            httpx.Response(200, json={"items": rows})
            if r.url.path.endswith("/tasks")
            else httpx.Response(200, json={**rows[0], "etag": '"v2"'})
        )

    b, client = backend(tmp_path, handler)
    lookup = await b.call("list_tasks", {})
    detail = await b.call(
        "get_workspace_item", {"resource_ref": lookup["items"][0]["resource_ref"]}
    )
    ref = detail["items"][0]["resource_ref"]
    request = {"resource_ref": ref, "operation": "delete"}
    assert (await b.call("prepare_resource_change", request))["status"] == "error"
    other = ProductivityBackend("other-room", google=b.google, store=b.store)
    assert (await other.call("get_workspace_item", {"resource_ref": ref}))["status"] == "error"
    await client.aclose()


async def test_reminders_and_all_day_updates_use_calendar_fields(tmp_path):
    calls = []
    remote = {
        "id": "event-A",
        "summary": "Holiday",
        "start": {"date": "2030-10-02"},
        "end": {"date": "2030-10-03"},
        "etag": '"v1"',
        "reminders": {"useDefault": True},
    }

    def handler(r):
        calls.append(r)
        if r.url.path.endswith("/events"):
            return httpx.Response(200, json={"items": [remote.copy()]})
        if r.method == "PATCH":
            remote.update(json.loads(r.content), etag='"v2"')
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    lookup = await b.call("list_calendar_events", {})
    reminders = {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]}
    change = await b.call(
        "prepare_resource_change",
        {
            "resource_ref": lookup["items"][0]["resource_ref"],
            "operation": "update",
            "changes": {"start": "2030-10-04", "reminders": reminders},
        },
    )
    assert change["status"] == "success", change
    result = await b.call("execute_plan", {"plan_id": change["plan"]["plan_id"]})
    assert result["status"] == "success", result
    assert remote["start"] == {"date": "2030-10-04"} and remote["end"] == {"date": "2030-10-05"}
    assert remote["reminders"] == reminders
    await client.aclose()


async def test_task_list_selection_is_returned_id_bound(tmp_path):
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(200, json={"items": [{"id": "work-list", "title": "Work"}]})

    b, client = backend(tmp_path, handler)
    assert (await b.call("list_tasks", {"task_list_id": "invented"}))[
        "status"
    ] == "error" and not calls
    await b.call("list_task_lists", {})
    result = await b.call(
        "prepare_plan",
        {"title": "Work", "tasks": [{"title": "Review", "task_list_id": "work-list"}]},
    )
    assert result["status"] == "success", result
    assert (
        await b.call(
            "prepare_plan",
            {"title": "Bad", "tasks": [{"title": "Review", "task_list_id": "invented"}]},
        )
    )["status"] == "error"
    await client.aclose()


async def test_lookup_does_not_hide_pending_contextual_delete(tmp_path):
    calls = []
    remote = {"id": "t-A", "title": "Review", "status": "needsAction", "etag": '"v1"'}

    def handler(r):
        calls.append(r)
        remote["etag"] = f'"v{len(calls)}"'
        return httpx.Response(200, json={"items": [remote.copy()]})

    b, client = backend(tmp_path, handler)
    row = (await b.call("list_tasks", {}))["items"][0]
    pending = (
        await b.call(
            "prepare_resource_change", {"resource_ref": row["resource_ref"], "operation": "delete"}
        )
    )["plan"]
    await b.call("list_tasks", {})
    b.observe_assistant("Delete the task Review?")
    b.observe_input("yeah")
    assert b.store.get(b.room, pending["plan_id"])["approved"] is True
    await client.aclose()


async def test_complete_reopen_complete_is_new_intent_not_cached_old_result(tmp_path):
    calls = []
    remote = {"id": "task-A", "title": "Review", "status": "needsAction", "etag": '"v1"'}

    def handler(r):
        calls.append(r)
        if r.url.path.endswith("/tasks"):
            return httpx.Response(200, json={"items": [remote.copy()]})
        if r.method == "PATCH":
            remote.update(json.loads(r.content), etag=f'"v{len(calls)}"')
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    ref = (await b.call("list_tasks", {}))["items"][0]["resource_ref"]
    for status in ["completed", "needsAction", "completed"]:
        b.observe_input("Mark that task " + status)
        p = (
            await b.call(
                "prepare_resource_change",
                {"resource_ref": ref, "operation": "update", "changes": {"task_status": status}},
            )
        )["plan"]
        result = await b.call("execute_plan", {"plan_id": p["plan_id"]})
        assert result["status"] == "success" and remote["status"] == status
    assert sum(r.method == "PATCH" for r in calls) == 3
    await client.aclose()


async def test_existing_item_delete_confirms_and_verifies_without_creation(tmp_path):
    calls = []
    deleted = False
    remote = {"id": "task-A", "title": "Review", "status": "needsAction", "etag": '"v1"'}

    def handler(r):
        nonlocal deleted
        calls.append(r)
        if r.method == "DELETE":
            deleted = True
            return httpx.Response(204)
        if deleted:
            return httpx.Response(404, json={"error": {}})
        return (
            httpx.Response(200, json={"items": [remote]})
            if r.url.path.endswith("/tasks")
            else httpx.Response(200, json=remote)
        )

    b, client = backend(tmp_path, handler)
    row = (await b.call("list_tasks", {}))["items"][0]
    proposal = (
        await b.call(
            "prepare_resource_change", {"resource_ref": row["resource_ref"], "operation": "delete"}
        )
    )["plan"]
    assert (await b.call("execute_plan", {"plan_id": proposal["plan_id"]}))[
        "status"
    ] == "approval_required"
    assert not deleted
    b.observe_assistant("Delete the task Review?")
    b.observe_input("sure")
    done = await b.call("execute_plan", {"plan_id": proposal["plan_id"]})
    assert done["status"] == "success" and done["plan"]["actions"][0]["result"] == "deleted"
    await b.call("execute_plan", {"plan_id": proposal["plan_id"]})
    assert [r.method for r in calls] == ["GET", "GET", "DELETE", "GET"]
    await client.aclose()


async def test_title_due_date_update_correction_before_dispatch_blocks_patch(tmp_path):
    import asyncio

    entered, release = asyncio.Event(), asyncio.Event()
    reads = 0
    calls = []
    remote = {"id": "task-A", "title": "Review", "status": "needsAction", "etag": '"v1"'}

    async def handler(r):
        nonlocal reads
        calls.append(r)
        if r.url.path.endswith("/tasks"):
            return httpx.Response(200, json={"items": [remote]})
        if r.method == "GET":
            reads += 1
            entered.set()
            await release.wait()
        if r.method == "PATCH":
            remote.update(json.loads(r.content))
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    row = (await b.call("list_tasks", {}))["items"][0]
    p = (
        await b.call(
            "prepare_resource_change",
            {
                "resource_ref": row["resource_ref"],
                "operation": "update",
                "changes": {"title": "Review slides", "due_date": "2030-10-04"},
            },
        )
    )["plan"]
    job = asyncio.create_task(b.call("execute_plan", {"plan_id": p["plan_id"]}))
    await asyncio.wait_for(entered.wait(), 1)
    b.observe_input("Actually cancel that edit")
    release.set()
    result = await job
    assert result["plan"]["actions"][0]["status"] == "not_executed" and not any(
        r.method == "PATCH" for r in calls
    )
    assert remote["title"] == "Review"
    await client.aclose()


async def test_search_errors_and_empty_pages_are_distinguished(tmp_path):
    b, client = backend(
        tmp_path, lambda r: httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}})
    )
    failed = await b.call("list_tasks", {})
    assert failed["status"] == "error" and "403" in failed["message"]
    await client.aclose()
    b, client = backend(
        tmp_path, lambda r: httpx.Response(200, json={"items": [], "nextPageToken": "next"})
    )
    empty = await b.call("list_tasks", {"query": "Unmatched"})
    assert empty["items"] == [] and empty["more_available"] and empty["next_page_token"] == "next"
    await client.aclose()


async def test_invalid_reminder_or_date_never_dispatches(tmp_path):
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(200, json={"id": "bad"})

    b, client = backend(tmp_path, handler)
    for reminders in [
        {"useDefault": True, "overrides": [{"method": "popup", "minutes": 1}]},
        {"useDefault": False, "overrides": [{"method": "sms", "minutes": 1}]},
        {"useDefault": False, "overrides": [{"method": "popup", "minutes": -1}]},
    ]:
        result = await b.call(
            "prepare_plan",
            {
                "title": "Invalid",
                "calendar_events": [
                    {
                        "title": "Invalid",
                        "start": "2030-10-04",
                        "end": "2030-10-05",
                        "reminders": reminders,
                    }
                ],
            },
        )
        assert result["status"] == "error"
    assert (await b.call("list_tasks", {"due_from": "not-a-date"}))["status"] == "error"
    assert not calls
    await client.aclose()


async def test_read_results_publish_actual_trace_events(tmp_path):
    from reprise.trace import Trace

    b, client = backend(
        tmp_path,
        lambda r: httpx.Response(
            200, json={"items": [{"id": "a", "title": "Review", "status": "needsAction"}]}
        ),
    )
    b.trace = Trace("room-a", tmp_path / "trace")
    result = await b.call("list_tasks", {})
    assert result["status"] == "success"
    event = json.loads(b.trace.path.read_text().splitlines()[-1])
    assert event["event"] == "workspace_items" and event["resource_kind"] == "task"
    assert event["items"][0]["id"] == "a"
    await client.aclose()


def test_productivity_tools_convert_to_gemini_live_schema():
    from google.genai import types
    from livekit.agents import llm
    from livekit.plugins.google.utils import create_tools_config

    from reprise.agent import make_tool
    from reprise.productivity import PRODUCTIVITY_TOOLS

    tools, mixed = create_tools_config(
        llm.ToolContext([make_tool(spec, None) for spec in PRODUCTIVITY_TOOLS]),
        tool_behavior=types.Behavior.NON_BLOCKING,
        use_parameters_json_schema=False,
    )
    assert not mixed
    assert {d.name for tool in tools for d in tool.function_declarations} == {
        spec.name for spec in PRODUCTIVITY_TOOLS
    }


async def test_paged_matches_require_selection_across_all_pages(tmp_path):
    def handler(r):
        second = bool(r.url.params.get("pageToken"))
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": "b" if second else "a", "title": "Coffee", "status": "needsAction"}
                ],
                **({} if second else {"nextPageToken": "next"}),
            },
        )

    b, client = backend(tmp_path, handler)
    b.observe_input("Complete the Coffee task")
    await b.call("list_tasks", {"query": "Coffee"})
    page = await b.call("list_tasks", {"query": "Coffee", "page_token": "next"})
    request = {
        "resource_ref": page["items"][0]["resource_ref"],
        "operation": "update",
        "changes": {"task_status": "completed"},
    }
    assert (await b.call("prepare_resource_change", request))["status"] == "error"
    assert page["items"][0]["number"] == 2
    b.observe_input("The second one")
    assert (await b.call("prepare_resource_change", request))["status"] == "success"
    await client.aclose()


async def test_matches_across_task_lists_do_not_silently_select_last_list(tmp_path):
    def handler(r):
        if r.url.path.endswith("/@me/lists"):
            return httpx.Response(
                200,
                json={"items": [{"id": "work", "title": "Work"}, {"id": "home", "title": "Home"}]},
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": r.url.path.split("/")[-2], "title": "Coffee", "status": "needsAction"}
                ]
            },
        )

    b, client = backend(tmp_path, handler)
    b.observe_input("Complete Coffee")
    await b.call("list_task_lists", {})
    await b.call("list_tasks", {"task_list_id": "work", "query": "Coffee"})
    result = await b.call("list_tasks", {"task_list_id": "home", "query": "Coffee"})
    request = {
        "resource_ref": result["items"][0]["resource_ref"],
        "operation": "update",
        "changes": {"task_status": "completed"},
    }
    assert (await b.call("prepare_resource_change", request))["status"] == "error"
    await client.aclose()


@pytest.mark.parametrize("add_guest", [False, True])
async def test_calendar_title_edit_does_not_replace_guest_metadata(tmp_path, add_guest):
    guest = {"email": "guest@example.com", "responseStatus": "accepted", "optional": True}
    remote = {
        "id": "event",
        "summary": "Coffee",
        "start": {"date": "2030-10-02"},
        "end": {"date": "2030-10-03"},
        "attendees": [guest.copy()],
        "etag": '"v1"',
    }

    def handler(r):
        if r.url.path.endswith("/events"):
            return httpx.Response(200, json={"items": [remote.copy()]})
        if r.method == "PATCH":
            body = json.loads(r.content)
            if add_guest:
                assert body["attendees"] == [guest, {"email": "new@example.com"}]
            else:
                assert "attendees" not in body
            remote.update(body, etag='"v2"')
        return httpx.Response(200, json=remote.copy())

    b, client = backend(tmp_path, handler)
    row = (await b.call("list_calendar_events", {}))["items"][0]
    changes = {
        "title": "Tea",
        **({"attendees": ["guest@example.com", "new@example.com"]} if add_guest else {}),
    }
    p = (
        await b.call(
            "prepare_resource_change",
            {"resource_ref": row["resource_ref"], "operation": "update", "changes": changes},
        )
    )["plan"]
    b.observe_assistant("Update Tea with guest@example.com and new@example.com?")
    b.observe_input("yes")
    assert (await b.call("execute_plan", {"plan_id": p["plan_id"]}))["status"] == "success"
    assert remote["attendees"][0] == guest
    await client.aclose()


@pytest.mark.parametrize("legacy_owner", [False, True])
async def test_default_task_list_alias_cannot_bypass_uncertain_write_lock(tmp_path, legacy_owner):
    from dataclasses import replace

    remote = {"id": "task", "title": "Review", "status": "needsAction", "etag": '"v1"'}
    writes = []

    def handler(r):
        if r.url.path.endswith("/lists/@default"):
            return httpx.Response(200, json={"id": "actual-list", "title": "My list"})
        if r.url.path.endswith("/@me/lists"):
            return httpx.Response(200, json={"items": [{"id": "actual-list", "title": "My list"}]})
        if r.method == "PATCH":
            writes.append(r)
            raise httpx.ReadError("lost receipt", request=r)
        return httpx.Response(
            200, json={"items": [remote.copy()]} if r.url.path.endswith("/tasks") else remote.copy()
        )

    b, client = backend(tmp_path, handler)
    b.google.config = replace(b.google.config, task_list_id="@default")
    b.management.known_lists.add("@default")
    await b.call("list_task_lists", {})
    for container, title in [("@default", "First edit"), ("actual-list", "Second edit")]:
        b.observe_input("Change Review to " + title)
        row = (await b.call("list_tasks", {"task_list_id": container}))["items"][0]
        assert row["container_id"] == "actual-list"
        p = (
            await b.call(
                "prepare_resource_change",
                {
                    "resource_ref": row["resource_ref"],
                    "operation": "update",
                    "changes": {"title": title},
                },
            )
        )["plan"]
        await b.call("execute_plan", {"plan_id": p["plan_id"]})
        if container == "@default" and legacy_owner:
            with b.store.connect() as db:
                db.execute(
                    "UPDATE resource_owners SET resource=?",
                    (json.dumps(["task", "@default", "task"]),),
                )
            b.google._task_list_ids.clear()
    assert len(writes) == 1
    await client.aclose()
