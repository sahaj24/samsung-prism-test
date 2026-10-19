"""Ordered event regressions; no network or wall-clock scheduling assumptions."""

import asyncio
from types import SimpleNamespace

import pytest
from test_coordinator import Backend, make

from reprise.catalog import CATALOG
from reprise.coordinator import OutcomeUnknown, Superseded
from reprise.session_events import attach_session_events


class PausedBackend(Backend):
    def __init__(self):
        super().__init__()
        self.preparing = asyncio.Queue()
        self.release = asyncio.Event()

    async def prepare(self, name):
        self.preparing.put_nowait(name)
        await self.release.wait()


async def test_final_request_survives_transcriptless_acoustic_pulse(tmp_path):
    b = PausedBackend()
    c = make(tmp_path, b, gate_timeout_s=0.03)
    c.speech_started()
    c.speech_ended()
    c.observe_transcript("Convert 85 euros to yen", is_final=True)
    task = asyncio.create_task(
        c.execute(
            CATALOG["get_exchange_rate"],
            {"amount": 85, "from_currency": "EUR", "to_currency": "JPY"},
        )
    )
    await b.preparing.get()
    c.speech_started()
    c.speech_ended()
    b.release.set()
    assert (await task)["status"] == "success"
    assert len(b.calls) == 1


async def test_backchannel_cannot_commit_an_unfinished_transcript(tmp_path):
    c = make(tmp_path, gate_timeout_s=0.03)
    c.observe_transcript("Change the phone bill to", is_final=False)
    c.observe_transcript("okay", is_final=True)
    with pytest.raises(Superseded):
        await c.execute(
            CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": "savings"}
        )
    assert not c.backend.calls


async def test_duplicate_speech_start_does_not_erase_finality(tmp_path):
    c = make(tmp_path, gate_timeout_s=0.03)
    c.speech_started()
    c.observe_transcript("Track order RX83", is_final=True)
    c.speech_started()
    c.speech_ended()
    assert (await c.execute(CATALOG["track_order"], {"order_id": "RX83"}))["status"] == "success"


async def test_old_identified_final_cannot_replace_new_partial_or_grant_consent(tmp_path):
    c = make(tmp_path, gate_timeout_s=0.03)
    c.observe_transcript("Use savings", is_final=True, item_id="old")
    c.observe_transcript("Actually use checking", is_final=False, item_id="new")
    revision = c.revision
    c.observe_transcript("Use savings", is_final=True, source="conversation", item_id="old")
    assert c.transcript == "Actually use checking" and not c.input_final
    assert c.revision == revision
    with pytest.raises(Superseded):
        await c.execute(
            CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": "checking"}
        )
    c.observe_transcript("Actually use checking", is_final=True, item_id="new")
    assert (
        await c.execute(
            CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": "checking"}
        )
    )["status"] == "success"


async def test_delayed_interim_after_same_item_final_cannot_reopen_request(tmp_path):
    c = make(tmp_path, gate_timeout_s=0.03)
    c.observe_transcript("Track order RX83", is_final=True, item_id="one")
    c.observe_transcript("Track order RX8", is_final=False, item_id="one")
    assert c.transcript == "Track order RX83" and c.input_final
    assert (await c.execute(CATALOG["track_order"], {"order_id": "RX83"}))["status"] == "success"


def test_session_forwards_input_identity_and_ordering_metadata(tmp_path):
    c = make(tmp_path)
    callbacks = {}

    class Session:
        def on(self, name):
            def register(fn):
                callbacks[name] = fn
                return fn

            return register

    attach_session_events(Session(), c)
    callbacks["user_input_transcribed"](
        SimpleNamespace(
            transcript="Actually use checking", is_final=True, item_id="latest", created_at=20.0
        )
    )
    callbacks["user_input_transcribed"](
        SimpleNamespace(transcript="Use savings", is_final=False, item_id="older", created_at=10.0)
    )
    assert c.transcript == "Actually use checking" and c.input_final


async def test_missing_final_stays_uncommitted_after_silence(tmp_path):
    c = make(tmp_path, gate_timeout_s=0.03)
    c.speech_started()
    c.observe_transcript("Change phone bill to savings", is_final=False)
    c.speech_ended()
    with pytest.raises(Superseded):
        await c.execute(
            CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": "savings"}
        )
    assert not c.backend.calls


@pytest.mark.parametrize(
    "text,product",
    [
        ("Add three item three of the items M7 to my cart", "M7"),
        ("Add product AX-008 to my cart", "AX-008"),
    ],
)
async def test_valid_product_identifier_is_not_replaced_or_reformatted(tmp_path, text, product):
    c = make(tmp_path)
    c.observe_transcript(text)
    await c.execute(CATALOG["add_to_cart"], {"product_id": product, "quantity": 3})
    assert c.backend.calls == [("add_to_cart", {"product_id": product, "quantity": 3})]


async def test_parallel_document_updates_keep_each_entity_identifier(tmp_path):
    c = make(tmp_path)
    c.observe_transcript(
        "Update my passport number to PX773 and my driver's license; change that one to LM009 too"
    )
    await asyncio.gather(
        c.execute(CATALOG["update_identity_doc"], {"doc_type": "passport", "doc_number": "PX773"}),
        c.execute(
            CATALOG["update_identity_doc"], {"doc_type": "driver's license", "doc_number": "LM009"}
        ),
    )
    assert [args["doc_number"] for _, args in c.backend.calls] == ["PX773", "LM009"]


async def test_identifier_conflict_is_rejected_not_silently_replaced(tmp_path):
    from reprise.coordinator import InvalidCall

    c = make(tmp_path)
    c.observe_transcript("Track order ZR006")
    with pytest.raises(InvalidCall):
        await c.execute(CATALOG["track_order"], {"order_id": "ZR66"})
    assert not c.backend.calls


async def test_identifier_substring_and_other_entity_do_not_authorize_reference(tmp_path):
    from reprise.coordinator import InvalidCall

    c = make(tmp_path)
    c.observe_transcript("Track order Q18 and add product ZX910 to my cart")
    for guessed in ["Q18", "ZX9"]:
        with pytest.raises(InvalidCall):
            await c.execute(CATALOG["add_to_cart"], {"product_id": guessed, "quantity": 1})
    assert not c.backend.calls


async def test_multiple_explicit_orders_preserve_each_proposal(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Track order RT8 and order RT9")
    await asyncio.gather(
        *(c.execute(CATALOG["track_order"], {"order_id": value}) for value in ["RT8", "RT9"])
    )
    assert {args["order_id"] for _, args in c.backend.calls} == {"RT8", "RT9"}


async def test_speech_response_interruption_does_not_cancel_committed_action(tmp_path):
    b = PausedBackend()
    c = make(tmp_path, b)
    c.observe_transcript("Use savings for the phone bill")
    interrupted = False
    task = asyncio.create_task(
        c.execute(
            CATALOG["modify_autopay"],
            {"bill_type": "phone", "source_account": "savings"},
            interrupted=lambda: interrupted,
        )
    )
    await b.preparing.get()
    interrupted = True
    b.release.set()
    assert (await task)["status"] == "success"
    assert len(b.calls) == 1


async def test_cancelling_one_duplicate_waiter_cannot_cancel_shared_operation(tmp_path):
    b = PausedBackend()
    c = make(tmp_path, b)
    c.observe_transcript("Track order RZ83")
    args = {"order_id": "RZ83"}
    first = asyncio.create_task(c.execute(CATALOG["track_order"], args))
    await b.preparing.get()
    # Yielding once admits the duplicate to the same operation, without a timer.
    second = asyncio.create_task(c.execute(CATALOG["track_order"], args))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    b.release.set()
    assert (await second)["status"] == "success"
    assert b.calls == [("track_order", args)]


async def test_specific_bill_correction_preserves_independent_pending_write(tmp_path):
    b = PausedBackend()
    c = make(tmp_path, b)
    c.observe_transcript("Use savings for the phone bill and checking for rent")
    phone = asyncio.create_task(
        c.execute(CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": "savings"})
    )
    rent = asyncio.create_task(
        c.execute(CATALOG["modify_autopay"], {"bill_type": "rent", "source_account": "checking"})
    )
    await b.preparing.get()
    await b.preparing.get()
    c.observe_transcript("Actually use checking for the phone bill")
    b.release.set()
    with pytest.raises(Superseded):
        await phone
    assert (await rent)["status"] == "success"
    assert b.calls == [("modify_autopay", {"bill_type": "rent", "source_account": "checking"})]


async def test_additive_turn_does_not_revalidate_old_action_against_new_entity(tmp_path):
    b = PausedBackend()
    c = make(tmp_path, b)
    c.observe_transcript("Track order RP30")
    # The proposal belongs to the ordered input preceding its creation, even
    # when the caller coroutine runs after an additive input event.
    proposed = c.execute(CATALOG["track_order"], {"order_id": "RP30"})
    c.observe_transcript("Also track order RP40")
    task = asyncio.create_task(proposed)
    b.release.set()
    assert (await task)["status"] == "success"
    assert b.calls == [("track_order", {"order_id": "RP30"})]


@pytest.mark.parametrize("reverse", [False, True])
async def test_proposals_own_input_and_arguments_before_scheduling(tmp_path, reverse):
    c = make(tmp_path)
    c.observe_transcript("Track order TZ50")
    args = {"order_id": "TZ50"}
    first = c.execute(CATALOG["track_order"], args)
    args["order_id"] = "mutated"
    c.observe_transcript("Also track order TZ60")
    second = c.execute(CATALOG["track_order"], {"order_id": "TZ60"})
    results = await asyncio.gather(*([second, first] if reverse else [first, second]))
    assert all(r["status"] == "success" for r in results)
    assert {a["order_id"] for _, a in c.backend.calls} == {"TZ50", "TZ60"}


@pytest.mark.parametrize("reverse", [False, True])
async def test_refinement_decision_does_not_depend_on_coroutine_order(tmp_path, reverse):
    c = make(tmp_path)
    c.observe_transcript("Find apartments in Wells with three bedrooms")
    first = c.execute(CATALOG["search_apartments"], {"city": "Wells"})
    second = c.execute(CATALOG["search_apartments"], {"city": "Wells", "bedrooms": 3})
    results = await asyncio.gather(
        *([second, first] if reverse else [first, second]), return_exceptions=True
    )
    assert sum(isinstance(r, Superseded) for r in results) == 1
    assert c.backend.calls == [("search_apartments", {"city": "Wells", "bedrooms": 3})]


async def test_independent_same_tool_reads_do_not_wait_for_each_other(tmp_path):
    class Independent(Backend):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def prepare(self, name):
            if not self.entered.is_set():
                self.entered.set()
                await self.release.wait()

    b = Independent()
    c = make(tmp_path, b)
    c.observe_transcript("Track order W21 and order W22")
    first = asyncio.create_task(c.execute(CATALOG["track_order"], {"order_id": "W21"}))
    await b.entered.wait()
    second = asyncio.create_task(c.execute(CATALOG["track_order"], {"order_id": "W22"}))
    try:
        assert (await asyncio.wait_for(asyncio.shield(second), 0.2))["status"] == "success"
    finally:
        b.release.set()
        await asyncio.gather(first, second, return_exceptions=True)
    assert b.calls[0][1]["order_id"] == "W22"


async def test_conflicting_writes_wait_for_actual_predecessor_outcome(tmp_path):
    class Writes(Backend):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()
            self.latest = None

        async def call(self, name, args):
            self.calls.append((name, args))
            self.started.set()
            if args["source_account"] == "savings":
                await self.release.wait()
            self.latest = args["source_account"]
            return {"status": "success"}

    b = Writes()
    c = make(tmp_path, b)
    c.observe_transcript("Change the phone bill to savings then checking")
    first = asyncio.create_task(
        c.execute(CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": "savings"})
    )
    await b.started.wait()
    second = asyncio.create_task(
        c.execute(CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": "checking"})
    )
    # Drain ready callbacks; no elapsed-time assumption for dispatch ordering.
    for _ in range(8):
        await asyncio.sleep(0)
    try:
        assert len(b.calls) == 1
    finally:
        b.release.set()
        await asyncio.gather(first, second)
    assert b.latest == "checking"


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"status": "unrecognized"},
        {"status": "running"},
        {"status": "partial"},
        {"status": "needs_review"},
    ],
)
async def test_unconfirmed_write_is_not_completed_or_redispatched(tmp_path, response):
    from reprise.coordinator import OutcomeUnknown

    class Uncertain(Backend):
        async def call(self, name, args):
            self.calls.append((name, args))
            return response

    b = Uncertain()
    c = make(tmp_path, b)
    c.observe_transcript("Use savings for phone bill")
    for _ in range(2):
        try:
            await c.execute(
                CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": "savings"}
            )
        except OutcomeUnknown:
            pass
    assert len(b.calls) == 1
    assert not c.snapshot()["actions"][0]["completed"]
    assert c.snapshot()["actions"][0]["outcome"] != "success"


async def test_dispatched_read_keeps_actual_outcome_but_suppresses_stale_delivery(tmp_path):
    class Read(Backend):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()

        async def call(self, name, args):
            self.calls.append((name, args))
            self.started.set()
            await self.release.wait()
            return {"status": "success", "value": args}

    b = Read()
    c = make(tmp_path, b)
    c.observe_transcript("Track order VN7")
    task = asyncio.create_task(c.execute(CATALOG["track_order"], {"order_id": "VN7"}))
    await b.started.wait()
    c.observe_transcript("Actually track order VN8")
    b.release.set()
    with pytest.raises(Superseded):
        await task
    op = next(iter(c.operations.values()))
    assert op.outcome == "success" and not op.task.cancelled()
    assert not c.results


async def test_followup_context_is_not_taken_from_a_later_independent_search(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find apartments in Durham with two bedrooms")
    await c.execute(CATALOG["search_apartments"], {"city": "Durham", "bedrooms": 2})
    c.observe_transcript("Same search but three bedrooms")
    followup = c.execute(CATALOG["search_apartments"], {"bedrooms": 3})
    c.observe_transcript("Also find apartments in Ely")
    independent = c.execute(CATALOG["search_apartments"], {"city": "Ely", "bedrooms": 1})
    await independent
    await followup
    assert c.backend.calls[-1][1] == {"city": "Durham", "bedrooms": 3}


async def test_partial_extension_cannot_dispatch_pre_extension_arguments(tmp_path):
    c = make(tmp_path, gate_timeout_s=0.04)
    c.observe_transcript("Find apartments in Ely", is_final=False)
    old = c.execute(CATALOG["search_apartments"], {"city": "Ely"})
    c.observe_transcript("Find apartments in Ely with four bedrooms", is_final=True)
    with pytest.raises(Superseded):
        await old
    assert not c.backend.calls
    await c.execute(CATALOG["search_apartments"], {"city": "Ely", "bedrooms": 4})
    assert len(c.backend.calls) == 1


async def test_document_correction_does_not_cancel_other_document(tmp_path):
    b = PausedBackend()
    c = make(tmp_path, b)
    c.observe_transcript("Update my passport number to PL23 and driver's license to DG93")
    passport = asyncio.create_task(
        c.execute(CATALOG["update_identity_doc"], {"doc_type": "passport", "doc_number": "PL23"})
    )
    license = asyncio.create_task(
        c.execute(
            CATALOG["update_identity_doc"], {"doc_type": "driver_license", "doc_number": "DG93"}
        )
    )
    await b.preparing.get()
    await b.preparing.get()
    c.observe_transcript("Actually the license number is DG94")
    b.release.set()
    with pytest.raises(Superseded):
        await license
    assert (await passport)["status"] == "success"
    assert b.calls == [("update_identity_doc", {"doc_type": "passport", "doc_number": "PL23"})]


async def test_literal_identifier_separator_is_not_assumed_insignificant(tmp_path):
    from reprise.coordinator import InvalidCall

    c = make(tmp_path)
    c.observe_transcript("Add product MX-008 to my cart")
    with pytest.raises(InvalidCall):
        await c.execute(CATALOG["add_to_cart"], {"product_id": "MX008", "quantity": 1})
    assert not c.backend.calls


async def test_correction_of_dispatched_duplicate_preserves_actual_write_receipt(tmp_path):
    class Write(Backend):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()

        async def call(self, name, args):
            self.calls.append((name, args))
            self.started.set()
            await self.release.wait()
            return {"status": "success"}

    b = Write()
    c = make(tmp_path, b)
    c.observe_transcript("Use savings for phone bill")
    args = {"bill_type": "phone", "source_account": "savings"}
    first = asyncio.create_task(c.execute(CATALOG["modify_autopay"], args))
    await b.started.wait()
    second = asyncio.create_task(c.execute(CATALOG["modify_autopay"], args))
    await asyncio.sleep(0)
    c.observe_transcript("Actually use checking for phone bill")
    b.release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(r, Superseded) for r in results)
    op = next(iter(c.operations.values()))
    assert op.outcome == "success" and not op.task.cancelled()
    assert len(b.calls) == 1 and not c.results


async def test_uncertain_write_cannot_be_retried_in_a_new_request(tmp_path):
    from reprise.coordinator import OutcomeUnknown

    b = Backend(fail=True)
    c = make(tmp_path, b)
    args = {"bill_type": "phone", "source_account": "savings"}
    c.observe_transcript("Use savings for phone bill")
    with pytest.raises(OutcomeUnknown):
        await c.execute(CATALOG["modify_autopay"], args)
    c.finish_response()
    c.observe_transcript("Please try that phone bill update again")
    with pytest.raises(OutcomeUnknown):
        await c.execute(CATALOG["modify_autopay"], args)
    assert len(b.calls) == 1


async def test_prepare_failure_is_terminal_without_a_dispatch(tmp_path):
    class Unavailable(Backend):
        async def prepare(self, name):
            raise ConnectionError("Unavailable before dispatch")

    c = make(tmp_path, Unavailable())
    c.observe_transcript("Use savings for phone bill")
    with pytest.raises(OutcomeUnknown):
        await c.execute(
            CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": "savings"}
        )
    action = c.snapshot()["actions"][0]
    assert action["state"] == "failed" and action["outcome"] == "error"
    assert not action["dispatched"] and not action["completed"]
    assert not c.backend.calls


async def test_no_input_is_not_a_committed_request(tmp_path):
    c = make(tmp_path, gate_timeout_s=0.03)
    with pytest.raises(Superseded):
        await c.execute(
            CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": "savings"}
        )
    assert not c.backend.calls


@pytest.mark.parametrize("reverse", [False, True])
async def test_registered_refinement_precedes_any_caller_scheduling(tmp_path, reverse):
    class Immediate(Backend):
        async def prepare(self, name):
            pass

    c = make(tmp_path, Immediate())
    c.observe_transcript("Find apartments in Wells with three bedrooms")
    proposals = [
        c.execute(CATALOG["search_apartments"], {"city": "Wells"}),
        c.execute(CATALOG["search_apartments"], {"city": "Wells", "bedrooms": 3}),
    ]
    results = []
    for proposal in reversed(proposals) if reverse else proposals:
        try:
            results.append(await proposal)
        except Superseded as exc:
            results.append(exc)
    assert sum(isinstance(r, Superseded) for r in results) == 1
    assert c.backend.calls == [("search_apartments", {"city": "Wells", "bedrooms": 3})]


async def test_independent_searches_are_not_misidentified_as_refinements(tmp_path):
    b = PausedBackend()
    c = make(tmp_path, b)
    c.observe_transcript("Find apartments in York")
    first = asyncio.create_task(c.execute(CATALOG["search_apartments"], {"city": "York"}))
    await b.preparing.get()
    c.observe_transcript("Also find apartments in York with two bedrooms for my sister")
    second = asyncio.create_task(
        c.execute(CATALOG["search_apartments"], {"city": "York", "bedrooms": 2})
    )
    await asyncio.sleep(0)
    b.release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(r, dict) and r["status"] == "success" for r in results)
    assert len(b.calls) == 2


async def test_dependent_write_rechecks_result_provenance_before_dispatch(tmp_path):
    from reprise.coordinator import InvalidCall

    class Dependent(PausedBackend):
        async def prepare(self, name):
            if name == "add_to_cart":
                await super().prepare(name)

    b = Dependent()
    c = make(tmp_path, b)
    c.observe_transcript("Find a notebook and add one")
    await c.execute(CATALOG["search_products"], {"query": "notebook"})
    write = asyncio.create_task(
        c.execute(CATALOG["add_to_cart"], {"product_id": "fresh-763", "quantity": 1})
    )
    await b.preparing.get()
    c.observe_transcript("Actually change the category to electronics")
    b.release.set()
    with pytest.raises((InvalidCall, Superseded)):
        await write
    assert [name for name, _ in b.calls] == ["search_products"]


async def test_identifier_punctuation_change_is_a_real_hypothesis_revision(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Track order RP-30", is_final=False)
    proposal = c.execute(CATALOG["track_order"], {"order_id": "RP-30"})
    c.observe_transcript("Track order RP30", is_final=True)
    with pytest.raises(Superseded):
        await proposal
    assert c.transcript == "Track order RP30" and not c.backend.calls


@pytest.mark.parametrize("run_earlier_first", [False, True])
async def test_followup_slots_follow_proposal_order_not_caller_scheduling(
    tmp_path, run_earlier_first
):
    c = make(tmp_path)
    c.observe_transcript("Find apartments in Bath under 1800")
    first = c.execute(CATALOG["search_apartments"], {"city": "Bath", "max_price": 1800})
    if run_earlier_first:
        await first
    c.observe_transcript("Same search but three bedrooms")
    second = c.execute(CATALOG["search_apartments"], {"bedrooms": 3})
    if not run_earlier_first:
        await first
    await second
    assert c.backend.calls[-1][1] == {"city": "Bath", "max_price": 1800, "bedrooms": 3}


async def test_late_proposal_from_superseded_response_cannot_bind_to_new_intent(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find a notebook and add one")
    await c.execute(CATALOG["search_products"], {"query": "notebook"}, response_id="old-response")
    c.observe_transcript("Actually do not add anything; just explain the options")
    with pytest.raises(Superseded):
        await c.execute(
            CATALOG["add_to_cart"],
            {"product_id": "fresh-763", "quantity": 1},
            response_id="old-response",
        )
    assert [name for name, _ in c.backend.calls] == ["search_products"]


async def test_late_mutation_from_superseded_response_does_not_regain_authority(tmp_path):
    b = PausedBackend()
    c = make(tmp_path, b)
    c.observe_transcript("Use savings for phone bill")
    args = {"bill_type": "phone", "source_account": "savings"}
    first = asyncio.create_task(c.execute(CATALOG["modify_autopay"], args, response_id="old"))
    await b.preparing.get()
    c.observe_transcript("Actually use checking for phone bill")
    late = c.execute(CATALOG["modify_autopay"], args, response_id="old")
    b.release.set()
    results = await asyncio.gather(first, late, return_exceptions=True)
    assert all(isinstance(r, Superseded) for r in results)
    assert not b.calls


async def test_replaced_identifier_in_same_final_utterance_cannot_execute(tmp_path):
    from reprise.coordinator import InvalidCall

    c = make(tmp_path)
    c.observe_transcript("Update passport PX111, actually passport PX222")
    with pytest.raises(InvalidCall):
        await c.execute(
            CATALOG["update_identity_doc"], {"doc_type": "passport", "doc_number": "PX111"}
        )
    await c.execute(CATALOG["update_identity_doc"], {"doc_type": "passport", "doc_number": "PX222"})
    assert c.backend.calls == [
        ("update_identity_doc", {"doc_type": "passport", "doc_number": "PX222"})
    ]


@pytest.mark.parametrize("reverse", [False, True])
async def test_duplicate_owner_keeps_original_order_without_a_dependency_cycle(tmp_path, reverse):
    c = make(tmp_path, gate_timeout_s=0.03, tool_timeout_s=0.03)
    c.observe_transcript("Set phone bill to savings then checking")
    calls = [
        c.execute(CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": account})
        for account in ["savings", "checking", "savings"]
    ]
    results = await asyncio.gather(*(reversed(calls) if reverse else calls), return_exceptions=True)
    assert all(isinstance(r, dict) and r["status"] == "success" for r in results)
    assert [a["source_account"] for _, a in c.backend.calls] == ["savings", "checking"]


async def test_read_refinement_does_not_obsolete_its_own_response(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find apartments in Wells with three bedrooms and track order W91")
    first = c.execute(CATALOG["search_apartments"], {"city": "Wells"}, response_id="reply")
    second = c.execute(
        CATALOG["search_apartments"], {"city": "Wells", "bedrooms": 3}, response_id="reply"
    )
    with pytest.raises(Superseded):
        await first
    await second
    assert (await c.execute(CATALOG["track_order"], {"order_id": "W91"}, response_id="reply"))[
        "status"
    ] == "success"


async def test_proposal_without_input_cannot_borrow_later_request_commitment(tmp_path):
    c = make(tmp_path, gate_timeout_s=0.03)
    proposal = c.execute(
        CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": "savings"}
    )
    c.observe_transcript("Explain what a savings account is")
    with pytest.raises(Superseded):
        await proposal
    assert not c.backend.calls


def test_finishing_assistant_speech_does_not_finish_uncommitted_input(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Use savings", is_final=True)
    c.observe_transcript("Actually use", is_final=False)
    transaction = c.transaction
    c.finish_response()
    c.observe_transcript("Actually use checking", is_final=True)
    assert c.transaction == transaction


async def test_raw_proposal_trace_precedes_normalization_and_preserves_arguments(tmp_path):
    import json

    c = make(tmp_path)
    c.observe_transcript("Find a desk lamp under 50")
    await c.execute(CATALOG["search_products"], {"query": "desk lamp"}, response_id="response")
    events = [json.loads(line) for line in c.trace.path.read_text().splitlines()]
    raw = next(e for e in events if e["event"] == "tool_raw_proposed")
    filled = next(e for e in events if e["event"] == "argument_completed_from_input")
    assert raw["args"] == {"query": "desk lamp"}
    assert raw["sequence"] < filled["sequence"]
    assert raw["proposal_id"] == filled["proposal_id"]


@pytest.mark.parametrize("identifier", ["A-7", "B-C-4-2"])
async def test_short_literal_identifier_hyphens_are_preserved(tmp_path, identifier):
    c = make(tmp_path)
    c.observe_transcript(f"Add product {identifier} to my cart")
    await c.execute(CATALOG["add_to_cart"], {"product_id": identifier, "quantity": 1})
    assert c.backend.calls == [("add_to_cart", {"product_id": identifier, "quantity": 1})]


async def test_spoken_dash_is_identifier_content(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Add product A dash seven to my cart")
    await c.execute(CATALOG["add_to_cart"], {"product_id": "A-7", "quantity": 1})
    assert c.backend.calls == [("add_to_cart", {"product_id": "A-7", "quantity": 1})]


@pytest.mark.parametrize("exception", [OutcomeUnknown, Superseded])
async def test_dispatched_write_exception_is_uncertain_even_if_it_names_a_controller_state(
    tmp_path, exception
):
    class Uncertain(Backend):
        async def call(self, name, args):
            self.calls.append((name, args))
            raise exception("Connection ended without an outcome")

    c = make(tmp_path, Uncertain())
    c.observe_transcript("Use savings for phone bill")
    for _ in range(2):
        with pytest.raises(OutcomeUnknown):
            await c.execute(
                CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": "savings"}
            )
    action = c.snapshot()["actions"][0]
    assert action["outcome"] == "unknown" and not action["completed"]
    assert len(c.backend.calls) == 1


async def test_unlabelled_identifier_replacement_rejects_old_value(tmp_path):
    from reprise.coordinator import InvalidCall

    c = make(tmp_path)
    c.observe_transcript("Update passport PX111, actually PX222")
    with pytest.raises(InvalidCall):
        await c.execute(
            CATALOG["update_identity_doc"], {"doc_type": "passport", "doc_number": "PX111"}
        )
    await c.execute(CATALOG["update_identity_doc"], {"doc_type": "passport", "doc_number": "PX222"})
    assert c.backend.calls == [
        ("update_identity_doc", {"doc_type": "passport", "doc_number": "PX222"})
    ]


async def test_old_response_authority_expires_across_request_boundary(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find a notebook and add product P7")
    await c.execute(CATALOG["search_products"], {"query": "notebook"}, response_id="old")
    c.finish_response()
    c.observe_transcript("Actually do not add anything; explain the options")
    with pytest.raises(Superseded):
        await c.execute(
            CATALOG["add_to_cart"], {"product_id": "P7", "quantity": 1}, response_id="old"
        )
    assert [name for name, _ in c.backend.calls] == ["search_products"]


async def test_identifier_correction_preserves_other_entities_in_same_utterance(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Update passport PX111 and license LX222, actually LX333")
    await c.execute(CATALOG["update_identity_doc"], {"doc_type": "passport", "doc_number": "PX111"})
    await c.execute(
        CATALOG["update_identity_doc"], {"doc_type": "driver_license", "doc_number": "LX333"}
    )
    assert [a["doc_number"] for _, a in c.backend.calls] == ["PX111", "LX333"]


async def test_correcting_second_identifier_does_not_reject_first_independent_order(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Track order BT11 and order BT22, actually order BT33")
    await c.execute(CATALOG["track_order"], {"order_id": "BT11"})
    await c.execute(CATALOG["track_order"], {"order_id": "BT33"})
    assert [a["order_id"] for _, a in c.backend.calls] == ["BT11", "BT33"]
