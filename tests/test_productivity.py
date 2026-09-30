"""HTTP contract and execution-safety tests; runtime adapters always call live APIs."""

import asyncio
import base64
import json
import time
from email import message_from_bytes

import httpx
import pytest

from reprise.google_workspace import GoogleWorkspace, WorkspaceConfig
from reprise.productivity import PlanStore, ProductivityBackend


def backend(tmp_path, handler):
    config = WorkspaceConfig(
        client_id="client",
        client_secret="secret",
        token_file=tmp_path / "tokens.json",
        shopping_list_id="shopping",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    google = GoogleWorkspace(config, client)
    google.save_tokens(
        {"access_token": "test-token", "refresh_token": "refresh", "expires_at": time.time() + 3600}
    )
    return ProductivityBackend(
        "room-a", google=google, store=PlanStore(tmp_path / "plans.sqlite3")
    ), client


def request_plan():
    return {
        "title": "Tomorrow's plan",
        "calendar_events": [
            {
                "title": "Prepare presentation",
                "start": "2030-10-01T17:00:00+05:30",
                "end": "2030-10-01T17:30:00+05:30",
            }
        ],
        "tasks": [{"title": "Review slides", "due_date": "2030-10-01", "notes": "Finish by noon"}],
        "messages": [
            {
                "to": ["alex@example.com"],
                "subject": "Meeting time",
                "body": "Let's meet at five.",
                "delivery": "send",
            }
        ],
        "shopping": [{"item": "milk", "quantity": "2 cartons"}],
    }


async def test_all_four_services_use_google_payloads_and_return_real_response_ids(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer test-token"
        if "calendar/v3" in str(request.url):
            assert body["start"]["dateTime"] == "2030-10-01T17:00:00+05:30"
            assert body["start"]["timeZone"] == "Asia/Kolkata"
            assert dict(request.url.params)["sendUpdates"] == "none"
            return httpx.Response(
                200, json={"id": "calendar-id", "htmlLink": "https://calendar.google.com/event"}
            )
        if "messages/send" in str(request.url):
            email = message_from_bytes(base64.urlsafe_b64decode(body["raw"]))
            assert email["To"] == "alex@example.com" and email["Subject"] == "Meeting time"
            return httpx.Response(200, json={"id": "message-id"})
        if "/shopping/" in str(request.url):
            assert body["title"] == "2 cartons milk"
            return httpx.Response(200, json={"id": "shopping-id"})
        assert body["due"] == "2030-10-01T00:00:00.000Z" and body["notes"] == "Finish by noon"
        return httpx.Response(200, json={"id": "task-id"})

    b, client = backend(tmp_path, handler)
    b.observe_input("Prepare this plan")
    prepared = await b.call("prepare_plan", request_plan())
    plan_id = prepared["plan"]["plan_id"]
    assert (await b.call("execute_plan", {"plan_id": plan_id}))["status"] == "approval_required"
    assert not calls
    b.observe_input("Confirm the plan.")
    results = await asyncio.gather(
        *(b.call("execute_plan", {"plan_id": plan_id}) for _ in range(2))
    )
    final = b.store.get("room-a", plan_id)
    assert final["status"] == "completed" and len(calls) == 4
    assert [a["google_id"] for a in final["actions"]] == [
        "calendar-id",
        "task-id",
        "message-id",
        "shopping-id",
    ]
    assert final["actions"][2]["delivery_result"] == "sent"
    assert all(r["status"] == "success" for r in results)
    await client.aclose()


async def test_timeout_persists_unknown_and_never_resends_email(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("response lost", request=request)

    b, client = backend(tmp_path, handler)
    args = {"title": "Email", "messages": request_plan()["messages"]}
    plan = (await b.call("prepare_plan", args))["plan"]
    b.observe_input("yes, do it")
    result = await b.call("execute_plan", {"plan_id": plan["plan_id"]})
    assert result["plan"]["status"] == "partial"
    assert result["plan"]["actions"][0]["status"] == "unknown"
    await b.call("execute_plan", {"plan_id": plan["plan_id"]})
    assert len(calls) == 1
    await client.aclose()


async def test_correction_invalidates_old_plan_and_requires_new_confirmation(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "id"})
    )
    first = (await b.call("prepare_plan", {"title": "Tasks", "tasks": [{"title": "Buy coffee"}]}))[
        "plan"
    ]
    b.observe_input("Actually buy tea instead")
    second = (await b.call("prepare_plan", {"title": "Tasks", "tasks": [{"title": "Buy tea"}]}))[
        "plan"
    ]
    assert (await b.call("execute_plan", {"plan_id": first["plan_id"]}))["plan"][
        "status"
    ] == "superseded"
    assert (await b.call("execute_plan", {"plan_id": second["plan_id"]}))[
        "status"
    ] == "approval_required"
    b.observe_input("yes but change the time")
    assert (await b.call("execute_plan", {"plan_id": second["plan_id"]}))[
        "status"
    ] == "approval_required"
    assert not calls
    with pytest.raises(ValueError):
        b.store.approve("different-room", second["plan_id"])
    await client.aclose()


async def test_oauth_refresh_and_draft_mime_are_correct(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if "oauth2.googleapis.com" in str(request.url):
            assert b"grant_type=refresh_token" in request.content
            assert b"refresh_token=refresh" in request.content
            return httpx.Response(200, json={"access_token": "new-token", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer new-token"
        body = json.loads(request.content)
        assert "message" in body and "raw" in body["message"]
        return httpx.Response(200, json={"id": "draft-id", "message": {"id": "message-id"}})

    b, client = backend(tmp_path, handler)
    b.google.save_tokens({"refresh_token": "refresh"})
    message = {**request_plan()["messages"][0], "delivery": "draft"}
    result = await b.google.create_email(message)
    assert result["id"] == "draft-id" and len(calls) == 2
    assert b.google.config.token_file.stat().st_mode & 0o777 == 0o600
    await client.aclose()


async def test_missing_google_credentials_never_create_success(tmp_path):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("No request expected"))
    )
    google = GoogleWorkspace(WorkspaceConfig(token_file=tmp_path / "missing.json"), client)
    b = ProductivityBackend("room", google=google, store=PlanStore(tmp_path / "plan.sqlite3"))
    plan = (await b.call("prepare_plan", {"title": "Task", "tasks": [{"title": "Real task"}]}))[
        "plan"
    ]
    b.observe_input("confirm the plan")
    result = await b.call("execute_plan", {"plan_id": plan["plan_id"]})
    assert (
        result["status"] == "error" and b.store.get("room", plan["plan_id"])["status"] == "prepared"
    )
    await client.aclose()


async def test_local_connection_routes_reject_bad_state_and_cross_room_access(monkeypatch):
    from reprise.demo_server import app, issued, oauth_states

    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8844"
    ) as client:
        missing = await client.get("/google/connect")
        assert missing.status_code == 503
        response = await client.get("/google/callback?state=untrusted&code=code")
        assert response.status_code == 400
        assert (await client.get("/plans/reprise-demo-00000000000000")).status_code == 404
        assert (
            await client.post(
                "/session?mode=productivity", headers={"Origin": "https://untrusted.example"}
            )
        ).status_code == 403
        oauth_states["expected"] = ("verifier", time.time() - 1)
        client.cookies.set("reprise_oauth_state", "expected")
        expired = await client.get("/google/callback?state=expected&code=code")
        assert expired.status_code == 400 and "expected" not in oauth_states
    assert "reprise-demo-00000000000000" not in issued
