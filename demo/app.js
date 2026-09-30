const {Room, RoomEvent, Track} = LivekitClient;
const $ = id => document.getElementById(id);
let currentPlan = null, googleConnected = false, planningTimeZone = 'Asia/Kolkata';
let room, pollTimer, clockTimer, startedAt = 0, cursor = 0, activeRoom = null, actionCount = 0;

function showStatus(label, connected = false, speaking = false) {
  $('status').textContent = label;
  $('dot').classList.toggle('on', connected);
  document.body.classList.toggle('connected', connected);
  document.body.classList.toggle('speaking', speaking);
  $('talkState').textContent = connected ? (speaking ? 'Speaking' : label) : 'Waiting';
}
function clockTick() {
  const seconds = Math.floor((Date.now() - startedAt) / 1000);
  $('timer').textContent = String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
}
function message(role, value) {
  const text = String(value || '').trim();
  if (!text || (role !== 'user' && role !== 'assistant')) return;
  document.querySelector('.empty')?.remove();
  const item = document.createElement('div');
  item.className = 'message ' + role;
  const who = document.createElement('div');
  who.className = 'speaker';
  who.textContent = role === 'user' ? 'You' : 'Reprise';
  const body = document.createElement('p');
  body.textContent = text;
  item.append(who, body);
  $('messages').append(item);
  $('messages').scrollTop = $('messages').scrollHeight;
}
function action(label, kind = 'tool') {
  const list = $('trace');
  list.querySelector('.quiet')?.remove();
  const item = document.createElement('li');
  const time = document.createElement('time');
  time.textContent = $('timer').textContent;
  const text = document.createElement('span');
  text.textContent = label;
  item.append(time, text);
  if (kind === 'tool') {
    actionCount += 1;
    $('actionCount').textContent = actionCount + (actionCount === 1 ? ' action' : ' actions');
  }
  list.append(item);
  list.scrollTop = list.scrollHeight;
}
function renderEvent(event) {
  if (event.event === 'plan_updated') renderPlan(event.plan);
  if (event.event === 'conversation_item') message(event.role, event.text);
  if (event.event === 'agent_state') {
    const state = event.state;
    showStatus(state === 'speaking' ? 'Reprise is speaking' : state === 'thinking' ? ($('mode').value === 'productivity' ? 'Planning' : 'Checking the source') : 'Listening', true, state === 'speaking');
    $('step').textContent = state === 'thinking' ? '02 / Checking' : state === 'speaking' ? '03 / Responding' : '01 / Listening';
  }
  if (event.event === 'tool_executed') {
    action((event.function || 'Tool') + ' · ' + (event.outcome || 'completed'));
    $('step').textContent = '02 / Source checked';
  }
  if (event.event === 'tool_superseded') action('Changed request · previous action stopped', 'change');
  if (event.event === 'tool_rejected') action('Action needs a clearer detail', 'change');
  if (event.event === 'session_error') action('Voice session reported an error', 'change');
  if (event.event === 'manual_found') {
    const box = $('sourceText');
    const heading = document.createElement('strong');
    heading.textContent = (event.code || 'Code') + ' · ' + (event.meaning || 'Support guidance');
    const para = document.createElement('p');
    const link = document.createElement('a');
    link.href = event.source_url || '#';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = event.source_title || 'Open source';
    para.append('Checked against ', link, '.');
    box.replaceChildren(heading, para);
  }
}
async function poll() {
  if (!activeRoom) return;
  try {
    const response = await fetch('/events/' + encodeURIComponent(activeRoom) + '?after=' + cursor, {cache: 'no-store'});
    if (!response.ok) throw new Error('Trace unavailable');
    const data = await response.json();
    data.events.forEach(renderEvent);
    cursor = data.cursor;
  } catch {
    showStatus('Trace reconnecting', true);
  }
}
async function closeSession() {
  clearInterval(pollTimer);
  clearInterval(clockTimer);
  activeRoom = null;
  if (room) await room.disconnect();
  document.querySelectorAll('[data-agent-audio]').forEach(el => el.remove());
  ['end', 'text', 'send'].forEach(id => $(id).disabled = true);
  $('start').disabled = false;
  $('mode').disabled = false;
  $('confirmPlan').disabled = true;
  $('step').textContent = '01 / Ready to listen';
  showStatus('Conversation ended');
}
$('start').onclick = async () => {
  $('start').disabled = true;
  showStatus('Connecting');
  try {
    const response = await fetch('/session?mode=' + encodeURIComponent($('mode').value), {method: 'POST'});
    if (!response.ok) throw new Error('Could not start a session');
    const data = await response.json();
    room = new Room({adaptiveStream: true, dynacast: true});
    room.on(RoomEvent.TrackSubscribed, track => {
      if (track.kind === Track.Kind.Audio) {
        const element = track.attach();
        element.dataset.agentAudio = 'true';
        document.body.append(element);
      }
    });
    room.on(RoomEvent.Disconnected, () => {
      if (activeRoom) closeSession();
    });
    await room.connect(data.url, data.token);
    await room.startAudio();
    await room.localParticipant.setMicrophoneEnabled(true);
    activeRoom = data.room;
    currentPlan = null;
    $('planCard').hidden = true;
    $('mode').disabled = true;
    cursor = 0;
    actionCount = 0;
    $('trace').replaceChildren();
    $('actionCount').textContent = '0 actions';
    action('Voice room connected', 'change');
    startedAt = Date.now();
    clockTimer = setInterval(clockTick, 1000);
    clockTick();
    pollTimer = setInterval(poll, 600);
    showStatus('Listening', true);
    $('step').textContent = '01 / Listening';
    ['end', 'text', 'send'].forEach(id => $(id).disabled = false);
  } catch (error) {
    showStatus(error.message || 'Connection failed');
    $('start').disabled = false;
    if (room) await room.disconnect();
  }
};
$('end').onclick = closeSession;
$('chat').onsubmit = async event => {
  event.preventDefault();
  const text = $('text').value.trim();
  if (!text || !room) return;
  try {
    await room.localParticipant.sendText(text, {topic: 'lk.chat'});
    $('text').value = '';
  } catch {
    showStatus('Message could not be sent', true);
  }
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
  $('planStatus').textContent = plan.status === 'prepared' ? 'Review & confirm' : plan.status;
  $('planActions').replaceChildren();
  for (const item of plan.actions) {
    const row = document.createElement('li'); row.className = 'plan-item ' + item.status;
    const label = document.createElement('span'); label.className = 'plan-kind';
    label.textContent = item.kind === 'email' ? (item.delivery === 'send' ? 'Send email' : 'Save email draft') : item.kind;
    const state = document.createElement('span'); state.className = 'plan-state'; state.textContent = item.status;
    const title = document.createElement('strong'); title.textContent = item.title || item.subject || [item.quantity, item.item].filter(Boolean).join(' ');
    const detail = document.createElement('p');
    detail.textContent = item.kind === 'calendar' ? when(item.start) + ' — ' + when(item.end) : item.kind === 'email' ? 'To: ' + item.to.join(', ') + '\n' + item.body : [item.due_date ? 'Due ' + item.due_date : '', item.notes || ''].filter(Boolean).join('\n');
    row.append(label, state, title, detail);
    if (item.google_id) {const proof = document.createElement('p'); proof.textContent = 'Google confirmed · ' + [item.delivery_result, item.google_id].filter(Boolean).join(' · '); row.append(proof);}
    if (item.url && /^https:\/\//.test(item.url)) {const link = document.createElement('a'); link.href = item.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'Open in Google ↗'; row.append(link);}
    if (item.error) {const error = document.createElement('p'); error.textContent = item.error; row.append(error);}
    $('planActions').append(row);
  }
  $('confirmPlan').disabled = !googleConnected || !activeRoom || plan.status !== 'prepared';
  $('planNote').textContent = plan.status === 'completed' ? 'Every action above was confirmed by Google.' : plan.status === 'partial' ? 'Some actions failed or have an unknown outcome. Check the result before repeating an action.' : !googleConnected ? 'Connect Google to execute this plan.' : 'Review the details. Say “confirm the plan” or press Confirm plan to make these changes.';
}
async function checkGoogle() {
  try {
    const response = await fetch('/google/status', {cache: 'no-store'});
    if (!response.ok) throw new Error();
    const value = await response.json(); googleConnected = value.connected; planningTimeZone = value.time_zone;
    $('googleStatus').textContent = value.connected ? 'Google connected · live APIs' : value.configured ? 'Google not connected' : 'Google OAuth setup needed';
    $('connectGoogle').textContent = value.connected ? 'Reconnect Google ↗' : 'Connect Google ↗';
    if (currentPlan) renderPlan(currentPlan);
  } catch {$('googleStatus').textContent = 'Google connection unavailable'; googleConnected = false;}
}
$('confirmPlan').onclick = async () => {
  if (!currentPlan || !activeRoom || !room) return;
  $('confirmPlan').disabled = true;
  try {
    const response = await fetch('/plans/' + encodeURIComponent(activeRoom) + '/' + encodeURIComponent(currentPlan.plan_id) + '/approve', {method: 'POST'});
    if (!response.ok) throw new Error('The plan changed. Review the current plan again.');
    await room.localParticipant.sendText('Confirm the plan.', {topic: 'lk.chat'});
    $('planNote').textContent = 'Plan approved. Waiting for the assistant to execute the Google actions.';
  } catch (error) {showStatus(error.message || 'Confirmation failed', true); if (currentPlan) renderPlan(currentPlan);}
};
$('mode').onchange = () => {
  const productivity = $('mode').value === 'productivity';
  document.body.dataset.mode = $('mode').value;
  $('heroTitle').textContent = productivity ? 'A messy request. A completed plan.' : 'Help that keeps up when you change your mind.';
  $('heroDescription').textContent = productivity ? 'Calendar, tasks, email and shopping using your real Google account.' : 'A live voice session that listens through corrections and shows each action.';
  $('sourceText').textContent = productivity ? 'Calendar · Tasks · Gmail. Real Google actions appear in the plan.' : $('mode').value === 'extension' ? 'Waiting for a washer-code lookup against Samsung guidance.' : 'Public benchmark tools use a simulated environment.';
  $('scenarioTitle').textContent = productivity ? '“Tomorrow at four… actually, make it five.”' : $('mode').value === 'extension' ? '“It says 4C… wait, I mean 5C.”' : 'Try a corrected travel or shopping request.';
  $('scenarioDescription').textContent = productivity ? 'Add tasks, an email and shopping items in one request. Review, then confirm the plan.' : $('mode').value === 'extension' ? 'Say a washer code and correct yourself. The assistant checks Samsung guidance.' : 'This mode uses the public benchmark’s simulated tools.';
  $('sourceHeading').textContent = productivity ? 'Connected services' : 'Source check';
  $('sourceLabel').textContent = productivity ? 'Google Workspace' : 'Support guidance';
};
checkGoogle();
setInterval(checkGoogle, 5000);
