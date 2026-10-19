import base64
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from reprise.google_workspace import GoogleWorkspace, WorkspaceConfig
from reprise.trace import safe_payload

READ = 'https://www.googleapis.com/auth/gmail.readonly'


def google(tmp_path, handler, scope=READ):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    workspace = GoogleWorkspace(WorkspaceConfig(client_id='client', client_secret='secret', token_file=tmp_path/'token.json'), client)
    workspace.save_tokens({'access_token':'test','refresh_token':'test','expires_at':time.time()+3600,'scope':scope})
    return workspace, client


@pytest.mark.asyncio
async def test_search_uses_native_query_and_only_returns_bounded_metadata(tmp_path):
    calls = []
    def handle(request):
        calls.append(request)
        assert request.method == 'GET'
        if request.url.path.endswith('/messages'):
            assert request.url.params['q'] == 'IndiGo Chennai'
            assert request.url.params['maxResults'] == '5'
            return httpx.Response(200,json={'messages':[{'id':'ab12'}]})
        assert request.url.params['format'] == 'metadata'
        return httpx.Response(200,json={'id':'ab12','snippet':'private snippet', 'payload':{'headers':[{'name':'Subject','value':'Booking'}]}})
    w,c=google(tmp_path,handle)
    result=await w.search_messages('IndiGo Chennai')
    assert result['messages'][0]['subject'] == 'Booking'
    assert 'private snippet' not in str(result)
    assert len(calls)==2
    await c.aclose()


@pytest.mark.asyncio
async def test_lookup_returns_only_actual_body_with_source_no_invented_booking(tmp_path):
    body='Your trip is confirmed. No flight details supplied.'
    def handle(request):
        assert request.url.path.endswith('/messages/ab12')
        assert request.url.params['format']=='full'
        return httpx.Response(200,json={'id':'ab12','payload':{'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(body.encode()).decode()}}})
    w,c=google(tmp_path,handle)
    result=await w.get_message('ab12')
    assert result['mail_body']==body
    assert result['source']['message_id']=='ab12'
    assert 'pnr' not in result
    assert body not in str(safe_payload(result))
    await c.aclose()


@pytest.mark.asyncio
async def test_missing_scope_requires_reconnect_without_reading_mail(tmp_path):
    def handle(_):
        pytest.fail('No mailbox request before read consent')
    w,c=google(tmp_path,handle,scope='https://www.googleapis.com/auth/gmail.compose')
    assert w.status()['gmail_read_requires_reauthorization'] is True
    with pytest.raises(ValueError,match='Reconnect'):
        await w.search_messages('booking')
    assert READ in parse_qs(urlparse(w.authorization_url('state','challenge')).query)['scope'][0]
    await c.aclose()


@pytest.mark.asyncio
async def test_empty_search_is_explicit_and_invalid_inputs_do_not_dispatch(tmp_path):
    w,c=google(tmp_path,lambda _:httpx.Response(200,json={}))
    assert (await w.search_messages('not-found'))['messages']==[]
    with pytest.raises(ValueError):
        await w.get_message('../other-user')
    with pytest.raises(ValueError):
        await w.search_messages('')
    await c.aclose()


def test_mime_prefers_plain_and_skips_attachments_and_scripts():
    from reprise.gmail_content import message_text
    def part(kind, text, **extra):
        return {'mimeType':kind,'body':{'data':base64.urlsafe_b64encode(text.encode()).decode()},**extra}
    assert message_text({'mimeType':'multipart/alternative','parts':[
        part('text/plain','PNR: ABC123'),part('text/html','<p>Different alternative</p>')]}) == 'PNR: ABC123'
    text=message_text({'mimeType':'multipart/mixed','parts':[
        part('text/html','<p>Flight: ZZ123</p><script>bad()</script>'),
        part('text/plain','secret attachment',filename='booking.txt')]})
    assert 'Flight: ZZ123' in text and 'bad()' not in text and 'secret' not in text
    assert message_text(part('text/plain','x'*15000)) == 'x'*12000


@pytest.mark.asyncio
async def test_api_error_is_failure_not_empty_search(tmp_path):
    from reprise.google_workspace import GoogleAPIError
    w,c=google(tmp_path,lambda _:httpx.Response(403,json={'error':{'status':'PERMISSION_DENIED'}}))
    with pytest.raises(GoogleAPIError):
        await w.search_messages('booking')
    await c.aclose()


def test_env_fallback_preserves_local_and_process_precedence(tmp_path, monkeypatch):
    from reprise import config
    monkeypatch.setattr(config,'ROOT',tmp_path)
    monkeypatch.delenv('GEMINI_MODEL',raising=False)
    (tmp_path/'.env').write_text('GEMINI_MODEL=fallback\n')
    assert config.Settings.from_env().model=='fallback'
    monkeypatch.delenv('GEMINI_MODEL')
    (tmp_path/'.env.local').write_text('GEMINI_MODEL=local\n')
    assert config.Settings.from_env().model=='local'
    monkeypatch.setenv('GEMINI_MODEL','process')
    assert config.Settings.from_env().model=='process'


@pytest.mark.asyncio
async def test_read_tools_integrate_without_approval_or_mutation(tmp_path):
    from reprise.productivity import ProductivityBackend, PlanStore, PRODUCTIVITY_TOOLS
    w,c=google(tmp_path,lambda request:httpx.Response(200,json={}))
    backend=ProductivityBackend('test',google=w,store=PlanStore(tmp_path/'plans.sqlite3'))
    assert (await backend.call('search_gmail',{'query':'booking'}))['messages']==[]
    read_specs=[s for s in PRODUCTIVITY_TOOLS if s.name in {'search_gmail','get_gmail_message'}]
    assert len(read_specs)==2 and all(not s.writes and not s.cache_result for s in read_specs)
    await c.aclose()


def test_booking_fields_require_explicit_labels_and_keep_literal_evidence():
    from reprise.gmail_content import booking_fields
    text='PNR: LMN456\nFlight number: 6E 987\nDeparture date: 22 October 2026\nDeparture time: 09:45 IST\n'
    fields=booking_fields(text)
    assert fields['pnr']=={'value':'LMN456','evidence':'PNR: LMN456'}
    assert fields['departure_time']['value']=='09:45 IST'
    assert booking_fields('Booking confirmed; details will follow.')=={}
    assert 'pnr' not in booking_fields('PNR: OLD111\nPNR: NEW222')
