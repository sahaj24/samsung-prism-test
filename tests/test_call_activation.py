"""Demo Live activation boundaries, with actual Coordinator execution."""
import asyncio

import pytest

from test_coordinator import Backend, make
from reprise.catalog import CATALOG
from reprise.coordinator import Superseded


def gate_for(c):
    from reprise.call_activation import CallActivation
    gate = CallActivation(c, enabled=True)
    c.activation = gate
    return gate


def test_standby_ignores_conversation_and_generic_greetings(tmp_path):
    c = make(tmp_path)
    gate = gate_for(c)
    for text in ['hey', 'thanks', 'done', 'I told Reprise about the meeting', 'Reprise']:
        c.observe_transcript(text)
    assert gate.state == 'standby'
    assert not c.active_request


def test_addressed_request_activates_only_on_final_and_dismisses(tmp_path):
    c = make(tmp_path)
    gate = gate_for(c)
    c.observe_transcript('Hey Reprise, find my flight', is_final=False)
    assert gate.state == 'standby'
    c.observe_transcript('Hey Reprise, find my flight', is_final=True)
    assert gate.state == 'active'
    c.observe_transcript('Actually make that tomorrow')
    assert c.transcript == 'Actually make that tomorrow'
    c.observe_transcript('thanks')
    assert gate.state == 'active'
    c.observe_transcript('Thanks, Reprise', item_id='dismiss', event_time=20)
    assert gate.state == 'standby'
    c.observe_transcript('Reprise find my flight', item_id='late', event_time=10)
    assert gate.state == 'standby'


async def test_standby_blocks_tools_even_without_agent_wrapper(tmp_path):
    c = make(tmp_path)
    gate_for(c)
    with pytest.raises(Superseded):
        await c.execute(CATALOG['search_products'], {'query': 'lamp'})
    assert not c.backend.calls


async def test_dismissal_stops_pending_but_preserves_dispatched_outcome(tmp_path):
    b = Backend(prepare=.02)
    c = make(tmp_path, b)
    gate = gate_for(c)
    c.observe_transcript('Reprise find a lamp')
    pending = asyncio.create_task(c.execute(CATALOG['search_products'], {'query': 'lamp'}))
    await asyncio.sleep(0)
    c.observe_transcript("That's all, Reprise")
    with pytest.raises(Superseded):
        await pending
    assert not b.calls
    c.observe_transcript('Reprise find a desk')
    b.delay, b.execute_delay = 0, .02
    dispatched = asyncio.create_task(c.execute(CATALOG['search_products'], {'query': 'desk'}))
    await b.started.wait()
    c.observe_transcript('Thank you, Reprise')
    # The receipt must still be retained, even though no standby reply is allowed.
    await dispatched
    assert len(b.calls) == 1
    assert next(o for o in c.operations.values() if o.dispatched).outcome == 'success'
    assert gate.state == 'standby'


def test_switching_to_live_restores_ordinary_behavior(tmp_path):
    c = make(tmp_path)
    gate = gate_for(c)
    gate.set_enabled(False)
    c.observe_transcript('Find a lamp')
    assert c.active_request
    gate.set_enabled(True)
    assert gate.state == 'standby'
    assert not gate.allows_tools


async def test_output_stream_cannot_leak_across_dismissal_and_reactivation(tmp_path):
    c = make(tmp_path)
    gate = gate_for(c)
    c.observe_transcript('Reprise find my flight')
    async def source():
        yield 'first'
        c.observe_transcript('Thanks Reprise')
        c.observe_transcript('Reprise read my tasks')
        yield 'old response'
    assert [x async for x in gate.filter_output(source())] == ['first']
    async def standby_source():
        yield 'must be silent'
    c.observe_transcript('Thanks Reprise')
    assert [x async for x in gate.filter_output(standby_source())] == []


async def test_final_audio_wake_requests_one_reply_after_transcript_commit(tmp_path):
    from types import SimpleNamespace
    from reprise.session_events import attach_session_events
    c = make(tmp_path)
    gate = gate_for(c)
    class Session:
        def __init__(self):
            self.callbacks, self.replies = {}, []
        def on(self, name):
            def register(fn):
                self.callbacks[name] = fn
                return fn
            return register
        def generate_reply(self, **kwargs):
            assert c.input_final
            self.replies.append(kwargs)
    session = Session()
    attach_session_events(session, c)
    transcript = session.callbacks['user_input_transcribed']
    gate.register_response('standby-generation')
    transcript(SimpleNamespace(transcript='Hey Reprise find my flight', is_final=False, item_id='wake'))
    assert not session.replies
    transcript(SimpleNamespace(transcript='Hey Reprise find my flight', is_final=True, item_id='wake'))
    await asyncio.sleep(0)
    assert len(session.replies) == 1
    assert not gate.allows_response('standby-generation')
    transcript(SimpleNamespace(transcript='Hey Reprise find my flight', is_final=True, item_id='wake'))
    await asyncio.sleep(0)
    assert len(session.replies) == 1


async def test_dismissal_invalidates_prepared_workspace_plan_without_remote_write(tmp_path):
    import httpx
    from test_productivity import backend
    calls = []
    b, client = backend(tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={'id': 'bad'}))
    c = make(tmp_path, b)
    gate_for(c)
    c.observe_transcript('Reprise add a task to collect the parcel')
    plan = (await b.call('prepare_plan', {'title': 'Errand', 'tasks': [{'title': 'Collect parcel'}]}))['plan']
    c.observe_transcript("That's all Reprise")
    # Even a direct stale execute_plan cannot revive the prepared plan.
    result = await b.call('execute_plan', {'plan_id': plan['plan_id']})
    assert result['plan']['status'] == 'superseded'
    assert not calls
    await client.aclose()


async def test_standby_generation_does_not_gain_tool_authority_after_wake(tmp_path):
    c = make(tmp_path)
    gate = gate_for(c)
    gate.register_response('old')
    c.observe_transcript('Reprise find a lamp')
    assert not gate.allows_response('old')
    gate.register_response('new')
    assert gate.allows_response('new')
    c.observe_transcript('Thanks Reprise')
    assert not gate.allows_response('new')


def test_delayed_conversation_echo_cannot_wake_and_declarative_mention_is_not_request(tmp_path):
    c = make(tmp_path)
    gate = gate_for(c)
    c.observe_transcript('Reprise is useful')
    assert gate.state == 'standby'
    c.observe_transcript('Reprise find a lamp', source='audio', item_id='spoken')
    c.observe_transcript('Thanks Reprise', source='audio', item_id='dismissal')
    c.observe_transcript('Reprise find a lamp', source='conversation', item_id='echo')
    assert gate.state == 'standby'


async def test_typed_background_conversation_is_retained_without_generating_reply(tmp_path):
    from types import SimpleNamespace
    from livekit.agents import llm
    from reprise.agent import make_text_input_handler
    c = make(tmp_path)
    gate_for(c)
    class Agent:
        chat_ctx = llm.ChatContext.empty()
        async def update_chat_ctx(self, history):
            self.chat_ctx = history
    agent = Agent()
    # No generate_reply: this test fails if standby takes the ordinary handler.
    await make_text_input_handler(c)(SimpleNamespace(current_agent=agent), SimpleNamespace(text='Coffee sounds good'))
    assert agent.chat_ctx.items[-1].text_content == 'Coffee sounds good'
    assert not c.backend.calls


@pytest.mark.parametrize('metadata', [{}, {'presentation': 'live'}, {'presentation': 'demo-live'},
    {'presentation': 'invalid', 'activation_version': 2}])
def test_demo_room_never_defaults_to_unrestricted_when_activation_contract_missing(metadata):
    from reprise.call_activation import activation_enabled
    assert activation_enabled('reprise-demo-1234567890abcd', metadata)


def test_live_requires_explicit_current_contract_and_benchmark_is_unchanged():
    from reprise.call_activation import activation_enabled
    assert not activation_enabled('reprise-demo-1234567890abcd', {'presentation':'live', 'activation_version':2})
    assert not activation_enabled('fdb-audio-case', {})


async def test_other_person_address_is_transcript_only_and_dismissals_close_active_interval(tmp_path):
    c = make(tmp_path)
    gate = gate_for(c)
    c.observe_transcript('Hey Karthick, what plans do we have tomorrow?', item_id='background')
    assert gate.state == 'standby'
    with pytest.raises(Superseded):
        await c.execute(CATALOG['search_products'], {'query':'lamp'})
    assert not c.backend.calls
    for index, dismissal in enumerate(['Thanks, Reprise', "That's all, Reprise", 'ok', 'done']):
        c.observe_transcript('Hey Reprise, find a lamp', item_id=f'wake-{index}')
        assert gate.state == 'active'
        c.observe_transcript('Actually find a desk lamp', item_id=f'correction-{index}')
        assert c.transcript == 'Actually find a desk lamp'
        c.observe_transcript('thanks', item_id=f'thanks-{index}')
        assert gate.state == 'active'
        c.observe_transcript(dismissal, item_id=f'dismiss-{index}')
        assert gate.state == 'standby'


def test_short_acknowledgement_prefix_is_not_an_interim_dismissal(tmp_path):
    c = make(tmp_path)
    gate = gate_for(c)
    c.observe_transcript('Hey Reprise find a lamp')
    c.observe_transcript('ok', is_final=False, item_id='correction')
    assert gate.state != 'standby'
    c.observe_transcript('ok actually make it a desk lamp', is_final=True, item_id='correction')
    assert gate.state == 'active'
