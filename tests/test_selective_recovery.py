"""Selective recovery with real Coordinator and deterministic event barriers; no network."""

import asyncio
from dataclasses import replace

import pytest
from test_coordinator import Backend, make

from reprise.catalog import S, ToolSpec
from reprise.coordinator import InvalidCall, Superseded

FLIGHT = ToolSpec(
    "flight_lookup",
    "Read an itinerary",
    {"query": S},
    ("query",),
    intent_scope=("flight", "itinerary"),
    reuse_for_s=30,
)
EVENT = ToolSpec(
    "calendar_create",
    "Create event",
    {"title": S, "time": S},
    ("title", "time"),
    writes=True,
    intent_scope=("calendar", "event", "meeting"),
)
TASK = ToolSpec(
    "task_create", "Create task", {"title": S}, ("title",), writes=True, intent_scope=("task",)
)


class Reads(Backend):
    def __init__(self, result_id="returned-flight"):
        super().__init__()
        self.result_id = result_id
        self.release = asyncio.Event()

    async def call(self, name, args):
        self.calls.append((name, dict(args)))
        if name == "flight_lookup":
            self.started.set()
            await self.release.wait()
        return {"status": "success", "items": [{"flight_id": self.result_id}]}


async def test_inflight_read_survives_time_correction_without_duplicate_lookup(tmp_path):
    b = Reads()
    c = make(tmp_path, b)
    c.observe_transcript("Find my flight and schedule coffee at five")
    first = asyncio.create_task(c.execute(FLIGHT, {"query": "my flight"}, response_id="old"))
    await b.started.wait()
    old = c.execute(EVENT, {"title": "coffee", "time": "five"}, response_id="old")
    c.observe_transcript("Actually schedule coffee at six")
    reused = asyncio.create_task(c.execute(FLIGHT, {"query": "my flight"}, response_id="new"))
    fixed = c.execute(EVENT, {"title": "coffee", "time": "six"}, response_id="new")
    b.release.set()
    result = await asyncio.gather(first, old, reused, fixed, return_exceptions=True)
    assert isinstance(result[1], Superseded)
    assert result[0]["status"] == result[2]["status"] == "success"
    assert b.calls == [
        ("flight_lookup", {"query": "my flight"}),
        ("calendar_create", {"title": "coffee", "time": "six"}),
    ]


async def test_elliptical_correction_uses_unique_last_clause_not_all_actions(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find my flight and schedule coffee at five")
    lookup = await c.execute(FLIGHT, {"query": "my flight"})
    old = c.execute(EVENT, {"title": "coffee", "time": "five"})
    c.observe_transcript("Actually six")
    with pytest.raises(Superseded):
        await old
    assert await c.execute(FLIGHT, {"query": "my flight"}) == lookup
    assert sum(name == "flight_lookup" for name, _ in c.backend.calls) == 1


async def test_final_correction_after_finished_response_reuses_valid_session_read(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find my flight and schedule coffee at five")
    result = await c.execute(FLIGHT, {"query": "my flight"})
    await c.execute(EVENT, {"title": "coffee", "time": "five"})
    c.finish_response()
    c.observe_transcript("Actually make the coffee event six")
    assert await c.execute(FLIGHT, {"query": "my flight"}) == result
    assert len(c.backend.calls) == 2


async def test_expired_and_explicit_refresh_results_are_not_reused(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find my flight")
    await c.execute(FLIGHT, {"query": "my flight"})
    c.finish_response()
    next(iter(c.operations.values())).completed_at -= 31
    c.observe_transcript("Show that same flight")
    await c.execute(FLIGHT, {"query": "my flight"})
    c.observe_transcript("Check that flight again with latest information")
    await c.execute(FLIGHT, {"query": "my flight"})
    assert len(c.backend.calls) == 3


async def test_successive_corrections_preserve_unrelated_task(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Schedule coffee at five and add a task to collect fabric")
    task = c.execute(TASK, {"title": "collect fabric"})
    five = c.execute(EVENT, {"title": "coffee", "time": "five"})
    c.observe_transcript("Actually move coffee to six")
    six = c.execute(EVENT, {"title": "coffee", "time": "six"})
    c.observe_transcript("Actually move coffee to seven")
    seven = c.execute(EVENT, {"title": "coffee", "time": "seven"})
    results = await asyncio.gather(task, five, six, seven, return_exceptions=True)
    assert results[0]["status"] == "success"
    assert all(isinstance(r, Superseded) for r in results[1:3])
    assert c.backend.calls == [
        ("task_create", {"title": "collect fabric"}),
        ("calendar_create", {"title": "coffee", "time": "seven"}),
    ]


async def test_undecided_alternatives_cannot_start_reads_or_writes(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find flight to Oslo or Madrid, I have not decided")
    for query in ["Oslo", "Madrid"]:
        with pytest.raises(InvalidCall):
            await c.execute(FLIGHT, {"query": query})
    assert not c.backend.calls


async def test_corrected_source_invalidates_dependent_mutation_not_unrelated_read(tmp_path):
    source = replace(FLIGHT, references={})
    write = ToolSpec(
        "reserve_flight",
        "Reserve",
        {"flight_id": S},
        ("flight_id",),
        True,
        references={"flight_id": ("flight_lookup", "items", "flight_id")},
    )
    c = make(tmp_path)
    c.observe_transcript("Find flight to Lyon and reserve it")
    await c.execute(source, {"query": "Lyon"})
    old = c.execute(write, {"flight_id": "returned-flight"})
    c.observe_transcript("Actually find the flight to Riga")
    with pytest.raises((Superseded, InvalidCall)):
        await old
    with pytest.raises(InvalidCall):
        await c.execute(write, {"flight_id": "returned-flight"})
    assert all(name != "reserve_flight" for name, _ in c.backend.calls)


async def test_goal_reset_discards_pending_work_and_prior_result_reuse(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find my flight and schedule coffee at five")
    await c.execute(FLIGHT, {"query": "my flight"})
    old = c.execute(EVENT, {"title": "coffee", "time": "five"})
    c.observe_transcript("Never mind all that, add a task to buy ribbon instead")
    with pytest.raises(Superseded):
        await old
    await c.execute(TASK, {"title": "buy ribbon"})
    c.observe_transcript("Actually show my flight again")
    await c.execute(FLIGHT, {"query": "my flight"})
    assert sum(name == "flight_lookup" for name, _ in c.backend.calls) == 2


async def test_partial_speech_and_acoustic_pause_never_commit_proposal(tmp_path):
    c = make(tmp_path, gate_timeout_s=0.03)
    c.observe_transcript("Schedule coffee at", is_final=False)
    c.speech_started()
    c.speech_ended()
    with pytest.raises(Superseded):
        await c.execute(EVENT, {"title": "coffee", "time": "five"})
    assert not c.backend.calls


async def test_workspace_calendar_correction_preserves_task_during_token_refresh(tmp_path):
    import httpx
    from test_productivity import backend

    entered, release = asyncio.Event(), asyncio.Event()
    writes = []

    async def handler(request):
        if "oauth2" in str(request.url):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})
        writes.append(request)
        return httpx.Response(200, json={"id": "actual-task"})

    b, client = backend(tmp_path, handler)
    b.google.save_tokens({"refresh_token": "refresh"})
    b.observe_input("Schedule coffee and add a task to collect fabric")
    p = (
        await b.call(
            "prepare_plan",
            {
                "title": "Plan",
                "calendar_events": [
                    {
                        "title": "coffee",
                        "start": "2030-11-06T17:00:00+05:30",
                        "end": "2030-11-06T17:30:00+05:30",
                    }
                ],
                "tasks": [{"title": "collect fabric"}],
            },
        )
    )["plan"]
    job = asyncio.create_task(b.call("execute_plan", {"plan_id": p["plan_id"]}))
    await entered.wait()
    b.observe_input("Actually move coffee to six", revision=2)
    release.set()
    result = await job
    assert len(writes) == 1 and "/tasks/" in str(writes[0].url)
    saved = b.store.get(b.room, p["plan_id"])
    assert saved["actions"][0]["status"] == "not_executed"
    assert saved["actions"][1]["google_id"] == "actual-task"
    assert result["status"] != "success"  # The original mixed plan did not fully complete.
    await client.aclose()


async def test_completed_workspace_read_is_reused_during_unrelated_correction(tmp_path):
    import httpx
    from test_productivity import backend
    from reprise.productivity import PRODUCTIVITY_TOOLS
    from reprise.config import Settings
    from reprise.coordinator import Coordinator
    from reprise.trace import Trace

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"messages": []})

    b, client = backend(tmp_path, handler)
    b.google.save_tokens(
        {
            "access_token": "token",
            "refresh_token": "refresh",
            "expires_at": 9999999999,
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
        }
    )
    c = Coordinator(
        b, Trace("room-a", tmp_path / "trace"), Settings(settle_ms=0, write_settle_ms=0)
    )
    spec = next(s for s in PRODUCTIVITY_TOOLS if s.name == "search_gmail")
    c.observe_transcript("Find my flight and schedule coffee at five")
    await c.execute(spec, {"query": "flight"})
    pending = c.execute(EVENT, {"title": "coffee", "time": "five"})
    c.observe_transcript("Actually coffee at six")
    with pytest.raises(Superseded):
        await pending
    await c.execute(spec, {"query": "flight"})
    assert len(calls) == 1
    await client.aclose()


@pytest.mark.parametrize("policy", ["guarded", "baseline"])
async def test_expired_dependency_policy_does_not_change_eager_baseline(tmp_path, policy):
    c = make(tmp_path, policy=policy)
    write = ToolSpec(
        "reserve",
        "Reserve",
        {"flight_id": S},
        ("flight_id",),
        True,
        references={"flight_id": ("flight_lookup", "items", "flight_id")},
    )
    b = Reads()
    b.release.set()
    c.backend = b
    c.observe_transcript("Find my flight and reserve it")
    await c.execute(FLIGHT, {"query": "my flight"})
    next(iter(c.operations.values())).completed_at -= 31
    if policy == "guarded":
        with pytest.raises(InvalidCall):
            await c.execute(write, {"flight_id": "returned-flight"})
        assert len(b.calls) == 1
    else:
        await c.execute(write, {"flight_id": "returned-flight"})
        assert len(b.calls) == 2


async def test_dependency_edge_invalidates_pending_child_even_without_shared_scope(tmp_path):
    b = Reads()
    b.release.set()
    c = make(tmp_path, b)
    write = ToolSpec(
        "reserve",
        "Reserve",
        {"flight_id": S},
        ("flight_id",),
        True,
        references={"flight_id": ("flight_lookup", "items", "flight_id")},
    )
    c.observe_transcript("Find a flight to Lyon and reserve it")
    await c.execute(FLIGHT, {"query": "Lyon"})
    child = c.execute(write, {"flight_id": "returned-flight"})
    pending = c._proposals[-1]
    assert pending.dependencies and pending.dependencies[0].name == "flight_lookup"
    c.observe_transcript("Actually find a flight to Riga")
    assert pending.superseded
    with pytest.raises(Superseded):
        await child
    assert len(b.calls) == 1


async def test_full_transcript_extension_scopes_only_the_replacement_tail(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find my flight and schedule coffee at five")
    await c.execute(FLIGHT, {"query": "my flight"})
    old = c.execute(EVENT, {"title": "coffee", "time": "five"})
    c.observe_transcript("Find my flight and schedule coffee at five actually six")
    with pytest.raises(Superseded):
        await old
    await c.execute(FLIGHT, {"query": "my flight"})
    assert sum(name == "flight_lookup" for name, _ in c.backend.calls) == 1


async def test_ambiguity_in_unrelated_clause_does_not_block_committed_read(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find my flight and coffee at five or six, not sure")
    await c.execute(FLIGHT, {"query": "my flight"})
    assert len(c.backend.calls) == 1


async def test_unresolved_mutation_alternative_is_not_permission_to_write(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Schedule coffee at five or six, I have not decided")
    with pytest.raises(InvalidCall):
        await c.execute(EVENT, {"title": "coffee", "time": "five"})
    assert not c.backend.calls


async def test_explicit_boolean_search_query_is_not_an_undecided_alternative(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find my flight OR hotel messages")
    await c.execute(FLIGHT, {"query": "flight OR hotel"})
    assert len(c.backend.calls) == 1


async def test_appended_correction_does_not_retarget_identifier_from_unchanged_prefix(tmp_path):
    from reprise.catalog import CATALOG

    c = make(tmp_path)
    text = "Track order SR42 and schedule coffee at five"
    c.observe_transcript(text)
    await c.execute(CATALOG["track_order"], {"order_id": "SR42"})
    old = c.execute(EVENT, {"title": "coffee", "time": "five"})
    c.observe_transcript(text + " actually six")
    with pytest.raises(Superseded):
        await old
    await c.execute(CATALOG["track_order"], {"order_id": "SR42"})
    assert len(c.backend.calls) == 1


async def test_explicit_current_identifier_is_new_authority_not_stale_lookup(tmp_path):
    b = Reads(result_id="RETURNED7")
    b.release.set()
    c = make(tmp_path, b)
    source = FLIGHT
    write = ToolSpec(
        "reserve",
        "Reserve",
        {"flight_id": S},
        ("flight_id",),
        True,
        references={"flight_id": ("flight_lookup", "items", "flight_id")},
    )
    c.observe_transcript("Find my flight")
    await c.execute(source, {"query": "my flight"})
    c.observe_transcript("Actually ignore that flight search")
    c.observe_transcript("Book flight RETURNED7")
    await c.execute(write, {"flight_id": "RETURNED7"})
    assert b.calls[-1][0] == "reserve"


@pytest.mark.parametrize(
    "text", ["Stop", "Cancel that", "Never mind all that, start a different request"]
)
async def test_whole_goal_cancellation_does_not_leave_unrelated_pending_writes(tmp_path, text):
    c = make(tmp_path)
    c.observe_transcript("Schedule coffee at five and add a task collect fabric")
    old = [
        c.execute(EVENT, {"title": "coffee", "time": "five"}),
        c.execute(TASK, {"title": "collect fabric"}),
    ]
    c.observe_transcript(text)
    results = await asyncio.gather(*old, return_exceptions=True)
    assert all(isinstance(r, Superseded) for r in results)
    assert not c.backend.calls


async def test_named_abandonment_keeps_unrelated_lookup(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find my flight and schedule coffee at five")
    await c.execute(FLIGHT, {"query": "my flight"})
    old = c.execute(EVENT, {"title": "coffee", "time": "five"})
    c.observe_transcript("Never mind coffee")
    with pytest.raises(Superseded):
        await old
    await c.execute(FLIGHT, {"query": "my flight"})
    assert len(c.backend.calls) == 1


async def test_google_write_invalidates_affected_cached_reads(tmp_path):
    import httpx
    from test_productivity import backend
    from reprise.productivity import PRODUCTIVITY_TOOLS
    from reprise.coordinator import Coordinator
    from reprise.config import Settings
    from reprise.trace import Trace

    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"messages": []})
        return httpx.Response(
            200, json={"id": "draft-id", "message": {"id": "message-id", "labelIds": ["DRAFT"]}}
        )

    b, client = backend(tmp_path, handler)
    b.google.save_tokens(
        {
            "access_token": "token",
            "refresh_token": "refresh",
            "expires_at": 9999999999,
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
        }
    )
    c = Coordinator(
        b, Trace("room-a", tmp_path / "trace"), Settings(settle_ms=0, write_settle_ms=0)
    )
    specs = {s.name: s for s in PRODUCTIVITY_TOOLS}
    c.observe_transcript("Search my mail and save a draft")
    await c.execute(specs["search_gmail"], {"query": "in:drafts"})
    plan = (
        await c.execute(
            specs["prepare_plan"],
            {
                "title": "Draft",
                "messages": [
                    {
                        "to": ["someone@example.com"],
                        "subject": "Note",
                        "body": "Hello",
                        "delivery": "draft",
                    }
                ],
            },
        )
    )["plan"]
    await c.execute(specs["execute_plan"], {"plan_id": plan["plan_id"]})
    await c.execute(specs["search_gmail"], {"query": "in:drafts"})
    assert [r.method for r in requests] == ["GET", "POST", "GET"]
    await client.aclose()


async def test_reusing_old_search_must_not_adopt_newer_lookup_selection(tmp_path):
    import httpx
    from test_productivity import backend
    from reprise.productivity import PRODUCTIVITY_TOOLS
    from reprise.coordinator import Coordinator
    from reprise.config import Settings
    from reprise.trace import Trace

    reads = []

    def handler(request):
        reads.append(request)
        return httpx.Response(
            200,
            json={"items": [{"id": "same-task", "title": "Read chapter", "status": "needsAction"}]},
        )

    b, client = backend(tmp_path, handler)
    c = Coordinator(
        b, Trace("room-a", tmp_path / "trace"), Settings(settle_ms=0, write_settle_ms=0)
    )
    spec = next(s for s in PRODUCTIVITY_TOOLS if s.name == "list_tasks")
    c.observe_transcript("Read tasks")
    await c.execute(spec, {"query": "Read"})
    await c.execute(spec, {"query": "chapter"})
    await c.execute(spec, {"query": "Read"})
    assert len(reads) == 3
    await client.aclose()


async def test_comparison_in_one_clause_does_not_authorize_undecided_write(tmp_path):
    c = make(tmp_path)
    c.observe_transcript(
        "Read both flight messages and schedule coffee at five or six, I have not decided"
    )
    await c.execute(FLIGHT, {"query": "flight messages"})
    with pytest.raises(InvalidCall):
        await c.execute(EVENT, {"title": "coffee", "time": "five"})
    assert [name for name, _ in c.backend.calls] == ["flight_lookup"]


async def test_invalidated_inflight_read_retains_receipt_but_not_current_result(tmp_path):
    class ChangingReads(Reads):
        def invalidates_reads(self, name, args):
            return {"flight_lookup"} if name == "calendar_create" else set()

    b = ChangingReads()
    c = make(tmp_path, b)
    c.observe_transcript("Find my flight and schedule coffee at five")
    job = asyncio.create_task(c.execute(FLIGHT, {"query": "my flight"}))
    await b.started.wait()
    await c.execute(EVENT, {"title": "coffee", "time": "five"})
    b.release.set()
    with pytest.raises(Superseded):
        await job
    assert not c.results.get("flight_lookup")
    read = next(op for op in c.operations.values() if not op.writes)
    assert read.outcome == "success" and read.result["items"]  # Actual API receipt preserved.
    await c.execute(FLIGHT, {"query": "my flight"})
    assert sum(name == "flight_lookup" for name, _ in b.calls) == 2


@pytest.mark.parametrize("previously_approved", [False, True])
async def test_scoped_correction_keeps_unrelated_risky_action_approvable(tmp_path, previously_approved):
    import httpx
    from test_productivity import backend, request_plan

    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "sent-mail"})
    )
    b.observe_input("Schedule coffee and send the email")
    p = b.store.prepare(b.room, "Mixed", [
        {"kind": "calendar", "title": "coffee"},
        {"kind": "email", **request_plan()["messages"][0]},
    ])
    if previously_approved:
        b.store.approve(b.room, p["plan_id"])
    claimed = b.store.claim(b.room, p["plan_id"])
    b.observe_input("Actually move coffee to six", revision=2)
    result = await b._execute_claimed(claimed)
    assert result["status"] == "approval_required"
    assert [a["status"] for a in result["plan"]["actions"]] == [
        "not_executed", "approval_required"
    ]
    assert not calls
    b.store.approve(b.room, p["plan_id"])
    result = await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert result["status"] == "partial"  # Not all original actions completed.
    assert result["plan"]["actions"][1]["google_id"] == "sent-mail"
    assert len(calls) == 1
    await client.aclose()


async def test_prepared_plan_with_only_completed_and_revoked_actions_is_terminal(tmp_path):
    import httpx
    from test_productivity import backend

    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "unused"}))
    p = b.store.prepare(b.room, "Mixed", [
        {"kind": "task", "title": "fabric"}, {"kind": "calendar", "title": "coffee"}
    ])
    p["actions"][0].update(status="completed", google_id="real-task")
    b.store.save(p)
    b.observe_input("Actually cancel coffee")
    saved = b.store.get(b.room, p["plan_id"])
    assert saved["status"] == "partial"
    assert saved["actions"][0]["google_id"] == "real-task"
    await client.aclose()


async def test_fresh_id_lookup_can_refresh_expired_snapshot_but_not_superseded_intent(tmp_path):
    b = Reads()
    b.release.set()
    c = make(tmp_path, b)
    detail = ToolSpec("flight_details", "Read details", {"flight_id": S}, ("flight_id",),
                      reuse_for_s=30)
    write = ToolSpec("reserve", "Reserve", {"flight_id": S}, ("flight_id",), writes=True)
    c.observe_transcript("Find my flight and reserve it")
    await c.execute(FLIGHT, {"query": "my flight"})
    next(iter(c.operations.values())).completed_at -= 31
    await c.execute(detail, {"flight_id": "returned-flight"})
    await c.execute(write, {"flight_id": "returned-flight"})
    assert [name for name, _ in b.calls] == ["flight_lookup", "flight_details", "reserve"]
    c.observe_transcript("Actually find the flight to Riga")
    with pytest.raises((InvalidCall, Superseded)):
        await c.execute(detail, {"flight_id": "returned-flight"})


async def test_replacement_plan_does_not_discard_unrelated_pending_send(tmp_path):
    import httpx
    from test_productivity import backend, request_plan

    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "unused"}))
    b.observe_input("Schedule coffee and send the email")
    original = b.store.prepare(b.room, "Mixed", [
        {"kind": "calendar", "title": "coffee"},
        {"kind": "email", **request_plan()["messages"][0]},
    ])
    b.observe_input("Actually coffee at six")
    await b.call("prepare_plan", {"title": "Coffee", "calendar_events": [{
        "title": "coffee", "start": "2030-11-06T18:00:00+05:30",
        "end": "2030-11-06T18:30:00+05:30"
    }]})
    retained = b.store.get(b.room, original["plan_id"])
    assert retained["status"] == "prepared"
    assert [a["status"] for a in retained["actions"]] == ["superseded", "pending"]
    assert not retained["approved"]
    await client.aclose()


async def test_overlapping_prepared_actions_have_only_one_executable_owner(tmp_path):
    import httpx
    from test_productivity import backend, request_plan

    b, client = backend(tmp_path, lambda r: httpx.Response(200, json={"id": "unused"}))
    first = b.store.prepare(b.room, "First", [{"kind": "task", "title": "fabric"}])
    second = b.store.prepare(b.room, "Better", [{"kind": "task", "title": "fabric"}])
    assert b.store.get(b.room, first["plan_id"])["status"] == "superseded"
    assert b.store.claim(b.room, first["plan_id"]) is None
    assert b.store.claim(b.room, second["plan_id"]) is not None

    email = {"kind": "email", **request_plan()["messages"][0]}
    original = b.store.prepare(b.room, "Mixed", [{"kind": "calendar", "title": "coffee"}, email])
    b.observe_input("Actually coffee at six")
    replacement = b.store.prepare(b.room, "Corrected", [
        {"kind": "calendar", "title": "coffee", "start": "six"}, email
    ])
    assert b.store.get(b.room, original["plan_id"])["status"] == "superseded"
    assert not replacement["approved"]
    await client.aclose()


async def test_pending_transfer_during_dispatch_cannot_send_from_both_plans(tmp_path):
    import httpx
    from test_productivity import backend, request_plan

    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        if "/tasks/" in str(request.url):
            entered.set()
            await release.wait()
        return httpx.Response(200, json={"id": f"actual-{len(calls)}"})

    b, client = backend(tmp_path, handler)
    email = {"kind": "email", **request_plan()["messages"][0]}
    task = {"kind": "task", "title": "fabric"}
    first = b.store.prepare(b.room, "Mixed", [task, email])
    b.store.approve(b.room, first["plan_id"])
    job = asyncio.create_task(b.call("execute_plan", {"plan_id": first["plan_id"]}))
    await entered.wait()
    with pytest.raises(ValueError, match="already dispatched"):
        b.store.prepare(b.room, "Duplicate running task", [task])
    replacement = b.store.prepare(b.room, "Mail", [email])
    release.set()
    result = await job
    assert [a["status"] for a in result["plan"]["actions"]] == ["completed", "not_executed"]
    assert len(calls) == 1
    b.store.approve(b.room, replacement["plan_id"])
    await b.call("execute_plan", {"plan_id": replacement["plan_id"]})
    await b.call("execute_plan", {"plan_id": first["plan_id"]})
    assert len(calls) == 2
    assert "messages/send" in str(calls[-1].url)
    await client.aclose()


async def test_named_task_correction_preserves_other_task_in_same_plan(tmp_path):
    import httpx
    from test_productivity import backend

    calls = []
    b, client = backend(
        tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={"id": "actual-task"})
    )
    b.observe_input("Add tasks collect fabric and read chapter")
    p = (await b.call("prepare_plan", {"title": "Tasks", "tasks": [
        {"title": "collect fabric"}, {"title": "read chapter"}
    ]}))["plan"]
    b.observe_input("Actually cancel the collect fabric task")
    saved = b.store.get(b.room, p["plan_id"])
    assert [a["status"] for a in saved["actions"]] == ["superseded", "pending"]
    await b.call("execute_plan", {"plan_id": p["plan_id"]})
    assert len(calls) == 1
    await client.aclose()
