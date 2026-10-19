/* Presentation only: controller and API events are the source of action state. */
function actionLabel(action) {
  const states = {completed:'Completed',retrieved:'Retrieved',deleted:'Deleted',updated:'Updated',undone:'Undone',superseded:'Superseded',unknown:'Outcome unknown',
    approval_required:'Confirmation required',prepared:'Prepared',pending:'Pending',running:'Running',failed:'Failed',not_executed:'Not executed',partial:'Partial',needs_review:'Outcome unknown'};
  return states[action.resource_result || action.result || action.status] || action.status;
}
function workspaceText(item) {
  const text = [(item.number ? item.number + '. ' : '') + (item.title || '(untitled)'), 'ID: ' + item.id];
  if (item.kind !== 'task_list' && item.container_id) text.push('List/calendar: ' + item.container_id);
  if (item.task_status) text.push('Task status: ' + ({completed:'Complete',needsAction:'Incomplete'}[item.task_status] || item.task_status));
  if (item.due_date) text.push('Due: ' + item.due_date);
  if (item.start) text.push('Start: ' + item.start.replace('T',' '), 'End: ' + item.end.replace('T',' ') + (item.start.length === 10 ? ' (exclusive)' : ''));
  if (item.location) text.push('Location: ' + item.location);
  if (item.attendees?.length) text.push('Guests: ' + item.attendees.join(', '));
  if (item.reminders) text.push('Calendar reminders: ' + (item.reminders.useDefault ? 'Calendar defaults' : item.reminders.overrides?.length ? item.reminders.overrides.map(r=>r.method + ' ' + r.minutes + ' min before').join(', ') : 'None'));
  if (item.notes) text.push(item.notes + (item.notes_truncated ? ' [truncated]' : ''));
  return text.join('\n');
}
function describeEvent(event) {
  if (event.event === 'workspace_items') {
    const title = {task:'Tasks',calendar:'Calendar events',task_list:'Task lists'}[event.resource_kind || event.kind] || 'Retrieved items';
    const items = event.items || [];
    return {title,text:(items.length ? items.map(workspaceText).join('\n\n') : 'No matching items in this page.') + (event.more_available ? '\nMore available' : ''),details:event};
  }
  const labels = {
    tool_raw_proposed:'Action proposed', tool_proposed:'Action ready', tool_dispatch:'Running',
    tool_superseded:'Action superseded', tool_rejected:'Action rejected',
    tool_validation_failed:'Action rejected', tool_outcome_unknown:'Unknown outcome',
    tool_caller_detached:'Response interrupted · action outcome pending',
    stale_result_suppressed:'Earlier result withheld', duplicate_coalesced:'Duplicate combined',
    intent_revised:'Request corrected', approval_revoked:'Approval revoked', approval_checked:'Approval checked',
    external_api_started:'Google request started', external_api_completed:'Google response',
    session_error:'Session error', speech_started:'User speaking', speech_ended:'User stopped speaking',
    agent_ready:'Assistant ready', session_closed:'Assistant disconnected', plan_updated:'Plan updated',
    agent_state:'Assistant state', transcript_received:'You · interim'
  };
  const outcomes = {success:'Succeeded',error:'Failed',unknown:'Unknown outcome',approval_required:'Approval required',
    prepared:'Prepared',superseded:'Superseded',cancelled:'Cancelled',rejected:'Rejected',partial:'Partially completed'};
  if (event.event === 'conversation_item') {
    if (!['user','assistant'].includes(event.role) || !event.text) return null;
    return {title:(event.role === 'user' ? 'You' : 'Reprise') + (event.interrupted ? ' · interrupted' : ''), text:event.text, role:event.role};
  }
  if (event.event === 'transcript_received' && event.is_final) return null;
  let title = labels[event.event];
  if (event.event === 'tool_executed') {
    title = outcomes[event.outcome] || event.outcome || 'Outcome not reported';
    if (event.outcome === 'success' && ['prepare_plan','prepare_change'].includes(event.function)) title = 'Prepared';
    if (event.outcome === 'success' && event.function === 'execute_plan') title = 'Completed';
  }
  if (!title) return null;
  if (event.function) title += ' · ' + event.function.replaceAll('_',' ');
  if (event.event === 'external_api_completed') title += ' · ' + (outcomes[event.outcome] || event.outcome || 'Outcome not reported');
  const details = Object.fromEntries(Object.entries(event).filter(([key]) => !['event','wall_time','monotonic_ns','elapsed_s','schema_version','room','text'].includes(key)));
  const readable = value => typeof value === 'object' ? JSON.stringify(value) : String(value);
  const facts = [(event.event === 'intent_revised' ? '' : event.text) || event.reason || event.error_type || (event.event === 'agent_state' ? event.state : '') || (event.plan ? event.plan.title + ' · ' + event.plan.status : '')];
  if (event.event === 'plan_updated' && event.plan?.actions) facts.push(...event.plan.actions.map(a => actionLabel(a) + ' · ' + (a.title || a.subject || a.item || a.kind) + (a.google_id ? ' · ' + a.google_id : '')));
  if (event.args) facts.push(...Object.entries(event.args).map(([key,value]) => key.replaceAll('_',' ') + ': ' + readable(value)));
  if (event.google_id) facts.push('Google receipt: ' + event.google_id);
  if (event.status_code) facts.push('HTTP ' + event.status_code);
  return {title, text:facts.filter(Boolean).join('\n'), details};
}
if (typeof module !== 'undefined') module.exports = {describeEvent};
if (typeof document !== 'undefined') initialize();

function initialize() {
const $ = id => document.getElementById(id);
const {Room, RoomEvent, Track} = LivekitClient;
let currentPlan = null, googleConnected = false, planningTimeZone = 'Asia/Kolkata';
let room, pollTimer, clockTimer, startedAt = 0, cursor = 0, activeRoom = null;
let generation = 0, polling = false, muted = false, agentClosed = false, closing = false;
const seen = new Set();
let agentReady = false;
const liveSegments = new Map(), committed = {user:'',assistant:''};
function service(id,state) {
  $(id).textContent = (id === 'livekitStatus' ? 'LiveKit' : 'Gemini') + ' · ' + state;
  $(id).setAttribute('data-state',state);
}
function liveText(role,text) {
  $(role === 'user' ? 'partial' : 'agentPartial').textContent = text ? (role === 'user' ? 'You' : 'Reprise') + ' · ' + text : '';
  $(role === 'user' ? 'userLiveRow' : 'agentLiveRow').hidden = !text;
  $(role === 'user' ? 'userLiveTime' : 'agentLiveTime').textContent = text ? new Date().toLocaleTimeString() : '';
}
function showStatus(label) { $('status').textContent = label; }
function notice(text) { $('notice').textContent = text; }
function clockTick() {
  const seconds = Math.floor((Date.now() - startedAt) / 1000);
  if (activeRoom && !agentReady && seconds >= 15) { showStatus('Waiting for assistant'); notice('No voice agent has joined yet. Check that the Reprise agent worker is running.'); }
  $('timer').textContent = String(Math.floor(seconds / 60)).padStart(2,'0') + ':' + String(seconds % 60).padStart(2,'0');
}
function append(entry, event = {}) {
  if (!entry) return;
  const list = $(entry.role ? 'conversation' : 'activity'), nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 70;
  list.querySelector('.empty')?.remove();
  const item = document.createElement('li'); item.className = 'entry ' + (entry.role || '');
  const heading = document.createElement('header'), label = document.createElement('strong'), time = document.createElement('time');
  label.textContent = entry.title;
  const date = new Date(event.wall_time ? event.wall_time * 1000 : Date.now());
  time.dateTime = date.toISOString(); time.textContent = date.toLocaleTimeString();
  heading.append(label,time); item.append(heading);
  if (entry.text) { const p = document.createElement('p'); p.textContent = entry.text; item.append(p); }
  if (entry.details) {
    const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
    summary.textContent = 'Details'; pre.textContent = JSON.stringify(entry.details,null,2); details.append(summary,pre); item.append(details);
  }
  list.append(item); if (nearBottom) list.scrollTop = list.scrollHeight;
}
function renderEvent(event) {
  if (event.event_id && seen.has(event.event_id)) return;
  if (event.event_id) seen.add(event.event_id);
  if (event.event === 'transcript_received') { if (!event.is_final) liveText('user',event.text); return; }
  if (event.event === 'conversation_item' && ['user','assistant'].includes(event.role)) {
    committed[event.role] = event.text; liveText(event.role,'');
  }
  if (event.event === 'agent_ready') { agentReady = true; agentClosed = false; if ($('geminiStatus').textContent.endsWith('waiting for agent')) service('geminiStatus','connecting'); if ($('notice').textContent.startsWith('No voice agent')) notice(''); }
  if (event.event === 'gemini_connection') { service('geminiStatus',event.state); }
  if (event.event === 'session_error') notice('Assistant error: ' + event.error_type);

  if (event.event === 'plan_updated') renderPlan(event.plan);
  if (event.event === 'approval_revoked' && currentPlan?.plan_id === event.plan_id) {
    renderPlan({...currentPlan, status:event.status || 'needs_review'});
  }
  if (event.event === 'agent_state') showStatus(({thinking:'Processing',speaking:'Speaking',listening:'Listening',initializing:'Connecting',idle:'Idle'})[event.state] || event.state);
  if (event.event === 'speech_started') showStatus('User speaking');
  if (event.event === 'conversation_item' && event.interrupted) showStatus('Interrupted');
  if (event.event === 'session_closed') {agentClosed = true; service('geminiStatus','disconnected'); showStatus('Assistant disconnected'); $('confirmPlan').disabled = true;}
  if (!['agent_state','speech_started','speech_ended','gemini_connection'].includes(event.event)) append(describeEvent(event),event);
}
async function poll() {
  if (!activeRoom || polling) return;
  const target = activeRoom, epoch = generation; polling = true;
  try {
    const response = await fetch('/events/' + encodeURIComponent(target) + '?after=' + cursor, {cache:'no-store',signal:AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error('Activity unavailable');
    const data = await response.json();
    if (epoch !== generation || target !== activeRoom) return;
    data.events.forEach(renderEvent); cursor = data.cursor;
    if ($('notice').textContent === 'Activity connection interrupted; retrying.') notice('');
  } catch { if (epoch === generation) notice('Activity connection interrupted; retrying.'); }
  finally { if (epoch === generation) polling = false; }
}
async function closeSession() {
  if (closing) return; closing = true;
  // Fetch the last available events before closing; never infer pending outcomes.
  await poll();
  ++generation; clearInterval(pollTimer); clearInterval(clockTimer);
  activeRoom = null; polling = false;
  const previous = room; room = null;
  if (previous) await previous.disconnect();
  document.querySelectorAll('[data-agent-audio]').forEach(el => el.remove());
  ['end','mute','text','send','confirmPlan'].forEach(id => $(id).disabled = true);
  $('start').disabled = false; $('mode').disabled = false; liveText('user','');
  service('livekitStatus','disconnected'); service('geminiStatus','disconnected'); liveText('assistant',''); liveSegments.clear();
  showStatus('Disconnected'); append({title:'Session ended'}); closing = false;
}
$('start').onclick = async () => {
  $('start').disabled = true; notice(''); showStatus('Connecting'); service('livekitStatus','connecting'); service('geminiStatus','waiting for agent');
  const epoch = ++generation;
  try {
    const response = await fetch('/session?mode=' + encodeURIComponent($('mode').value), {method:'POST'});
    if (!response.ok) throw new Error('Could not start a session (' + response.status + ')');
    const data = await response.json();
    // Configure peer connections after the server join response. The early-offer
    // path is rejected by some embedded Chromium WebRTC implementations.
    room = new Room({adaptiveStream:true,dynacast:true,singlePeerConnection:false});
    room.registerTextStreamHandler('lk.transcription', async (reader, participant) => {
      const role = participant.identity === room?.localParticipant.identity ? 'user' : 'assistant';
      const id = reader.info.attributes?.['lk.segment_id'] || reader.info.id;
      // User streams contain a full interim revision; assistant streams contain deltas.
      let text = role === 'user' ? '' : (liveSegments.get(id) || '');
      try {
        for await (const chunk of reader) {
          if (epoch !== generation) return;
          text += chunk; liveSegments.set(id,text);
          if (text !== committed[role]) liveText(role,text);
        }
      } catch { if (epoch === generation) notice('Live transcript interrupted.'); }
      finally { if (reader.info.attributes?.['lk.transcription_final'] === 'true') liveSegments.delete(id); }
    });
    room.on(RoomEvent.ConnectionStateChanged, state => {
      if (epoch === generation) service('livekitStatus',state);
    });
    room.on(RoomEvent.ParticipantDisconnected, participant => {
      if (epoch === generation && participant.isAgent) {
        agentClosed = true; service('geminiStatus','disconnected');
        showStatus('Assistant disconnected'); $('confirmPlan').disabled = true;
      }
    });
    room.on(RoomEvent.TrackSubscribed, track => {
      if (track.kind === Track.Kind.Audio) { const el = track.attach(); el.dataset.agentAudio='true'; document.body.append(el); }
    });
    room.on(RoomEvent.Disconnected, () => { if (epoch === generation && activeRoom) closeSession(); });
    await room.connect(data.url,data.token); await room.startAudio();
    await room.localParticipant.setMicrophoneEnabled(true);
    service('livekitStatus','connected');
    activeRoom = data.room; cursor = 0; polling = false; muted = false; agentClosed = false; agentReady = false; liveSegments.clear(); committed.user=''; committed.assistant=''; seen.clear(); currentPlan = null;
    $('planCard').hidden = true; $('confirmPlan').disabled = true; $('mode').disabled = true;
    $('mute').textContent = 'Mute microphone'; $('mute').setAttribute('aria-pressed','false');
    startedAt=Date.now(); clockTick(); clockTimer=setInterval(clockTick,1000); pollTimer=setInterval(poll,600);
    append({title:'Session connected',text:$('mode').value === 'productivity' ? 'Google Workspace' : 'Simulated tools'});
    showStatus('Connected'); ['end','mute','text','send'].forEach(id => $(id).disabled=false); await poll();
  } catch (error) { notice(error.message || 'Connection failed'); await closeSession(); }
};
$('end').onclick = closeSession;
$('mute').onclick = async () => {
  const target = room; if (!target) return; $('mute').disabled = true;
  try { await target.localParticipant.setMicrophoneEnabled(muted); muted = !muted;
    $('mute').textContent = muted ? 'Unmute microphone' : 'Mute microphone'; $('mute').setAttribute('aria-pressed',String(muted));
    append({title:muted ? 'Microphone muted' : 'Microphone enabled'});
  } catch {notice('Microphone could not be changed.');}
  finally {$('mute').disabled = !activeRoom;}
};
$('chat').onsubmit = async event => {
  event.preventDefault(); const text = $('text').value.trim(); if (!text || !room) return;
  $('send').disabled = true;
  try {await room.localParticipant.sendText(text,{topic:'lk.chat'}); $('text').value='';}
  catch {notice('Message could not be sent.');} finally {$('send').disabled=!activeRoom;}
};
window.addEventListener('beforeunload', () => room?.disconnect());
function when(value) {
  return new Intl.DateTimeFormat(undefined, {dateStyle: 'medium', timeStyle: 'short', timeZone: planningTimeZone}).format(new Date(value));
}
function renderPlan(plan) {
  if (!plan) return;
  currentPlan = plan;
  $('planCard').hidden = false;
  $('planTitle').textContent = plan.title;
  $('planStatus').textContent = plan.approved && plan.status === 'prepared' ? 'Confirmed' : plan.requires_confirmation && plan.status === 'prepared' ? 'Confirmation required' : actionLabel({status:plan.status});
  $('planActions').replaceChildren();
  for (const saved of plan.actions) {
    const item = {...saved,...saved.current_values};
    const row = document.createElement('li'); row.className = 'plan-item ' + item.status;
    const label = document.createElement('span'); label.className = 'plan-kind';
    label.textContent = item.kind === 'email' ? (item.delivery === 'send' ? 'Send email' : 'Save email draft') : (item.operation === 'delete' ? 'Delete ' : item.operation === 'update' ? 'Update ' : '') + item.kind;
    const state = document.createElement('span'); state.className = 'plan-state'; state.textContent = actionLabel(item);
    const title = document.createElement('strong'); title.textContent = item.title || item.subject || [item.quantity, item.item].filter(Boolean).join(' ');
    const detail = document.createElement('p');
    detail.textContent = item.kind === 'calendar' ? when(item.start) + ' — ' + when(item.end) + (item.attendees?.length ? '\nGuests: ' + item.attendees.join(', ') : '') : item.kind === 'email' ? 'To: ' + item.to.join(', ') + '\n' + item.body : [item.due_date ? 'Due ' + item.due_date : '', item.notes || ''].filter(Boolean).join('\n');
    row.append(label, state, title, detail);
    if (item.task_status) {const status = document.createElement('p'); status.textContent = 'Task status: ' + (item.task_status === 'completed' ? 'Complete' : 'Incomplete'); row.append(status);}
    if (item.reminders) {const reminder = document.createElement('p'); reminder.textContent = 'Calendar reminders: ' + (item.reminders.useDefault ? 'Calendar defaults' : (item.reminders.overrides || []).map(r=>r.method + ' ' + r.minutes + ' min before').join(', ') || 'None'); row.append(reminder);}
    if (item.google_id) {const proof = document.createElement('p'); proof.textContent = 'Google confirmed · ' + [item.delivery_result, item.google_id].filter(Boolean).join(' · '); row.append(proof);}
    if (item.url && item.result !== 'deleted' && item.resource_result !== 'deleted' && item.result !== 'undone' && item.resource_result !== 'undone' && /^https:\/\//.test(item.url)) {const link = document.createElement('a'); link.href = item.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'Open in Google ↗'; row.append(link);}
    if (item.error) {const error = document.createElement('p'); error.textContent = item.error; row.append(error);}
    $('planActions').append(row);
  }
  $('confirmPlan').hidden = plan.requires_confirmation === false || plan.status !== 'prepared';
  $('confirmPlan').disabled = !googleConnected || !activeRoom || agentClosed || plan.status !== 'prepared' || plan.requires_confirmation === false;
  $('planNote').textContent = '';
}
async function checkGoogle() {
  try {
    const response = await fetch('/google/status', {cache: 'no-store', signal:AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error();
    const value = await response.json(); googleConnected = value.connected; planningTimeZone = value.time_zone;
    $('googleStatus').textContent = value.connected ? (value.gmail_read_requires_reauthorization ? 'Google connected · reconnect for Gmail search' : 'Google connected') : value.configured ? 'Google not connected' : 'Google OAuth setup needed';
    $('connectGoogle').textContent = value.connected ? 'Reconnect Google ↗' : 'Connect Google ↗';
    if (currentPlan) renderPlan(currentPlan);
  } catch {$('googleStatus').textContent = 'Google connection unavailable'; googleConnected = false; $('confirmPlan').disabled = true;}
}
$('confirmPlan').onclick = async () => {
  if (!currentPlan || !activeRoom || !room) return;
  $('confirmPlan').disabled = true;
  try {
    const response = await fetch('/plans/' + encodeURIComponent(activeRoom) + '/' + encodeURIComponent(currentPlan.plan_id) + '/approve', {method: 'POST'});
    if (!response.ok) throw new Error('The plan changed. Review the current plan again.');
    renderPlan((await response.json()).plan);
    await room.localParticipant.sendText('Confirm the plan.', {topic: 'lk.chat'});
    $('planNote').textContent = 'Confirmed';
  } catch (error) {notice(error.message || 'Confirmation failed'); if (currentPlan) renderPlan(currentPlan);}
};
checkGoogle();
setInterval(checkGoogle,5000);
}
