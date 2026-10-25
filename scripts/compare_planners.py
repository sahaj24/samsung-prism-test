#!/usr/bin/env python3
"""Small text-input comparison; model inference is real, all Google HTTP is isolated.

Not an FDB benchmark, voice test, account write or phone synchronization test.
"""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import time
import uuid

import httpx

from reprise.config import Settings
from reprise.google_workspace import GoogleWorkspace, WorkspaceConfig
from reprise.local_planner import LocalPlanner, LocalSession
from reprise.productivity import PlanStore, ProductivityBackend
from reprise.trace import Trace

# Fixed before running either model; no public FDB content or expected answers.
CASES = [
    {'id':'task', 'turns':['Add a task called Collect library card, due 2030-11-06.'],
     'expected':[{'kind':'task','title':'Collect library card','due_date':'2030-11-06'}]},
    {'id':'calendar', 'turns':['Create a personal calendar event called Sketch session on 2030-11-06 from 5 PM to 5:30 PM India time, no guests.'],
     'expected':[{'kind':'calendar','title':'Sketch session','start':'2030-11-06T17:00:00+05:30','end':'2030-11-06T17:30:00+05:30'}]},
    {'id':'draft', 'turns':['Save an unsent Gmail draft to person@example.com. Subject: Library visit. Body: I will arrive tomorrow.'],
     'expected':[{'kind':'email','delivery':'draft','to':['person@example.com'],'subject':'Library visit','body':'I will arrive tomorrow.'}]},
    {'id':'corrected-calendar', 'turns':['Create a personal event Sketch session on 2030-11-06 at 5 PM, actually 6 PM India time, for 30 minutes, no guests.'],
     'expected':[{'kind':'calendar','title':'Sketch session','start':'2030-11-06T18:00:00+05:30','end':'2030-11-06T18:30:00+05:30'}]},
    {'id':'cancel-pending', 'turns':['Add a task called Collect the blue folder.', 'Actually cancel that task.'],
     'interrupt_after_s':.15, 'expected':[]},
    {'id':'rename-created', 'turns':['Add a task called Check the cupboard.', 'Actually rename that task to Check the cabinet.'],
     'expected':[{'kind':'task','title':'Check the cabinet'}], 'require_update':True},
    {'id':'ambiguous', 'turns':['Schedule a meeting.'], 'expected':[]},
]


class Fixture:
    def __init__(self):
        self.resources = {}
        self.writes = []
        self.counter = 0

    def handle(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if request.method == 'POST':
            self.counter += 1
            rid = body.get('id') or f'fixture-{self.counter}'
            if path.endswith('/drafts'):
                resource = {'id':rid,'message':{'id':f'message-{self.counter}','labelIds':['DRAFT']}}
            else:
                resource = {**body,'id':rid,'etag':'"fixture-v1"','status':body.get('status','confirmed')}
            self.resources[rid] = resource
            self.writes.append({'method':'POST','path':path,'body':body,'id':rid,'at':time.monotonic()})
            return httpx.Response(200,json=resource)
        if request.method == 'PATCH':
            rid = path.rsplit('/',1)[-1]
            if rid not in self.resources:
                return httpx.Response(404,json={'error':{'message':'Fixture not found'}})
            self.resources[rid].update(body)
            self.resources[rid]['etag'] = '"fixture-v2"'
            self.writes.append({'method':'PATCH','path':path,'body':body,'id':rid,'at':time.monotonic()})
            return httpx.Response(200,json=self.resources[rid])
        if request.method == 'GET':
            if path.endswith('/users/@me/lists'):
                return httpx.Response(200,json={'items':[{'id':'fixture-list','title':'Tasks'}]})
            if path.endswith('/lists/fixture-list'):
                return httpx.Response(200,json={'id':'fixture-list','title':'Tasks'})
            rid = path.rsplit('/',1)[-1]
            if rid in self.resources:
                return httpx.Response(200,json=self.resources[rid])
            if path.endswith('/events') or path.endswith('/tasks'):
                return httpx.Response(200,json={'items':list(self.resources.values())})
            return httpx.Response(404,json={'error':{'message':'Fixture not found'}})
        raise RuntimeError(f'Unsupported fixture request: {request.method} {path}')


def build_backend(folder, trace, fixture):
    config = WorkspaceConfig(client_id='fixture', client_secret='fixture',
        token_file=folder/'fixture-token.json', task_list_id='fixture-list',
        calendar_id='fixture-calendar', time_zone='Asia/Kolkata')
    google = GoogleWorkspace(config, httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)), trace)
    google.save_tokens({'access_token':'fixture-only','refresh_token':'fixture-only',
        'expires_at':time.time()+3600, 'scope':'https://www.googleapis.com/auth/gmail.readonly'})
    return ProductivityBackend(trace.room, trace, google, PlanStore(folder/'plans.sqlite3'))


async def run_case(model, case, folder):
    folder.mkdir(parents=True, exist_ok=False)
    trace = Trace('comparison-'+uuid.uuid4().hex[:10], folder)
    fixture = Fixture()
    backend = build_backend(folder, trace, fixture)
    settings = replace(Settings.from_env(), mode='productivity', policy='guarded', tool_timeout_s=180)
    session = None
    coordinator = None
    error = None
    start = time.monotonic()
    try:
        if model == 'local':
            session = LocalSession(trace.room, LocalPlanner(), backend, trace, settings)
            def submit(text):
                return session.submit(text)
        else:
            from livekit.agents import Agent, AgentSession
            from reprise.agent import build_model, make_tool
            from reprise.coordinator import Coordinator
            from reprise.productivity import PRODUCTIVITY_TOOLS
            from reprise.prompts import PRODUCTIVITY_PROMPT
            from reprise.session_events import attach_session_events
            coordinator = Coordinator(backend, trace, settings)
            session = AgentSession(llm=build_model(settings), tools=[make_tool(s, coordinator) for s in PRODUCTIVITY_TOOLS], max_tool_steps=12)
            attach_session_events(session, coordinator)
            await session.start(agent=Agent(instructions=PRODUCTIVITY_PROMPT))
            active_run = None
            async def send(text):
                nonlocal active_run
                coordinator.observe_transcript(text, source='text', item_id=uuid.uuid4().hex)
                await session.interrupt()
                if active_run is not None and not active_run.done():
                    await active_run
                active_run = session.run(user_input=text)
                await active_run
            def submit(text):
                return asyncio.create_task(send(text))
        async with asyncio.timeout(180):
            if case.get('interrupt_after_s'):
                first = submit(case['turns'][0])
                await asyncio.sleep(case['interrupt_after_s'])
                second = submit(case['turns'][1])
                await asyncio.gather(first, second)
            else:
                for text in case['turns']:
                    await submit(text)
    except Exception as exc:
        error = {'type':type(exc).__name__, 'message':str(exc)[:400]}
    finally:
        if session:
            if model == 'local':
                await session.close()
            else:
                await session.aclose()
                await coordinator.close()
        await backend.close()
        await backend.google.client.aclose()
    events = [json.loads(line) for line in trace.path.read_text().splitlines()]
    plans = []
    for event in events:
        if event['event'] == 'plan_updated':
            plans.append(event['plan'])
    # Track final confirmed resource state via actual backend receipts, not model prose.
    final = {}
    for plan in plans:
        for action in plan['actions']:
            if action.get('google_id') and action['status'] == 'completed':
                final[action['google_id']] = {**action, **action.get('current_values',{})}
    actual = list(final.values())
    expected = case['expected']
    def match(action, want):
        return all(action.get(k) == v for k,v in want.items())
    passes = (not error and len(actual) == len(expected) and all(
        any(match(action,want) for action in actual) for want in expected))
    creates = [w for w in fixture.writes if w['method']=='POST']
    updates = [w for w in fixture.writes if w['method']=='PATCH']
    if case.get('require_update'):
        passes = passes and len(creates)==1 and len(updates)==1 and creates[0]['id']==updates[0]['id']
    # Empty-expected cancellation/incomplete cases must have no writes at all.
    if not expected and fixture.writes:
        passes = False
    dispatch = [e for e in events if e['event']=='tool_dispatch']
    result = {'case':case['id'],'model':model,'modality':'text-input; Gemini Live may generate audio; no microphone',
        'google':'isolated HTTP fixtures, no account writes','pass':bool(passes),'error':error,
        'expected':expected,'actual':actual,'writes':fixture.writes,
        'tool_dispatches':[{'function':e['function'],'args':e['args'],'elapsed_s':e['elapsed_s']} for e in dispatch],
        'superseded':sum(e['event']=='tool_superseded' for e in events),
        'stale_results_suppressed':sum(e['event']=='stale_result_suppressed' for e in events),
        'first_dispatch_s':dispatch[0]['elapsed_s'] if dispatch else None,
        'duration_s':time.monotonic()-start}
    (folder/'result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:result[k] for k in ['model','case','pass','error','first_dispatch_s','duration_s']}),flush=True)
    return result


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', choices=['local','gemini'], default=['local','gemini'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'cases.json').write_text(json.dumps(CASES,indent=2))
    results = []
    for model in args.models:
        for case in CASES:
            results.append(await run_case(model, case, args.output/model/case['id']))
            (args.output/'results.json').write_text(json.dumps(results,indent=2))


if __name__ == '__main__':
    asyncio.run(main())
