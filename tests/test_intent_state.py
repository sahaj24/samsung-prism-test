import asyncio
import json

import pytest
from test_coordinator import Backend, make

from reprise.catalog import CATALOG
from reprise.coordinator import InvalidCall, Superseded


async def test_followup_retains_only_explicit_unchanged_search_slots(tmp_path):
    b = Backend()
    c = make(tmp_path, b)
    c.observe_transcript("Find apartments in Brighton with two bedrooms under 2400", is_final=True)
    await c.execute(
        CATALOG["search_apartments"], {"city": "Brighton", "bedrooms": 2, "max_price": 2400}
    )
    c.finish_response()
    c.observe_transcript("Make it three bedrooms", is_final=True)
    await c.execute(CATALOG["search_apartments"], {"bedrooms": 3})
    assert b.calls[-1][1] == {"city": "Brighton", "bedrooms": 3, "max_price": 2400}
    c.finish_response()
    c.observe_transcript("Find apartments in Dover", is_final=True)
    await c.execute(CATALOG["search_apartments"], {"city": "Dover"})
    assert b.calls[-1][1] == {"city": "Dover"}


async def test_explicit_constraint_removal_does_not_reintroduce_slot(tmp_path):
    b = Backend()
    c = make(tmp_path, b)
    c.observe_transcript("Find apartments in Lancaster under 2100", is_final=True)
    await c.execute(CATALOG["search_apartments"], {"city": "Lancaster", "max_price": 2100})
    c.finish_response()
    c.observe_transcript("Same search but no budget limit", is_final=True)
    await c.execute(CATALOG["search_apartments"], {})
    assert b.calls[-1][1] == {"city": "Lancaster"}


async def test_implicit_correction_cancels_uncommitted_write(tmp_path):
    b = Backend(prepare=0.04)
    c = make(tmp_path, b)
    c.observe_transcript("Use savings for the phone bill", is_final=True)
    task = asyncio.create_task(
        c.execute(CATALOG["modify_autopay"], {"bill_type": "phone", "source_account": "savings"})
    )
    await asyncio.sleep(0.01)
    c.observe_transcript("Make that checking", is_final=True)
    with pytest.raises(Superseded):
        await task
    assert not b.calls


async def test_interim_read_proposal_waits_for_final_and_drops_rewritten_hypothesis(tmp_path):
    b = Backend()
    c = make(tmp_path, b, gate_timeout_s=0.5)
    c.observe_transcript("Find apartments in Truro", is_final=False)
    task = asyncio.create_task(c.execute(CATALOG["search_apartments"], {"city": "Truro"}))
    await asyncio.sleep(0.02)
    assert not b.calls
    c.observe_transcript("Find apartments in Exeter", is_final=True)
    with pytest.raises(Superseded):
        await task
    await c.execute(CATALOG["search_apartments"], {"city": "Exeter"})
    assert b.calls == [("search_apartments", {"city": "Exeter"})]


async def test_explicit_identifier_conflict_requires_a_corrected_proposal(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Track order ZQ-0042", is_final=True)
    with pytest.raises(InvalidCall):
        await c.execute(CATALOG["track_order"], {"order_id": "ZQ00422"})
    assert not c.backend.calls
    await c.execute(CATALOG["track_order"], {"order_id": "ZQ-0042"})
    assert c.backend.calls == [("track_order", {"order_id": "ZQ-0042"})]
    events = [json.loads(line) for line in c.trace.path.read_text().splitlines()]
    assert any(e["event"] == "tool_validation_failed" for e in events)


async def test_spelled_digits_retain_leading_zeros_without_phonetic_guessing(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Update my passport number to K zero zero seven", is_final=True)
    with pytest.raises(InvalidCall):
        await c.execute(
            CATALOG["update_identity_doc"], {"doc_type": "passport", "doc_number": "K07"}
        )
    await c.execute(CATALOG["update_identity_doc"], {"doc_type": "passport", "doc_number": "K007"})
    assert c.backend.calls[-1][1]["doc_number"] == "K007"


async def test_ambiguous_identifier_is_not_guessed(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Track order RR8 or order RR9, I am not sure", is_final=True)
    with pytest.raises(InvalidCall):
        await c.execute(CATALOG["track_order"], {"order_id": "RR0"})
    assert not c.backend.calls


async def test_same_text_in_a_new_turn_is_a_new_request(tmp_path):
    c = make(tmp_path)
    for _ in range(2):
        c.observe_transcript("Track order D3", is_final=True)
        await c.execute(CATALOG["track_order"], {"order_id": "D3"})
        c.finish_response()
    assert len(c.backend.calls) == 2


async def test_identifier_validation_uses_final_text_after_gate(tmp_path):
    c = make(tmp_path, gate_timeout_s=0.5)
    c.observe_transcript("Track order LM-006", is_final=False)
    task = asyncio.create_task(c.execute(CATALOG["track_order"], {"order_id": "LM66"}))
    await asyncio.sleep(0.01)
    c.observe_transcript("Track order LM-006", is_final=True)
    with pytest.raises(InvalidCall):
        await task
    await c.execute(CATALOG["track_order"], {"order_id": "LM-006"})
    assert c.backend.calls[-1][1] == {"order_id": "LM-006"}


async def test_new_constraint_supersedes_uncommitted_narrower_read(tmp_path):
    b = Backend(prepare=0.04)
    c = make(tmp_path, b)
    c.observe_transcript("Find apartments in Ripon with three bedrooms", is_final=True)
    first = asyncio.create_task(c.execute(CATALOG["search_apartments"], {"city": "Ripon"}))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(
        c.execute(CATALOG["search_apartments"], {"city": "Ripon", "bedrooms": 3})
    )
    with pytest.raises(Superseded):
        await first
    await second
    assert b.calls == [("search_apartments", {"city": "Ripon", "bedrooms": 3})]


async def test_automatically_completed_integral_float_budget_survives_followup(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find a desk lamp under 50", is_final=True)
    await c.execute(CATALOG["search_products"], {"query": "desk lamp"})
    c.finish_response()
    c.observe_transcript("Same search but a floor lamp", is_final=True)
    await c.execute(CATALOG["search_products"], {"query": "floor lamp"})
    assert c.backend.calls[-1][1] == {"query": "floor lamp", "max_price": 50.0}


async def test_removed_budget_is_not_sent_even_when_model_repeats_old_value(tmp_path):
    c = make(tmp_path)
    c.observe_transcript("Find apartments in Ely under 1700", is_final=True)
    await c.execute(CATALOG["search_apartments"], {"city": "Ely", "max_price": 1700})
    c.finish_response()
    c.observe_transcript("Same search but no budget limit", is_final=True)
    await c.execute(CATALOG["search_apartments"], {"max_price": 1700})
    assert c.backend.calls[-1][1] == {"city": "Ely"}
