const {Room, RoomEvent, Track} = LivekitClient;
const $ = id => document.getElementById(id);
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
  if (event.event === 'conversation_item') message(event.role, event.text);
  if (event.event === 'agent_state') {
    const state = event.state;
    showStatus(state === 'speaking' ? 'Reprise is speaking' : state === 'thinking' ? 'Checking the source' : 'Listening', true, state === 'speaking');
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
  $('step').textContent = '01 / Ready to listen';
  showStatus('Conversation ended');
}
$('start').onclick = async () => {
  $('start').disabled = true;
  showStatus('Connecting');
  try {
    const response = await fetch('/session', {method: 'POST'});
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
