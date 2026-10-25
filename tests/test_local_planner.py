"""Local planner boundaries: real controller/backend, simulated model and Google HTTP only."""
import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from reprise.config import Settings
from reprise.local_planner import LocalPlanner, LocalSession, ProposalError
from reprise.productivity import PRODUCTIVITY_TOOLS
from reprise.trace import Trace
from test_productivity import backend


def completion(value):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": json.dumps(value)}}], "usage": {"total_tokens": 20}})


def proposal(title='Collect keys'):
    return {"reply": "", "calls": [{"name": "prepare_plan", "arguments": {
        "title": title, "tasks": [{"title": title}]}}]}


async def test_invalid_batch_is_rejected_before_any_dispatch():
    value = proposal()
    value['calls'].append({'name': 'execute_shell', 'arguments': {}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: completion(value))) as client:
        planner = LocalPlanner(client=client)
        with pytest.raises(ProposalError):
            await planner.complete([{'role': 'user', 'content': 'Add a task'}], PRODUCTIVITY_TOOLS)


async def test_invalid_arguments_and_truncation_are_rejected():
    bad = proposal()
    bad['calls'][0]['arguments']['unexpected'] = True
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: completion(bad))) as client:
        with pytest.raises(ProposalError):
            await LocalPlanner(client=client).complete([], PRODUCTIVITY_TOOLS)
    truncated = completion(proposal()).json()
    truncated['choices'][0]['finish_reason'] = 'length'
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=truncated))) as client:
        with pytest.raises(ProposalError):
            await LocalPlanner(client=client).complete([], PRODUCTIVITY_TOOLS)


@pytest.mark.parametrize('url', ['https://example.com', 'http://0.0.0.0:8080', 'http://user:secret@localhost:8080'])
def test_remote_or_credentialed_model_url_is_rejected(url):
    with pytest.raises(ValueError):
        LocalPlanner(base_url=url)


class Planner:
    def __init__(self, answer):
        self.answer = answer

    async def complete(self, messages, specs):
        return await self.answer(messages)


def session(tmp_path, answer, handler=None, settle=0):
    b, client = backend(tmp_path, handler or (lambda r: httpx.Response(200, json={'id': 'actual-task'})))
    trace = Trace(b.room, tmp_path/'trace')
    settings = replace(Settings(), mode='productivity', settle_ms=settle, write_settle_ms=settle)
    return LocalSession(b.room, Planner(answer), b, trace, settings), b, client


async def test_stale_inference_cannot_dispatch_under_new_input(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0
    async def answer(messages):
        nonlocal count
        count += 1
        if count == 1:
            entered.set()
            await release.wait()
            return proposal('Old keys')
        return {'reply': 'Cancelled.', 'calls': []}
    s, b, client = session(tmp_path, answer)
    old = s.submit('Add a task to collect old keys')
    await entered.wait()
    new = s.submit('Actually cancel that task')
    release.set()
    await asyncio.gather(old, new)
    events = [json.loads(line) for line in s.trace.path.read_text().splitlines()]
    assert not any(e['event'] == 'tool_dispatch' for e in events)
    assert [e['text'] for e in events if e['event'] == 'conversation_item' and e['role'] == 'assistant'] == ['Cancelled.']
    await s.close()
    await client.aclose()


async def test_duplicate_proposals_use_controller_and_write_only_once(tmp_path):
    calls = []
    async def answer(messages):
        if not messages[-1]['content'].startswith('{'):
            return proposal()
        result = json.loads(messages[-1]['content'])
        plan = result['results'][0]['result'].get('plan')
        if plan and plan['status'] == 'prepared':
            call = {'name': 'execute_plan', 'arguments': {'plan_id': plan['plan_id']}}
            return {'reply': 'Done already', 'calls': [call, call]}
        return {'reply': 'Task created.', 'calls': []}
    s, b, client = session(tmp_path, answer, lambda r: calls.append(r) or httpx.Response(200, json={'id':'real-id'}))
    await s.submit('Add a task to collect keys')
    assert len(calls) == 1
    assert b.store.get(b.room)['actions'][0]['google_id'] == 'real-id'
    events = [json.loads(line) for line in s.trace.path.read_text().splitlines()]
    assert any(e['event'] == 'tool_dispatch' for e in events)
    assert not any(e.get('text') == 'Done already' for e in events)
    await s.close()
    await client.aclose()


async def test_correction_during_settlement_prevents_old_proposal(tmp_path):
    proposed = asyncio.Event()
    async def answer(messages):
        if 'cancel' in messages[-1]['content'].lower():
            return {'reply': 'Cancelled.', 'calls': []}
        proposed.set()
        return proposal()
    s, b, client = session(tmp_path, answer, settle=150)
    old = s.submit('Add a task to collect keys')
    await proposed.wait()
    await asyncio.sleep(.02)
    new = s.submit('Actually cancel that task')
    await asyncio.gather(old, new)
    events = [json.loads(line) for line in s.trace.path.read_text().splitlines()]
    assert not any(e['event'] == 'tool_dispatch' for e in events)
    await s.close()
    await client.aclose()


async def test_failed_tool_cannot_be_replaced_with_model_success_claim(tmp_path):
    async def answer(messages):
        return {'reply': 'Completed successfully.', 'calls': [{'name': 'get_plan', 'arguments': {'plan_id': 'invented'}}]}
    s, b, client = session(tmp_path, answer)
    await s.submit('Read the plan')
    events = [json.loads(line) for line in s.trace.path.read_text().splitlines()]
    replies = [e['text'] for e in events if e['event'] == 'conversation_item' and e['role'] == 'assistant']
    assert replies and all('Completed successfully' not in text for text in replies)
    assert any('error' in text.lower() or 'failed' in text.lower() for text in replies)
    await s.close()
    await client.aclose()


async def test_risky_send_stays_pending_without_confirmation(tmp_path):
    writes = []
    async def answer(messages):
        if not messages[-1]['content'].startswith('{'):
            return {'reply':'', 'calls':[{'name':'prepare_plan','arguments':{
                'title':'Email', 'messages':[{'to':['person@example.com'], 'subject':'Schedule',
                    'body':'Tomorrow works.', 'delivery':'send'}]}}]}
        result = json.loads(messages[-1]['content'])['results'][0]['result']
        if json.loads(messages[-1]['content'])['results'][0]['name'] == 'execute_plan':
            return {'reply':'Send the email Schedule to person@example.com?', 'calls':[]}
        return {'reply':'', 'calls':[{'name':'execute_plan','arguments':{'plan_id':result['plan']['plan_id']}}]}
    s, b, client = session(tmp_path, answer, lambda r: writes.append(r) or httpx.Response(200, json={'id':'mail'}))
    await s.submit('Send person@example.com an email titled Schedule saying Tomorrow works.')
    assert not writes
    assert b.store.get(b.room)['actions'][0]['status'] == 'pending'
    assert not b.store.get(b.room)['approved']
    await s.close()
    await client.aclose()


async def test_close_waits_for_dispatched_write_receipt(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={'id':'confirmed-after-close'})
    async def answer(messages):
        if not messages[-1]['content'].startswith('{'):
            return proposal()
        result = json.loads(messages[-1]['content'])['results'][0]['result']
        return {'reply':'', 'calls':[{'name':'execute_plan','arguments':{'plan_id':result['plan']['plan_id']}}]}
    s, b, client = session(tmp_path, answer, handler)
    s.submit('Add a task to collect keys')
    await asyncio.wait_for(entered.wait(),1)
    closing = asyncio.create_task(s.close())
    await asyncio.sleep(.01)
    assert not closing.done()
    release.set()
    await closing
    assert b.store.get(b.room)['actions'][0]['google_id'] == 'confirmed-after-close'
    await client.aclose()


async def test_demo_local_messages_queue_while_previous_generation_runs(tmp_path, monkeypatch):
    from reprise import demo_server as server
    room = 'reprise-demo-1234567890abcd'
    entered, release = asyncio.Event(), asyncio.Event()
    async def answer(messages):
        entered.set()
        await release.wait()
        return {'reply':'Understood.', 'calls':[]}
    s, b, client = session(tmp_path, answer)
    monkeypatch.setattr(server, 'issued', {room})
    monkeypatch.setattr(server, 'local_sessions', {room:s})
    assert (await server.local_message(room, server.LocalMessage(text='Read my tasks')))['status'] == 'queued'
    await entered.wait()
    assert (await server.local_message(room, server.LocalMessage(text='Actually cancel that')))['status'] == 'queued'
    assert s.sequence == 2
    release.set()
    await asyncio.gather(*s.jobs)
    await s.close()
    await client.aclose()


async def test_model_http_error_is_not_retried():
    requests = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: requests.append(r) or httpx.Response(503))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await LocalPlanner(client=client).complete([], PRODUCTIVITY_TOOLS)
    assert len(requests) == 1


async def test_unknown_write_remains_unknown_without_retry_or_success_claim(tmp_path):
    writes = []
    def handler(request):
        writes.append(request)
        raise httpx.ReadTimeout('No receipt')
    async def answer(messages):
        if not messages[-1]['content'].startswith('{'):
            return proposal()
        result = json.loads(messages[-1]['content'])['results'][0]['result']
        return {'reply':'Everything succeeded.', 'calls':[{'name':'execute_plan','arguments':{'plan_id':result['plan']['plan_id']}}]}
    s, b, client = session(tmp_path, answer, handler)
    await s.submit('Add a task to collect keys')
    assert len(writes) == 1
    assert b.store.get(b.room)['actions'][0]['status'] == 'unknown'
    events = [json.loads(line) for line in s.trace.path.read_text().splitlines()]
    assert all(e.get('text') != 'Everything succeeded.' for e in events)
    await s.close()
    await client.aclose()


async def test_mixed_invalid_batch_never_registers_valid_half(tmp_path):
    async def answer(messages):
        value = proposal()
        value['calls'].append({'name':'execute_plan','arguments':{'plan_id':17}})
        return value
    s, b, client = session(tmp_path, answer)
    await s.submit('Add a task')
    events = [json.loads(line) for line in s.trace.path.read_text().splitlines()]
    assert not any(e['event'] == 'tool_raw_proposed' for e in events)
    await s.close()
    await client.aclose()


async def test_late_turn_history_keeps_current_request_after_old_receipt(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []
    async def handler(request):
        entered.set()
        await release.wait()
        return httpx.Response(200,json={'id':'actual-id'})
    async def answer(messages):
        seen.append(messages[-1]['content'])
        if not messages[-1]['content'].startswith('{'):
            if 'rename' in messages[-1]['content']:
                return {'reply':'Which title should I use?', 'calls':[]}
            return proposal()
        result=json.loads(messages[-1]['content'])['results'][0]['result']
        return {'reply':'','calls':[{'name':'execute_plan','arguments':{'plan_id':result['plan']['plan_id']}}]}
    s, b, client = session(tmp_path, answer, handler)
    old=s.submit('Add a task to collect keys')
    await entered.wait()
    new=s.submit('Actually rename that task')
    release.set()
    await asyncio.gather(old,new)
    assert seen[-1] == 'Actually rename that task'
    assert b.store.get(b.room)['actions'][0]['google_id'] == 'actual-id'
    assert any('plan_id' in entry['content'] for entry in s.history)
    await s.close()
    await client.aclose()


async def test_local_runtime_key_stays_in_server_http_headers():
    requests=[]
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: requests.append(r) or completion({'reply':'Hello.', 'calls':[]}))) as client:
        await LocalPlanner(client=client, api_key='local-private-fixture').complete([], PRODUCTIVITY_TOOLS)
    assert requests[0].headers['Authorization'] == 'Bearer local-private-fixture'
    assert b'local-private-fixture' not in requests[0].content


async def test_partial_execution_cannot_turn_into_false_completion_prose(tmp_path):
    async def answer(messages):
        if not messages[-1]['content'].startswith('{'):
            return proposal()
        saved=json.loads(messages[-1]['content'])['results'][0]
        if saved['name']=='prepare_plan':
            return {'reply':'','calls':[{'name':'execute_plan','arguments':{'plan_id':saved['result']['plan']['plan_id']}}]}
        return {'reply':'Everything completed successfully.', 'calls':[]}
    s,b,client=session(tmp_path,answer,lambda r:httpx.Response(503,json={'error':{'message':'Unavailable'}}))
    await s.submit('Add a task to collect keys')
    events=[json.loads(line) for line in s.trace.path.read_text().splitlines()]
    replies=[e['text'] for e in events if e['event']=='conversation_item' and e['role']=='assistant']
    assert replies and 'Everything completed successfully.' not in replies
    await s.close()
    await client.aclose()


async def test_stale_inference_error_cannot_publish_or_finish_new_request(tmp_path):
    entered,release=asyncio.Event(),asyncio.Event()
    async def answer(messages):
        if 'cancel' in messages[-1]['content'].lower():
            return {'reply':'Cancelled.', 'calls':[]}
        entered.set()
        await release.wait()
        raise ProposalError('Old response was malformed')
    s,b,client=session(tmp_path,answer)
    old=s.submit('Add a task')
    await entered.wait()
    new=s.submit('Actually cancel it')
    release.set()
    await asyncio.gather(old,new)
    events=[json.loads(line) for line in s.trace.path.read_text().splitlines()]
    assert [e['text'] for e in events if e['event']=='conversation_item' and e['role']=='assistant']==['Cancelled.']
    await s.close()
    await client.aclose()
