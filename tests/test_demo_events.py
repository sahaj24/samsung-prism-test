import json

import pytest

from reprise import demo_server


@pytest.mark.asyncio
async def test_activity_feed_preserves_real_events_and_partial_append(tmp_path, monkeypatch):
    room = 'reprise-demo-1234567890abcd'
    monkeypatch.setattr(demo_server, 'ROOT', tmp_path)
    monkeypatch.setattr(demo_server, 'issued', {room})
    directory = tmp_path / 'runs' / room
    directory.mkdir(parents=True)
    path = directory / 'events.jsonl'
    kinds = ['transcript_received', 'tool_raw_proposed', 'tool_dispatch',
             'approval_revoked', 'external_api_completed', 'tool_outcome_unknown', 'workspace_items']
    records = [{'event': kind, 'event_id': str(index)} for index, kind in enumerate(kinds)]
    path.write_text(''.join(json.dumps(record) + '\n' for record in records) + '{"event":')
    result = await demo_server.events(room, 0)
    assert result == {'events': records, 'cursor': len(records)}
    path.write_text(path.read_text() + '"session_closed"}\n')
    assert (await demo_server.events(room, result['cursor']))['events'] == [{'event': 'session_closed'}]


@pytest.mark.asyncio
async def test_private_diagnostics_not_exposed(tmp_path, monkeypatch):
    room = 'reprise-demo-1234567890abcd'
    monkeypatch.setattr(demo_server, 'ROOT', tmp_path)
    monkeypatch.setattr(demo_server, 'issued', {room})
    path = tmp_path / 'runs' / room / 'events.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text('{"event":"session_started","settings":{"private":"value"}}\n')
    assert (await demo_server.events(room, 0)) == {'events': [], 'cursor': 1}


def test_gemini_status_requires_confirmed_provider_connection(tmp_path):
    from types import SimpleNamespace
    from reprise.session_events import attach_session_events
    from reprise.trace import Trace

    callbacks = {}
    class Session:
        def on(self, name):
            def register(fn):
                callbacks[name] = fn
                return fn
            return register
    trace = Trace('room', tmp_path)
    attach_session_events(Session(), SimpleNamespace(trace=trace))
    callbacks['metrics_collected'](SimpleNamespace(metrics={
        'type': 'realtime_model_metrics', 'request_id': '',
        'connection_reused': False, 'acquire_time': 0.12,
    }))
    callbacks['error'](SimpleNamespace(error=SimpleNamespace(
        type='realtime_model_error', recoverable=True)))
    # A late per-response metric is not a new connection handshake.
    callbacks['metrics_collected'](SimpleNamespace(metrics={
        'type': 'realtime_model_metrics', 'request_id': 'response-1',
        'connection_reused': False, 'acquire_time': 0.0,
    }))
    callbacks['close'](SimpleNamespace())
    statuses = [json.loads(line)['state'] for line in trace.path.read_text().splitlines()
                if json.loads(line)['event'] == 'gemini_connection']
    assert statuses == ['connected', 'reconnecting', 'disconnected']
