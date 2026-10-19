import asyncio
import json
import time

import httpx
from test_productivity import backend, request_plan

from reprise.productivity import PlanStore, ProductivityBackend


async def test_expired_worker_lease_preserves_receipts_and_does_not_resend(tmp_path):
    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "wrong"})
    )
    plan = b.store.prepare(
        "room-a",
        "Mail and task",
        [
            {"kind": "task", "title": "Already done"},
            {"kind": "email", **request_plan()["messages"][0]},
            {"kind": "task", "title": "Not dispatched"},
        ],
    )
    b.store.approve("room-a", plan["plan_id"])
    running = b.store.claim("room-a", plan["plan_id"])
    running["actions"][0].update(status="completed", google_id="existing-task")
    running["actions"][1].update(status="running", operation_id="mail-operation")
    running["lease_expires_at"] = time.time() - 1
    b.store.save(running)
    with b.store.connect() as db:
        running["lease_expires_at"] = 0
        db.execute("UPDATE plans SET document=? WHERE id=?", (json.dumps(running), plan["plan_id"]))
    restarted = ProductivityBackend("room-a", google=b.google, store=PlanStore(b.store.path))
    result = await restarted.call("execute_plan", {"plan_id": plan["plan_id"]})
    assert result["status"] == "needs_review"
    final = result["plan"]
    assert final["status"] == "needs_review"
    assert final["actions"][0]["google_id"] == "existing-task"
    assert [a["status"] for a in final["actions"]] == ["completed", "unknown", "not_executed"]
    await restarted.call("execute_plan", {"plan_id": plan["plan_id"]})
    assert not calls
    await client.aclose()


async def test_active_lease_is_not_recovered_by_another_backend(tmp_path):
    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "unused"}))
    plan = b.store.prepare("room-a", "Task", [{"kind": "task", "title": "One"}])
    b.store.approve("room-a", plan["plan_id"])
    b.store.claim("room-a", plan["plan_id"])
    other = ProductivityBackend("room-a", google=b.google, store=PlanStore(b.store.path))
    result = await other.call("execute_plan", {"plan_id": plan["plan_id"]})
    assert result["status"] == "running"
    assert result["plan"]["status"] == "running"
    await client.aclose()


async def test_invalid_success_receipt_is_uncertain_not_completed(tmp_path):
    calls = []
    b, client = backend(tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": ""}))
    plan = (
        await b.call(
            "prepare_plan", {"title": "Task", "tasks": [{"title": "One"}, {"title": "Two"}]}
        )
    )["plan"]
    b.observe_input("confirm the plan")
    result = await b.call("execute_plan", {"plan_id": plan["plan_id"]})
    assert result["status"] == "partial"
    assert result["plan"]["actions"][0]["status"] == "unknown"
    assert result["plan"]["actions"][1]["status"] == "not_executed"
    assert len(calls) == 1
    await client.aclose()


async def test_malformed_google_success_body_is_uncertain_and_durable(tmp_path):
    b, client = backend(tmp_path, lambda r: httpx.Response(200, text="not-json"))
    plan = (await b.call("prepare_plan", {"title": "Task", "tasks": [{"title": "One"}]}))["plan"]
    b.observe_input("confirm the plan")
    result = await b.call("execute_plan", {"plan_id": plan["plan_id"]})
    assert result["status"] == "partial"
    assert b.store.get("room-a", plan["plan_id"])["actions"][0]["status"] == "unknown"
    await client.aclose()


async def test_correction_stops_remaining_actions_but_preserves_inflight_receipt(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"id": "committed-task"})

    b, client = backend(tmp_path, handler)
    plan = (
        await b.call(
            "prepare_plan", {"title": "Tasks", "tasks": [{"title": "One"}, {"title": "Two"}]}
        )
    )["plan"]
    b.observe_input("confirm the plan")
    task = asyncio.create_task(b.call("execute_plan", {"plan_id": plan["plan_id"]}))
    await asyncio.wait_for(entered.wait(), 2)
    b.observe_input("Do not add the second task")
    release.set()
    result = await task
    assert result["status"] == "partial"
    assert [a["status"] for a in result["plan"]["actions"]] == ["completed", "not_executed"]
    assert result["plan"]["actions"][0]["google_id"] == "committed-task"
    assert len(calls) == 1
    await client.aclose()


async def test_renewed_lease_survives_stale_in_memory_plan_save(tmp_path):
    store = PlanStore(tmp_path / "plans.db")
    plan = store.prepare("room-a", "Task", [{"kind": "task", "title": "One"}])
    store.approve("room-a", plan["plan_id"])
    stale = store.claim("room-a", plan["plan_id"])
    stale["lease_expires_at"] -= 20
    assert store.renew("room-a", plan["plan_id"], stale["lease_owner"])
    renewed = store.get("room-a", plan["plan_id"])["lease_expires_at"]
    store.save(stale)
    assert store.get("room-a", plan["plan_id"])["lease_expires_at"] >= renewed


async def test_recovered_plan_cannot_be_overwritten_by_dead_worker(tmp_path):
    store = PlanStore(tmp_path / "plans.db")
    plan = store.prepare("room-a", "Task", [{"kind": "task", "title": "One"}])
    store.approve("room-a", plan["plan_id"])
    stale = store.claim("room-a", plan["plan_id"])
    with store.connect() as db:
        expired = {**stale, "lease_expires_at": 0}
        db.execute("UPDATE plans SET document=? WHERE id=?", (json.dumps(expired), plan["plan_id"]))
    store.recover_expired("room-a")
    stale["status"] = "completed"
    assert not store.save(stale)
    assert store.get("room-a", plan["plan_id"])["status"] == "needs_review"


async def test_shopping_list_receipt_survives_unknown_task_write(tmp_path):
    def handler(request):
        if str(request.url).endswith("/users/@me/lists"):
            return httpx.Response(200, json={"id": "new-list"})
        raise httpx.ReadTimeout("response lost", request=request)

    b, client = backend(tmp_path, handler)
    from dataclasses import replace

    b.google.config = replace(b.google.config, shopping_list_id="")
    plan = (await b.call("prepare_plan", {"title": "Shop", "shopping": [{"item": "Rice"}]}))["plan"]
    b.observe_input("confirm the plan")
    result = await b.call("execute_plan", {"plan_id": plan["plan_id"]})
    action = result["plan"]["actions"][0]
    assert action["status"] == "unknown"
    assert action["list_receipt"]["id"] == "new-list"
    assert action["target_list_id"] == "new-list"
    await client.aclose()


async def test_status_read_recovers_expired_running_plan_without_reexecution(tmp_path):
    store = PlanStore(tmp_path / "plans.db")
    plan = store.prepare("room-a", "Task", [{"kind": "task", "title": "One"}])
    store.approve("room-a", plan["plan_id"])
    running = store.claim("room-a", plan["plan_id"])
    running["actions"][0]["status"] = "running"
    running["lease_expires_at"] = 0
    with store.connect() as db:
        db.execute("UPDATE plans SET document=? WHERE id=?", (json.dumps(running), plan["plan_id"]))
    assert PlanStore(store.path).get("room-a", plan["plan_id"])["status"] == "needs_review"


async def test_late_confirmed_receipt_survives_expiry_without_reviving_plan(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"id": "late-but-confirmed"})

    b, client = backend(tmp_path, handler)
    plan = (
        await b.call(
            "prepare_plan", {"title": "Task", "tasks": [{"title": "One"}, {"title": "Two"}]}
        )
    )["plan"]
    b.observe_input("confirm the plan")
    task = asyncio.create_task(b.call("execute_plan", {"plan_id": plan["plan_id"]}))
    await asyncio.wait_for(entered.wait(), 2)
    with b.store.connect() as db:
        row = db.execute("SELECT document FROM plans WHERE id=?", (plan["plan_id"],)).fetchone()
        expired = json.loads(row[0])
        expired["lease_expires_at"] = 0
        db.execute("UPDATE plans SET document=? WHERE id=?", (json.dumps(expired), plan["plan_id"]))
    b.store.recover_expired("room-a")
    release.set()
    await task
    final = b.store.get("room-a", plan["plan_id"])
    assert final["status"] == "needs_review"
    assert final["actions"][0]["google_id"] == "late-but-confirmed"
    assert final["actions"][0]["status"] == "completed"
    assert final["actions"][1]["status"] == "not_executed"
    assert len(calls) == 1
    await client.aclose()


async def test_correction_during_shopping_list_creation_blocks_following_task_write(tmp_path):
    from dataclasses import replace

    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        if str(request.url).endswith("/users/@me/lists"):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"id": "created-list"})
        return httpx.Response(200, json={"id": "unauthorized-task"})

    b, client = backend(tmp_path, handler)
    b.google.config = replace(b.google.config, shopping_list_id="")
    plan = (await b.call("prepare_plan", {"title": "Shop", "shopping": [{"item": "Tea"}]}))["plan"]
    b.observe_input("confirm the plan")
    task = asyncio.create_task(b.call("execute_plan", {"plan_id": plan["plan_id"]}))
    await asyncio.wait_for(entered.wait(), 2)
    b.observe_input("Actually do not add tea")
    release.set()
    result = await task
    assert len(calls) == 1
    assert result["status"] == "partial"
    assert result["plan"]["actions"][0]["status"] == "not_executed"
    assert result["plan"]["actions"][0]["list_receipt"]["id"] == "created-list"
    await client.aclose()


async def test_correction_while_refreshing_token_blocks_mutation_after_auth(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        if "oauth2.googleapis.com" in str(request.url):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"access_token": "refreshed", "expires_in": 3600})
        return httpx.Response(200, json={"id": "unauthorized-task"})

    b, client = backend(tmp_path, handler)
    b.google.save_tokens({"refresh_token": "refresh"})
    plan = (await b.call("prepare_plan", {"title": "Task", "tasks": [{"title": "Tea"}]}))["plan"]
    b.observe_input("confirm the plan")
    task = asyncio.create_task(b.call("execute_plan", {"plan_id": plan["plan_id"]}))
    await asyncio.wait_for(entered.wait(), 2)
    b.observe_input("Cancel that task")
    release.set()
    result = await task
    assert len(calls) == 1
    assert result["plan"]["actions"][0]["status"] == "not_executed"
    await client.aclose()


async def test_expired_shopping_executor_preserves_late_list_receipt_without_task_write(tmp_path):
    from dataclasses import replace

    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"id": "late-list"})

    b, client = backend(tmp_path, handler)
    b.google.config = replace(b.google.config, shopping_list_id="")
    plan = (await b.call("prepare_plan", {"title": "Shop", "shopping": [{"item": "Tea"}]}))["plan"]
    b.observe_input("confirm the plan")
    task = asyncio.create_task(b.call("execute_plan", {"plan_id": plan["plan_id"]}))
    await asyncio.wait_for(entered.wait(), 2)
    with b.store.connect() as db:
        row = db.execute("SELECT document FROM plans WHERE id=?", (plan["plan_id"],)).fetchone()
        expired = json.loads(row[0])
        expired["lease_expires_at"] = 0
        db.execute("UPDATE plans SET document=? WHERE id=?", (json.dumps(expired), plan["plan_id"]))
    b.store.recover_expired("room-a")
    release.set()
    await task
    recovered = b.store.get("room-a", plan["plan_id"])
    assert recovered["status"] == "needs_review"
    assert recovered["actions"][0]["list_receipt"]["id"] == "late-list"
    assert len(calls) == 1
    await client.aclose()
