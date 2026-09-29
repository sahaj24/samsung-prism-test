import asyncio
import json
from dataclasses import replace

import pytest

from reprise.catalog import CATALOG
from reprise.backend import FDBBackend
from reprise.config import Settings
from reprise.coordinator import Coordinator, InvalidCall, OutcomeUnknown, Superseded
from reprise.trace import Trace


class Backend:
    def __init__(self, prepare=0, execute=0, fail=False):
        self.delay, self.execute_delay, self.fail = prepare, execute, fail
        self.calls = []
        self.started = asyncio.Event()

    async def prepare(self, name):
        await asyncio.sleep(self.delay)

    async def call(self, name, args):
        self.calls.append((name, args))
        self.started.set()
        await asyncio.sleep(self.execute_delay)
        if self.fail:
            raise TimeoutError()
        if name == "search_products":
            return {"status": "success", "products": [{"product_id": "fresh-763"}]}
        return {"status": "success", "value": args}


def make(tmp_path, backend=None, **settings):
    trace = Trace("test", tmp_path, tmp_path / "official.jsonl")
    return Coordinator(backend or Backend(), trace,
                       replace(Settings(settle_ms=0, write_settle_ms=0), **settings))


async def test_concurrent_duplicate_write_executes_once(tmp_path):
    b = Backend(execute=.03)
    c = make(tmp_path, b)
    c.observe_transcript("Change utility autopay to savings")
    spec, args = CATALOG["modify_autopay"], {"bill_type": "utility", "source_account": "savings"}
    results = await asyncio.gather(*(c.execute(spec, args) for _ in range(12)))
    assert len(b.calls) == 1
    assert all(r == results[0] for r in results)
    assert len((tmp_path / "official.jsonl").read_text().splitlines()) == 1


async def test_spoken_ceiling_controls_a_conditional_product_search(tmp_path):
    b = Backend()
    c = make(tmp_path, b)
    c.observe_transcript("Find a desk lamp under $75 and add one if there is one.")
    await c.execute(CATALOG["search_products"], {"query": "desk lamp"})
    assert b.calls[0][1] == {"query": "desk lamp", "max_price": 75.0}


async def test_canonical_api_formats_keep_meaning(tmp_path):
    b = Backend()
    c = make(tmp_path, b)
    c.observe_transcript("Flights for July 3rd, and use my checking account for rent.")
    await c.execute(CATALOG["search_flights"], {"destination": "Boston", "date": "July 3rd"})
    await c.execute(CATALOG["modify_autopay"],
                    {"bill_type": "rent", "source_account": "checking account"})
    await c.execute(CATALOG["get_card_benefits"], {"card_type": "gold card"})
    assert b.calls[0][1]["date"] == "July 3"
    assert b.calls[1][1]["source_account"] == "checking"
    assert b.calls[2][1]["card_type"] == "gold"


async def test_correction_cancels_prepared_write_before_side_effect(tmp_path):
    b = Backend(prepare=.05)
    c = make(tmp_path, b)
    c.observe_transcript("Use savings")
    task = asyncio.create_task(c.execute(CATALOG["modify_autopay"],
                                        {"bill_type": "phone", "source_account": "savings"}))
    await asyncio.sleep(.01)
    c.observe_transcript("Actually use checking")
    with pytest.raises(Superseded):
        await task
    assert b.calls == []
    await c.execute(CATALOG["modify_autopay"],
                    {"bill_type": "phone", "source_account": "checking"})
    assert b.calls[0][1]["source_account"] == "checking"


async def test_additive_followup_preserves_pending_action(tmp_path):
    b = Backend(prepare=.04)
    c = make(tmp_path, b)
    c.observe_transcript("Track order GG5")
    initial_revision = c.revision
    task = asyncio.create_task(c.execute(CATALOG["track_order"], {"order_id": "GG5"}))
    await asyncio.sleep(.01)
    c.speech_started()
    c.observe_transcript("And also add item X1 to my cart")
    c.speech_ended()
    assert c.revision == initial_revision
    assert (await task)["status"] == "success"
    assert b.calls == [("track_order", {"order_id": "GG5"})]


async def test_committed_write_is_recorded_even_after_correction(tmp_path):
    b = Backend(execute=.04)
    c = make(tmp_path, b)
    c.observe_transcript("Use savings")
    args = {"bill_type": "phone", "source_account": "savings"}
    task = asyncio.create_task(c.execute(CATALOG["modify_autopay"], args))
    await b.started.wait()
    c.observe_transcript("Wait, explain that first")
    with pytest.raises(Superseded):
        await task
    # The action happened; repeating its call must reuse the completed result.
    await c.execute(CATALOG["modify_autopay"], args)
    assert len(b.calls) == 1
    lines = [json.loads(x) for x in (tmp_path / "official.jsonl").read_text().splitlines()]
    assert len(lines) == 1 and lines[0]["call"]["outcome"] == "success"


async def test_cancelled_read_cannot_leak_old_result(tmp_path):
    b = Backend(execute=.1)
    c = make(tmp_path, b)
    c.observe_transcript("Track blue")
    task = asyncio.create_task(c.execute(CATALOG["track_order"], {"order_id": "blue"}))
    await b.started.wait()
    c.observe_transcript("Actually track green")
    with pytest.raises(Superseded):
        await task
    assert not c.results


async def test_reference_is_bound_to_actual_result(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find a notebook and add two")
    with pytest.raises(InvalidCall):
        await c.execute(CATALOG["add_to_cart"], {"product_id": "guessed", "quantity": 2})
    await c.execute(CATALOG["search_products"], {"query": "notebook"})
    await c.execute(CATALOG["add_to_cart"], {"product_id": "fresh-763", "quantity": 2})
    assert len(c.backend.calls) == 2


async def test_explicit_identifier_can_be_used_without_search(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Add product AB-008 twice")
    await c.execute(CATALOG["add_to_cart"], {"product_id": "AB-008", "quantity": 2})


async def test_schema_rejects_unknown_and_fractional_integer(tmp_path):
    c = make(tmp_path)
    with pytest.raises(InvalidCall):
        await c.execute(CATALOG["track_order"], {"order_id": "X", "hallucinated": 1})
    with pytest.raises(InvalidCall):
        await c.execute(CATALOG["add_to_cart"], {"product_id": "X", "quantity": "1.5"})
    assert not c.backend.calls


async def test_unknown_write_outcome_is_never_retried(tmp_path):
    b = Backend(fail=True)
    c = make(tmp_path, b)
    args = {"doc_type": "passport", "doc_number": "AB0091"}
    for _ in range(2):
        with pytest.raises(OutcomeUnknown):
            await c.execute(CATALOG["update_identity_doc"], args)
    assert len(b.calls) == 1


async def test_new_request_can_repeat_a_completed_action(tmp_path):
    c = make(tmp_path)
    args = {"doc_type": "passport", "doc_number": "AB0091"}
    c.observe_transcript("Update my passport")
    await c.execute(CATALOG["update_identity_doc"], args)
    c.finish_response()
    c.observe_transcript("Please repeat the same update")
    await c.execute(CATALOG["update_identity_doc"], args)
    assert len(c.backend.calls) == 2


async def test_backchannel_preserves_work(tmp_path):
    b = Backend(prepare=.03)
    c = make(tmp_path, b)
    c.observe_transcript("Track my parcel")
    task = asyncio.create_task(c.execute(CATALOG["track_order"], {"order_id": "Z004"}))
    await asyncio.sleep(.01)
    c.speech_started()
    c.observe_transcript("mhm")
    c.speech_ended()
    assert (await task)["status"] == "success"


async def test_no_dispatch_while_user_speaking(tmp_path):
    c = make(tmp_path)
    c.speech_started()
    task = asyncio.create_task(c.execute(CATALOG["track_order"], {"order_id": "Y"}))
    await asyncio.sleep(.03)
    assert not c.backend.calls
    c.speech_ended()
    await task
    assert len(c.backend.calls) == 1


async def test_punctuation_only_transcript_does_not_cancel(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Track order Zee")
    rev = c.revision
    c.observe_transcript("Track order Zee.", is_final=True)
    assert c.revision == rev


async def test_explicit_identifier_survives_followup_turn(tmp_path):
    b = Backend()
    c = make(tmp_path, b)
    c.observe_transcript("Add item R7 to my cart")
    c.remember_user_message("Add item R7 to my cart")
    c.finish_response()
    c.observe_transcript("Make it one, and check my order")
    await c.execute(CATALOG["add_to_cart"], {"product_id": "R7", "quantity": 1})
    assert b.calls[-1][1]["product_id"] == "R7"


async def test_partial_apartment_search_reaches_unchanged_mock(tmp_path):
    backend = FDBBackend()
    c = make(tmp_path, backend)
    c.observe_transcript("Search for a pet-friendly three-bedroom place")
    result = await c.execute(CATALOG["search_apartments"],
                             {"bedrooms": 3, "pets_allowed": True})
    assert result["status"] == "success"
    assert result["results"][0]["beds"] == 3
    assert CATALOG["search_apartments"].required == ()


async def test_spoken_identifiers_and_commute_labels_are_canonical(tmp_path):
    b = Backend()
    c = make(tmp_path, b)
    c.observe_transcript("Update my driver's license to D-L-5-5-5 and walk from my house to the office")
    await c.execute(CATALOG["update_identity_doc"],
                    {"doc_type": "driver's license", "doc_number": "D-L-5-5-5"})
    await c.execute(CATALOG["calculate_commute"],
                    {"origin_address": "home", "destination_address": "office", "mode": "walk"})
    assert b.calls[0][1] == {"doc_type": "driver_license", "doc_number": "DL555"}
    assert b.calls[1][1] == {"origin_address": "my house", "destination_address": "the office",
                              "mode": "walking"}


async def test_same_tool_calls_keep_proposal_order_despite_latency(tmp_path):
    class StaggeredBackend(Backend):
        prepared = 0

        async def prepare(self, name):
            self.prepared += 1
            await asyncio.sleep(.08 if self.prepared == 1 else 0)

    b = StaggeredBackend()
    c = make(tmp_path, b)
    c.observe_transcript("Change mortgage from savings, then credit card from checking")
    first = asyncio.create_task(c.execute(CATALOG["modify_autopay"],
                                          {"bill_type": "mortgage", "source_account": "savings"}))
    second = asyncio.create_task(c.execute(CATALOG["modify_autopay"],
                                           {"bill_type": "credit_card", "source_account": "checking"}))
    await asyncio.gather(first, second)
    assert [args["bill_type"] for _, args in b.calls] == ["mortgage", "credit_card"]
