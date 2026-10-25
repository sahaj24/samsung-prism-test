/* Presentation only: controller and API events are the source of action state. */
function actionLabel(action) {
  const states = {cancelled:'Cancelled',rejected:'Rejected',completed:'Completed',retrieved:'Retrieved',deleted:'Deleted',updated:'Updated',undone:'Undone',superseded:'Superseded',unknown:'Outcome unknown',
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
    demo_action_paused:'Action paused', demo_navigation:'App navigation', demo_result:'Preview result', tool_raw_proposed:'Action proposed', tool_proposed:'Action ready', tool_dispatch:'Running',
    tool_superseded:'Action superseded', tool_rejected:'Action rejected',
    tool_validation_failed:'Action rejected', tool_outcome_unknown:'Unknown outcome',
    tool_caller_detached:'Response interrupted · action outcome pending',
    stale_result_suppressed:'Earlier result withheld', duplicate_coalesced:'Duplicate combined',
    intent_revised:'Request corrected', approval_revoked:'Approval revoked', approval_checked:'Approval checked',
    external_api_started:'Google request started', external_api_completed:'Google response',
    session_error:'Session error', speech_started:'User speaking', speech_ended:'User stopped speaking',
    agent_ready:'Assistant ready', session_closed:'Assistant disconnected', plan_updated:'Plan updated',
    agent_state:'Assistant state', transcript_received:'You · interim', local_model_metrics:'Local inference'
  };
  const outcomes = {success:'Succeeded',error:'Failed',unknown:'Unknown outcome',approval_required:'Approval required',
    prepared:'Prepared',superseded:'Superseded',cancelled:'Cancelled',rejected:'Rejected',partial:'Partially completed'};
  if (event.event === 'conversation_item') {
    if (!['user','assistant'].includes(event.role) || !event.text) return null;
    return {title:(event.simulated && event.speaker ? event.speaker : event.role === 'user' ? 'You' : 'Reprise') + (event.interrupted ? ' · interrupted' : ''), text:event.text, role:event.role};
  }
  if (event.event === 'transcript_received' && event.is_final) return null;
  let title = labels[event.event];
  if (event.event === 'tool_executed') {
    title = outcomes[event.outcome] || event.outcome || 'Outcome not reported';
    if (event.outcome === 'success' && ['prepare_plan','prepare_change','prepare_resource_change'].includes(event.function)) title = 'Prepared';
    if (event.outcome === 'success' && event.function === 'execute_plan') title = event.simulated?'Completed':'Execution returned';
  }
  if (!title) return null;
  if (event.function) title += ' · ' + event.function.replaceAll('_',' ');
  if (event.event === 'external_api_completed') title += ' · ' + (outcomes[event.outcome] || event.outcome || 'Outcome not reported');
  const details = Object.fromEntries(Object.entries(event).filter(([key]) => !['event','wall_time','monotonic_ns','elapsed_s','schema_version','room','text'].includes(key)));
  const readable = value => typeof value === 'object' ? JSON.stringify(value) : String(value);
  const facts = [(event.event === 'demo_navigation' ? event.app + ' · ' + event.step : '') || (event.event === 'intent_revised' ? '' : event.text) || event.reason || event.error_type || (event.event === 'agent_state' ? event.state : '') || (event.plan ? event.plan.title + ' · ' + event.plan.status : '')];
  if (event.event === 'plan_updated' && event.plan?.actions) facts.push(...event.plan.actions.map(a => actionLabel(a) + ' · ' + (a.title || a.subject || a.item || a.kind) + (a.google_id ? ' · ' + a.google_id : '')));
  if (event.args) facts.push(...Object.entries(event.args).map(([key,value]) => key.replaceAll('_',' ') + ': ' + readable(value)));
  if (event.event === 'local_model_metrics') facts.push('Inference: ' + Number(event.duration_s).toFixed(2) + ' s');
  if (event.google_id) facts.push('Google receipt: ' + event.google_id);
  if (event.status_code) facts.push('HTTP ' + event.status_code);
  return {title, text:facts.filter(Boolean).join('\n'), details};
}
// Styling state never changes the controller's operation or receipt.
function timelineState(event) {
  if (['tool_outcome_unknown'].includes(event.event) || event.outcome === 'unknown' || ['unknown','needs_review'].includes(event.plan?.status)) return 'unknown';
  if (['tool_rejected','tool_validation_failed','session_error'].includes(event.event) || event.outcome === 'error' || event.plan?.status === 'failed') return 'failed';
  if (event.event === 'tool_caller_detached' || event.event === 'demo_action_paused' || event.interrupted) return 'interrupted';
  if (['tool_superseded'].includes(event.event) || ['superseded','cancelled'].includes(event.outcome) || ['superseded','cancelled'].includes(event.plan?.status)) return 'cancelled';
  if (event.event === 'intent_revised') return 'corrected';
  if (['tool_dispatch','external_api_started'].includes(event.event) || event.plan?.status === 'running') return 'running';
  if (event.event === 'tool_executed' && event.outcome === 'success' && ['prepare_plan','prepare_change','prepare_resource_change'].includes(event.function)) return 'pending';
  if (!event.simulated && event.event === 'tool_executed' && event.outcome === 'success' && event.function === 'execute_plan') return 'info';
  if (event.outcome === 'success' || event.plan?.status === 'completed' || event.event === 'demo_result') return 'completed';
  if (['tool_raw_proposed','tool_proposed','approval_checked','approval_revoked'].includes(event.event) || ['approval_required','prepared'].includes(event.outcome) || event.plan?.status === 'prepared') return 'pending';
  return 'info';
}
function timelineCaption(event) {
  if(event.event==='demo_action_paused')return 'Paused';
  if(event.simulated && event.caption)return event.caption;
  if(event.proposal_id==='demo-coffee-five')return '5 PM · not saved';
  if(event.proposal_id==='demo-coffee-six')return event.outcome==='error'?'6 PM · Not saved':event.outcome==='unknown'?'6 PM · Outcome unknown':event.outcome==='success' ? '6 PM saved' + (event.calendar_write_count ? ' · '+event.calendar_write_count+' write'+(event.calendar_write_count===1?'':'s') : '') : event.event==='tool_dispatch'?'6 PM · saving':'6 PM · pending';
  if(event.proposal_id==='demo-free-five')return '5 PM · check';
  if(event.proposal_id==='demo-free-six')return event.outcome==='success'?'6 PM · available':'6 PM · checking';
  if (event.event === 'intent_revised') return /5 PM to 6 PM/i.test(event.reason||'')?'5 PM → 6 PM':/options only/i.test(event.reason || '') ? 'Options only' : 'Changed intent';
  if (event.event === 'tool_superseded') return /book/.test(event.function || '') ? 'Booking cancelled' : /search|find/.test(event.function || '') ? 'Old search dropped' : 'Action superseded';
  if (event.event === 'demo_result') return /Available/i.test(event.text||'')?'Availability':/Draft saved/i.test(event.text||'')?'Draft saved':/Coffee with/i.test(event.text||'')?'Coffee · 6 PM':/PNR/i.test(event.text||'')?'PNR found':/No booking made/i.test(event.text || '') ? 'No booking made' : /calendar/i.test(event.text || '') ? 'Meeting found' : 'Flight details';
  if (event.event === 'demo_navigation') {
    if (event.app === 'Phone') return 'Back to call';
    if (event.app === 'Gmail') return /Open.*email/i.test(event.step) ? 'Flight email' : 'Gmail';
    return event.app;
  }
  if (event.function === 'search_flights') return event.outcome === 'success' ? 'Flight options' : event.args?.destination || 'Find flights';
  if (event.function === 'book_flight') return 'Booking pending';
  if(event.function==='create_gmail_draft' && event.outcome==='success')return event.simulated?'Draft saved · Not sent':'Draft saved';
  const names={find_contact:'Find Rajesh',create_gmail_draft:'Save draft',create_calendar_event:'Save event',search_gmail:'Find flight',get_gmail_message:'Read email',list_tasks:'Read tasks',list_calendar_events:'Check calendar',prepare_plan:'Prepare action',prepare_resource_change:'Update item',execute_plan:'Execute action'};
  return names[event.function] || event.function?.replaceAll('_',' ') || ({tool_caller_detached:'Speech interrupted',approval_revoked:'Consent revoked',external_api_started:'API request',external_api_completed:'API result'})[event.event] || describeEvent(event)?.title || 'Session';
}
function completedDemoWrite(event){return event.simulated && event.event==='tool_executed' && event.outcome==='success' && /^(create|update|delete|execute)_/.test(event.function||'');}
function quietTimelineEvent(event) {
  if (!event.simulated) return ['tool_proposed','external_api_started','approval_checked'].includes(event.event);
  if (event.event === 'demo_navigation') {
    if(event.demo_scene==='calendar')return true;
    return !['Open inbox','Open contacts','Open compose'].includes(event.step);
  }
  if (['demo_action_paused','tool_superseded'].includes(event.event))return true;
  if(event.event==='tool_dispatch'&&event.function==='create_calendar_event')return false;
  if (event.event === 'demo_result') return /Available|^Coffee with|^Draft saved/i.test(event.text || '');
  return ['tool_dispatch','plan_updated','workspace_items'].includes(event.event);
}
// The microphone is published only after the actual worker acknowledges its guard.
// A stale API/worker cannot silently turn Demo Live into unrestricted Live.
function validateSessionPresentation(reply, mode){
  if(reply.activation_version!==2 || reply.presentation!==mode)
    throw new Error('Restart Reprise and reload: activation configuration is out of date.');
}
function validateActivation(reply,mode){
  validateSessionPresentation(reply,mode);
  if(reply.enabled!==(mode==='demo-live') || !['standby','active','interruption'].includes(reply.state)
      || !Number.isInteger(reply.epoch))
    throw new Error('Agent did not confirm the requested listening guard.');
  return reply.state;
}
async function negotiatePresentation(target,mode){
  let agent=[...target.remoteParticipants.values()].find(p=>p.isAgent);
  if(!agent)agent=await new Promise((resolve,reject)=>{
    const joined=p=>{if(p.isAgent){cleanup();resolve(p);}};
    const timer=setTimeout(()=>{cleanup();reject(new Error('Voice agent did not connect.'));},20000);
    const cleanup=()=>{clearTimeout(timer);target.off('participantConnected',joined);};
    target.on('participantConnected',joined);
  });
  const reply=JSON.parse(await target.localParticipant.performRpc({destinationIdentity:agent.identity,
    method:'reprise.presentation',payload:JSON.stringify({mode,activation_version:2}),responseTimeout:10000}));
  validateActivation(reply,mode);
  return reply;
}
async function connectVoiceInput(target,mode){
  const reply=await negotiatePresentation(target,mode);
  await target.localParticipant.setMicrophoneEnabled(true);
  return reply;
}
if (typeof module !== 'undefined') module.exports = {describeEvent,timelineState,timelineCaption,validateActivation,validateSessionPresentation,connectVoiceInput};
if (typeof document !== 'undefined') initialize();

function initialize() {
const $ = id => document.getElementById(id);
const {Room, RoomEvent, Track} = LivekitClient;
let currentPlan = null, googleConnected = false, planningTimeZone = 'Asia/Kolkata';
let room, pollTimer, clockTimer, startedAt = 0, cursor = 0, activeRoom = null;
let generation = 0, polling = false, muted = false, agentClosed = false, closing = false;
const seen = new Set();
let agentReady = false, localMode = false, audioMuted = false, activityCount = 0;
const liveSegments = new Map(), committed = {user:'',assistant:''};
let currentTranscriptRow=null, lastUserRow=null, currentTimelineRow=null;
const timelineProposals=new Map(), presentationRows=[];
let callAssistActive=false, assistWorkspace='mock', assistCalendar=null, assistSupersededTime=null, assistProjectionKey=null;
const assistProvider=()=>assistWorkspace==='mock'?'Mock Calendar':'Google Calendar';
function assistCall(values={}){const pending=currentPlan?.status==='prepared'&&currentPlan.actions?.find(a=>a.kind==='calendar'&&['pending','approval_required'].includes(a.status));phone.show({app:'call',view:'call',live:true,inCall:!!activeRoom,connected:!!activeRoom,caller:'Live Call Assist',callCaption:$('status').textContent+' · '+assistProvider(),callSeconds:activeRoom?(Date.now()-startedAt)/1000:0,activation:activeRoom?'active':'standby',muted,audioMuted,...(pending?{confirmation:pending.title+' · '+assistTime(pending.start),confirmationDetail:currentPlan.approved?'Confirmed · Awaiting dispatch':'Pending · Owner confirmation',ownerConfirm:!$('confirmPlan').disabled}:{}),...values});}
function assistTime(value){return new Intl.DateTimeFormat('en-IN',{hour:'numeric',minute:'2-digit',timeZone:planningTimeZone}).format(new Date(value));}
function projectAssistPlan(plan){
  const action=plan.actions?.find(a=>a.kind==='calendar');if(!action||plan.origin==='lookup')return;
  if(['superseded','not_executed'].includes(action.status)){
    assistSupersededTime=assistTime(action.start);assistCalendar=action;
    if(phone.current.app==='calendar')phone.show({...phone.current,ownerConfirm:false,saveState:'paused',correctionText:assistSupersededTime+' · Not saved'});
    return;
  }
  if(action.status==='completed'&&action.google_id&&action.receipt?.id===action.google_id){
    assistCalendar=null;assistCall({confirmation:action.title+' · '+assistTime(action.current_values?.start||action.start),confirmationDetail:assistProvider()+' receipt · '+action.google_id});return;
  }
  if(['unknown','failed'].includes(action.status)||['partial','needs_review'].includes(plan.status)){
    assistCall({confirmation:action.status==='unknown'?'Outcome unknown':'Action not completed',confirmationDetail:action.error||plan.status});return;
  }
  if(!['pending','approval_required','running'].includes(action.status))return;
  assistCalendar=action;
  const date=value=>new Intl.DateTimeFormat('en-IN',{weekday:'short',day:'numeric',month:'short',timeZone:planningTimeZone}).format(new Date(value));
  phone.show({app:'calendar',view:'edit',live:true,inCall:!!activeRoom,connected:!!activeRoom,callSeconds:activeRoom?(Date.now()-startedAt)/1000:0,activation:'active',item:{...action,id:plan.plan_id},
    time:assistTime(action.start),endTime:assistTime(action.end),startDate:date(action.start),endDate:date(action.end),
    previousTime:assistSupersededTime&&assistSupersededTime!==assistTime(action.start)?assistSupersededTime:undefined,
    saveState:action.status==='running'?'saving':assistSupersededTime?'revising':'pending',
    ownerConfirm:!$('confirmPlan').disabled});
}
function focusAssistPresentation(){
  if(!callAssistActive)return;
  const records=presentationRows.filter(r=>!r.simulated&&r.room===activeRoom);
  const dialogue=records.filter(r=>r.role), actions=records.filter(r=>!r.role&&['tool_raw_proposed','tool_proposed','tool_dispatch','tool_executed','plan_updated','tool_superseded','tool_rejected','tool_validation_failed','external_api_completed','mock_calendar_receipt'].includes(r.kind));
  for(const record of presentationRows.filter(r=>!r.simulated)){
    const items=record.role?dialogue:actions, age=items.length-1-items.indexOf(record);
    record.node.setAttribute('data-live-archived',String(!items.includes(record)||age>(record.role?2:3)));
    if(record.role)record.node.setAttribute('data-recency',String(age));
  }
  $('conversation').scrollTop=$('conversation').scrollHeight;$('activity').scrollLeft=0;
}
function showPresentationChannel(){for(const record of presentationRows)record.node.hidden=record.simulated!==demoActive || (record.simulated && record.run!==demoRun);}
function focusDemoPresentation(scene){
  const records=presentationRows.filter(r=>r.simulated&&r.run===demoRun);
  const dialogue=records.filter(r=>r.role);
  for(const record of records){
    if(record.role){const age=dialogue.length-1-dialogue.indexOf(record);record.node.setAttribute('data-recency',String(age));record.node.setAttribute('data-archived',String(age>2));}
    else record.node.setAttribute('data-scene-current',String(record.scene===scene));
  }
  $('conversation').scrollTop=$('conversation').scrollHeight;$('activity').scrollLeft=0;
}
let demoActive = false, demoPlaying = false, demoTimer, demoEpoch = 0, demoRun = 0, demoDeadline=0, demoFrameStarted=0;
function allowPhoneNavigation() {
  if (activeRoom || room || ($('start').disabled && !demoActive)) return false;
  pauseDemo();
  if (!demoActive) {$('experience').value='scripted';$('experience').onchange();}
  return true;
}
const phone = ReprisePhone.createPhone($('galaxyApp'),document,allowPhoneNavigation,view=>{
  const backgroundCall=(demoActive||callAssistActive) && view.inCall && view.app!=='call';
  $('phoneOngoingCall').hidden=!backgroundCall;$('phoneClock').hidden=!!backgroundCall;
  const seconds=view.callSeconds||0;
  $('phoneCallTime').textContent=String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(Math.floor(seconds%60)).padStart(2,'0');
},action=>{const id={approve:'confirmPlan',end:'end',mute:'mute',speaker:'speaker'}[action];if(id&&!$(id).disabled)$(id).onclick();});
const playback = ReprisePhone.createPlayback(frame => {
  phone.show(frame.screen);
  frame.events.forEach((event,index) => renderEvent({...event,demo_scene:frame.scene,...(event.function==='create_calendar_event' && event.event==='tool_executed' ? {calendar_write_count:playback.calendarWrites.length} : {}),event_id:'demo:' + demoRun + ':' + playback.position + ':' + index,wall_time:Date.now()/1000}));
  focusDemoPresentation(frame.scene);
  $('demoProgress').textContent = playback.position + ' / ' + playback.length;
});
// Keep one set of controls and result components for both data sources.
const liveResults=document.createElement('section');
liveResults.className='live-results';liveResults.hidden=true;
liveResults.setAttribute('aria-label','Agent results');
const phoneStage=document.querySelector('.phone-stage');
phoneStage.before(liveResults);
const sessionControls=document.querySelector('.call-controls');
const phoneControlsParent=sessionControls.parentNode;
const statusParent=$('status').parentNode;
const planParent=$('planCard').parentNode;
const presentationControls=document.querySelector('.presentation-controls');
const presentationParent=presentationControls.parentNode;
const timelinePanel=document.querySelector('.timeline-panel');
const timelineParent=timelinePanel.parentNode;
const actionCard=$('phoneActivity');
liveResults.append(actionCard);
const micIndicator=document.createElement('span');
micIndicator.className='mic-indicator';micIndicator.textContent='Mic off';
document.querySelector('.chat').prepend(micIndicator);
let presentationMode='live', livePlan=null, activationState=null, activationEpoch=-1, changingPresentation=false;
const activationStatus=document.createElement('span');
activationStatus.className='status';activationStatus.setAttribute('role','status');
activationStatus.hidden=true;activationStatus.textContent='Standby';
presentationControls.append(activationStatus);
function renderActivation(){
  activationStatus.hidden=demoActive||presentationMode!=='demo-live'||activationState===null;
  activationStatus.textContent=activationState==='standby'?'Standby':'Active';
}
async function selectLivePresentation(next){
  if(!activeRoom||next===presentationMode)return true;
  if(localMode){notice('End the text session before selecting Demo Live.');return false;}
  const target=room, epoch=generation;
  changingPresentation=true;
  try{
    if(!muted)await target.localParticipant.setMicrophoneEnabled(false);
    const result=await negotiatePresentation(target,next);
    if(target!==room||epoch!==generation)return false;
    activationState=result.state;activationEpoch=result.epoch;
    if(!muted)await target.localParticipant.setMicrophoneEnabled(true);
    return true;
  }catch(error){
    notice(error.message||'Could not confirm the agent listening guard.');
    await closeSession();return false;
  }finally{changingPresentation=false;}
}

function syncModeAvailability(){
  $('experience').disabled=false;
  $('experience').querySelector('[value="scripted"]').disabled=!!(activeRoom||room||$('start').disabled&&!demoActive);
}
function presentDataSource(){
  document.querySelector('main').dataset.source=demoActive?'preview':presentationMode;
  phoneStage.hidden=!demoActive;liveResults.hidden=demoActive||presentationMode==='demo-live';
  if(demoActive){
    phoneControlsParent.insertBefore(sessionControls,document.querySelector('.phone-nav'));
    statusParent.append($('status'));planParent.append($('planCard'));
    presentationParent.append(presentationControls);timelineParent.append(timelinePanel);
  }else{
    document.querySelector('.topbar .connection').prepend(presentationControls,sessionControls);
    sessionControls.append($('status'));liveResults.append($('planCard'));
    if(presentationMode==='live')liveResults.append(timelinePanel);
    else timelineParent.append(timelinePanel);
  }
  renderActivation();
  if(!activeRoom&&!demoActive)$('planner').disabled=presentationMode==='demo-live'||callAssistActive;
  syncModeAvailability();
}
function pauseDemo() {demoPlaying=false; ++demoEpoch; clearInterval(demoTimer); $('demoPlay').textContent='Play';}
function resetDemo() {
  pauseDemo(); ++demoRun; playback.reset($('demoWorkflow').value);showPresentationChannel();
  // Archived rows remain intact; a new run must not reuse their lifecycle nodes.
  timelineProposals.clear();currentTimelineRow?.setAttribute('data-current','false');currentTimelineRow=null;
  currentPlan=null; $('planCard').hidden=true; $('confirmPlan').disabled=true;
  currentTranscriptRow?.setAttribute('data-current','false');currentTranscriptRow=null;lastUserRow=null;
  $('phoneRevision').hidden=true; $('phoneActivity').hidden=true; $('phoneTranscriptRow').hidden=true;
  phone.reset(); $('demoProgress').textContent='0 / ' + playback.length;
  showStatus('Scripted demo');
}
function nextDemo() {
  if (!demoActive) return;
  if (!playback.advance() || playback.position===playback.length) pauseDemo();
}
$('experience').onchange = async () => {
  const next=$('experience').value;
  if(!demoActive&&next!=='scripted'&&activeRoom){
    $('experience').disabled=true;
    const changed=await selectLivePresentation(next);
    $('experience').disabled=false;
    if(!changed){$('experience').value=presentationMode;return;}
  }
  if(next==='scripted'&&(activeRoom||room||$('start').disabled&&!demoActive)){$('experience').value=presentationMode;return;}
  const wasDemo=demoActive;
  presentationMode=next;
  if(!wasDemo&&next!=='scripted'){presentDataSource();return;}
  if(!wasDemo)livePlan=currentPlan;
  pauseDemo(); demoActive=next==='scripted';callAssistActive=!demoActive&&$('mode').value==='call_assist';showPresentationChannel();
  presentDataSource();
  currentTranscriptRow?.setAttribute('data-current','false');currentTranscriptRow=null;lastUserRow=null;
  $('phoneSurface').setAttribute('data-experience',demoActive?'scripted':callAssistActive?'call-assist':'live');
  $('demoControls').hidden=!demoActive; $('demoBadge').hidden=!demoActive; $('voiceConnections').hidden=demoActive;
  $('start').disabled=demoActive; $('planner').disabled=demoActive||presentationMode==='demo-live'; $('mode').disabled=demoActive;
  if (demoActive) {
    $('phoneModality').textContent='Scripted demo'; service('livekitStatus','not used'); service('geminiStatus','not used'); resetDemo();
  } else {
    currentPlan=livePlan; $('planCard').hidden=!currentPlan; $('phoneRevision').hidden=true; $('phoneTranscriptRow').hidden=true;
    $('phoneModality').textContent='Voice assistant'; phone.reset(); showStatus('Not connected');
    service('livekitStatus','disconnected');service('geminiStatus','disconnected');
    if(callAssistActive){$('planner').value='gemini';$('planner').disabled=true;}
    if(currentPlan)renderPlan(currentPlan);
  }
  syncModeAvailability();
};
$('mode').onchange=()=>{callAssistActive=$('mode').value==='call_assist';if(callAssistActive)$('planner').value='gemini';$('planner').disabled=callAssistActive||presentationMode==='demo-live';};
$('demoWorkflow').onchange = () => {if (demoActive) resetDemo();};
$('demoRestart').onclick = () => {if (demoActive) resetDemo();};
$('demoNext').onclick = () => {pauseDemo(); nextDemo();};
$('demoPlay').onclick = () => {
  if (!demoActive) return;
  if (demoPlaying) {pauseDemo();return;}
  if (playback.position===playback.length) resetDemo();
  demoPlaying=true; $('demoPlay').textContent='Pause'; const epoch=++demoEpoch;
  const advance=()=>{if(!playback.advance()){pauseDemo();return;}demoFrameStarted=Date.now();demoDeadline=demoFrameStarted+playback.durationMs;};
  advance();
  if(demoPlaying)demoTimer=setInterval(()=>{if(demoActive && demoPlaying && epoch===demoEpoch){phone.setElapsed((playback.elapsedMs+Math.min(Date.now()-demoFrameStarted,playback.durationMs))/1000);if(Date.now()>=demoDeadline)advance();}},100);
};
$('phoneOngoingCall').onclick=()=>{if(callAssistActive){assistCall();return;}if(allowPhoneNavigation())phone.returnToCall();};
$('phoneRecent').onclick=()=>{if(allowPhoneNavigation())phone.recents();};
$('phoneHome').onclick = () => {if(allowPhoneNavigation())phone.home();};
$('phoneBack').onclick = () => {if(allowPhoneNavigation())phone.back();};
$('inspectActions').onchange = () => document.body.setAttribute('data-inspect',String($('inspectActions').checked));
function service(id,state) {
  $(id).textContent = (id === 'livekitStatus' ? 'LiveKit' : id === 'localStatus' ? 'Gemma' : 'Gemini') + ' · ' + state;
  $(id).setAttribute('data-state',state);
}
function phoneTranscript(role,text) {
  $('phoneSpeaker').textContent = role === 'user' ? 'You' : 'Reprise';
  $('phoneTranscript').textContent = text || '';
  $('phoneTranscriptRow').hidden = !text;
}
function phoneActivity(entry,event) {
  if(event.simulated)return;
  const state = timelineState(event);
  const labels = {pending:'Pending',running:'Running',completed:'Completed',cancelled:'Superseded',interrupted:'Speech interrupted',corrected:'Intent revised',failed:'Failed',unknown:'Outcome unknown',info:'Activity'};
  $('phoneActivity').hidden = !!currentPlan && (event.event === 'plan_updated' || (event.event === 'tool_executed' && ['prepare_plan','prepare_change','prepare_resource_change','execute_plan'].includes(event.function)));
  $('phoneActivity').setAttribute('data-state',state);
  $('phoneActivityState').textContent = labels[state];
  $('phoneActivityTitle').textContent = entry.title;
  $('phoneActivityText').textContent = entry.text || '';
  if(!demoActive&&currentPlan&&(['plan_updated'].includes(event.event)||['prepare_plan','prepare_change','prepare_resource_change','execute_plan'].includes(event.function))){actionCard.hidden=true;return;}
  if(!demoActive&&!['plan_updated'].includes(event.event)&&!['prepare_plan','prepare_change','prepare_resource_change','execute_plan'].includes(event.function)){
    actionCard.hidden=false;$('planCard').hidden=true;
  }
}
function liveText(role,text) {
  $(role === 'user' ? 'partial' : 'agentPartial').textContent = text ? (role === 'user' ? (presentationMode==='demo-live'?'Call':'You') : 'Reprise') + ' · ' + text : '';
  $(role === 'user' ? 'userLiveRow' : 'agentLiveRow').hidden = !text;
  $(role === 'user' ? 'userLiveTime' : 'agentLiveTime').textContent = text ? new Date().toLocaleTimeString() : '';
  if (text) phoneTranscript(role,text);
}
function showStatus(label) {
  micIndicator.textContent=activeRoom?(muted?'Mic muted':'Mic on'):'Mic off';
  $('status').textContent = label; $('phoneStatus').textContent = label;
  const state = ({Speaking:'speaking',Listening:'listening','User speaking':'listening',Processing:'thinking',Interrupted:'interrupted',Connecting:'connecting'})[label] || 'idle';
  $('phoneSurface').setAttribute('data-state',state);
  if(callAssistActive&&phone.current.app==='call')phone.show({...phone.current,callCaption:label+' · '+assistProvider()});
}
function notice(text) { $('notice').textContent = text; }
function clockTick() {
  $('phoneClock').textContent = new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false});
  const seconds = Math.floor((Date.now() - startedAt) / 1000);
  if (activeRoom && !agentReady && seconds >= 15) { showStatus('Waiting for assistant'); notice('No voice agent has joined yet. Check that the Reprise agent worker is running.'); }
  $('timer').textContent = String(Math.floor(seconds / 60)).padStart(2,'0') + ':' + String(seconds % 60).padStart(2,'0');
  if(callAssistActive&&activeRoom)phone.setElapsed(seconds);
}
function append(entry, event = {}) {
  if (!entry) return;
  const list = $(entry.role ? 'conversation' : 'activity');
  const nearBottom = entry.role ? list.scrollHeight - list.scrollTop - list.clientHeight < 70 : (list.scrollWidth || 0) - list.scrollLeft - (list.clientWidth || 0) < 90 || !activityCount;
  list.querySelector('.empty')?.remove();
  const item = document.createElement('li'); item.className = 'entry ' + (entry.role || '');
  presentationRows.push({node:item,role:entry.role,scene:event.demo_scene,kind:event.event,room:event.room||activeRoom,simulated:!!event.simulated,run:demoRun});item.hidden=!!event.simulated!==demoActive;
  item.setAttribute('data-state',timelineState(event));if(event.event)item.setAttribute('data-event',event.event);
  if (event.simulated) item.setAttribute('data-source','scripted');
  if (event.proposal_id) item.setAttribute('data-proposal-id',event.proposal_id);
  const heading = document.createElement('header'), label = document.createElement('strong'), time = document.createElement('time');
  label.textContent = entry.role ? (event.simulated && event.speaker ? event.speaker : entry.role==='user'?(presentationMode==='demo-live'?'Call':'You'):'Reprise') : entry.title;
  const date = new Date(event.wall_time ? event.wall_time * 1000 : Date.now());
  time.dateTime = date.toISOString(); time.textContent = date.toLocaleTimeString();
  heading.append(label,time);
  if (!entry.role) {
    const prior=event.proposal_id && timelineProposals.get(event.proposal_id);
    if(event.event==='tool_superseded' && prior && !prior.dispatched && !prior.settled)item.setAttribute('data-superseded','true');
    const short=document.createElement('span');short.className='short-label';short.textContent=timelineCaption(event);heading.append(short);
    if(event.event==='demo_action_paused' && prior)prior.pauseNode=item;
    if(event.simulated&&event.event==='tool_dispatch'&&event.function==='create_calendar_event'&&prior)prior.dispatchNode=item;
    item.setAttribute('data-quiet',String(quietTimelineEvent(event)||!!(event.simulated && event.event==='tool_executed' && prior && !completedDemoWrite(event))));
    if (!quietTimelineEvent(event)) {currentTimelineRow?.setAttribute('data-current','false');currentTimelineRow=item;item.setAttribute('data-current','true');}
    if (event.proposal_id && ['tool_raw_proposed','tool_proposed'].includes(event.event)) timelineProposals.set(event.proposal_id,{node:item,caption:short,dispatched:false,settled:false});
  } else {
    currentTranscriptRow?.setAttribute('data-current','false');currentTranscriptRow=item;item.setAttribute('data-current','true');
    if (entry.role==='user') lastUserRow=item;
  }
  item.append(heading);
  if (entry.text) { const p = document.createElement('p'); p.textContent = entry.text; item.append(p); }
  if (entry.details) {
    const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
    details.setAttribute('name','timeline-details'); summary.textContent = 'Details'; pre.textContent = JSON.stringify(entry.details,null,2); details.append(summary,pre); item.append(details);
  }
  list.append(item);
  if (entry.role) { if (nearBottom) list.scrollTop = list.scrollHeight; }
  else {
    activityCount++; $('eventCount').textContent = activityCount + (activityCount === 1 ? ' event' : ' events');
    if (nearBottom) list.scrollLeft = list.scrollWidth;
    // Lifecycle controls are retained in history; only backend actions fill the phone card.
    if (event.event) phoneActivity(entry,event);
  }
  focusAssistPresentation();
  if(!demoActive){
    const rows=presentationRows.filter(r=>!r.simulated&&r.room===activeRoom);
    for(const r of rows.filter(r=>r.role)){const age=rows.filter(r=>r.role).indexOf(r);r.node.dataset.recency=String(rows.filter(r=>r.role).length-1-age);}
    const actions=rows.filter(r=>!r.role&&r.node.dataset.quiet!=='true');
    actions.forEach((r,i)=>r.node.dataset.liveHistory=String(i>=actions.length-4));
  }
}
function renderEvent(event) {
  if(callAssistActive&&event.simulated)return;
  if (event.event_id && seen.has(event.event_id)) return;
  if (event.event_id) seen.add(event.event_id);
  // Capture follow intent before a new plan changes the timeline's width.
  const rail=$('activity'), followTimeline=(rail.scrollWidth || 0) - (rail.scrollLeft || 0) - (rail.clientWidth || 0) < 90;
  const prior=event.proposal_id && timelineProposals.get(event.proposal_id);
  if(prior && event.event==='tool_dispatch') {prior.dispatched=true;if(event.simulated && event.function==='create_calendar_event'){prior.node.setAttribute('data-state','running');prior.node.setAttribute('data-quiet','true');prior.caption.textContent=timelineCaption(event);}}
  if(prior && event.event==='tool_executed') {prior.settled=true;prior.node.setAttribute('data-state',timelineState(event));if(prior.caption)prior.caption.textContent=timelineCaption(event);if(completedDemoWrite(event))prior.node.setAttribute('data-quiet','true');prior.dispatchNode?.setAttribute('data-quiet','true');}
  if(prior && event.simulated && event.event==='demo_action_paused' && !prior.dispatched && !prior.settled)prior.node.setAttribute('data-state','interrupted');
  if(prior && event.event==='tool_superseded' && !prior.dispatched && !prior.settled) {prior.node.setAttribute('data-state','cancelled');prior.node.setAttribute('data-superseded','true');prior.pauseNode?.setAttribute('data-superseded','true');}
  if(event.event==='intent_revised') lastUserRow?.setAttribute('data-correction','true');
  const change = ({intent_revised:'Intent revised',tool_superseded:'Earlier action superseded',tool_caller_detached:'Speech response interrupted'})[event.event];
  if (change) { $('phoneRevision').hidden = false; $('phoneRevision').textContent = change; }
  if(event.event==='call_activation'){
    if(event.epoch<activationEpoch)return; // old startup events cannot override an RPC acknowledgement
    if(!changingPresentation && event.enabled!==(presentationMode==='demo-live')){
      notice('Agent listening mode mismatch. Reconnect before speaking.');
      closeSession();return;
    }
    if(!changingPresentation){activationState=event.state;activationEpoch=event.epoch;renderActivation();}
    return;
  }
  if (event.event === 'transcript_received') { if (!event.is_final) liveText('user',event.text); return; }
  if (event.event === 'conversation_item' && ['user','assistant'].includes(event.role)) {
    committed[event.role] = event.text; liveText(event.role,''); phoneTranscript(event.role,event.text);
  }
  if (event.event === 'agent_ready') { agentReady = true; agentClosed = false; if ($('geminiStatus').textContent.endsWith('waiting for agent')) service('geminiStatus','connecting'); if ($('notice').textContent.startsWith('No voice agent')) notice(''); }
  if (event.event === 'local_connection') service('localStatus',event.state);
  if (event.event === 'gemini_connection') { service('geminiStatus',event.state); }
  if (event.event === 'session_error') notice('Assistant error: ' + event.error_type);

  if (event.event === 'plan_updated') renderPlan(event.plan);
  if(callAssistActive&&event.event==='intent_revised'&&phone.current.app==='calendar'&&phone.current.saveState!=='saving')phone.show({...phone.current,ownerConfirm:false,saveState:'paused'});
  if (event.event === 'approval_revoked' && currentPlan?.plan_id === event.plan_id) {
    renderPlan({...currentPlan, status:event.status || 'needs_review'});
  }
  if (event.event === 'agent_state') showStatus(({thinking:'Processing',speaking:'Speaking',listening:'Listening',initializing:'Connecting',idle:'Idle'})[event.state] || event.state);
  if (event.event === 'speech_started') showStatus('User speaking');
  if (event.event === 'conversation_item' && event.interrupted) showStatus('Interrupted');
  if (event.event === 'session_closed') {agentClosed = true; service(localMode ? 'localStatus' : 'geminiStatus','disconnected'); showStatus('Assistant disconnected'); $('confirmPlan').disabled = true;}
  if (!['agent_state','speech_started','speech_ended','gemini_connection','local_connection'].includes(event.event)) {
    const entry=describeEvent(event); append(entry,event);
    if (entry && !entry.role && followTimeline) rail.scrollLeft=rail.scrollWidth;
  }
  if (event.event === 'session_closed' && localMode && closing) finishSession();
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
  if (localMode && activeRoom) {
    try {const response = await fetch('/local/' + encodeURIComponent(activeRoom) + '/end', {method:'POST'});
      if (!response.ok) throw new Error();
    } catch {notice('Could not end local session. Retry End.'); closing = false; return;}
    ['end','mute','speaker','text','send','confirmPlan'].forEach(id => $(id).disabled = true);
    showStatus('Ending');
    if (agentClosed) await finishSession();
    return; // Keep polling until dispatched writes have receipts and session_closed arrives.
  }
  await finishSession();
}
async function finishSession() {
  ++generation; clearInterval(pollTimer); clearInterval(clockTimer);
  activeRoom = null; polling = false;activationState=null;activationEpoch=-1;renderActivation();
  const previous = room; room = null;
  if (previous) await previous.disconnect();
  document.querySelectorAll('[data-agent-audio]').forEach(el => el.remove());
  ['end','mute','speaker','text','send','confirmPlan'].forEach(id => $(id).disabled = true);
  $('start').disabled = false; $('mode').disabled = false; $('planner').disabled = false; $('experience').disabled = false; liveText('user','');
  syncModeAvailability();
  $('phoneSurface').setAttribute('data-call-connected','false');
  if(callAssistActive){$('mode').disabled=true;$('planner').disabled=true;assistCall();}
  service('livekitStatus','disconnected'); service('geminiStatus','disconnected'); liveText('assistant',''); liveSegments.clear();
  service('localStatus','disconnected'); audioMuted = false; $('speaker').textContent = 'Audio on'; $('speaker').setAttribute('aria-pressed','false'); showStatus('Disconnected'); append({title:'Session ended'}); closing = false;
}
$('start').onclick = async () => {
  if (demoActive) return;
  activationState=null;activationEpoch=-1;renderActivation();
  $('start').disabled = true; notice(''); showStatus('Connecting'); service('livekitStatus','connecting'); service('geminiStatus','waiting for agent');
  callAssistActive=$('mode').value==='call_assist';syncModeAvailability();
  const epoch = ++generation;
  localMode = presentationMode!=='demo-live'&&!callAssistActive&&$('planner').value === 'local';
  $('phoneModality').textContent = localMode ? 'Text assistant' : 'Voice assistant';
  $('phoneTranscriptRow').hidden = true; $('phoneActivity').hidden = true; $('phoneRevision').hidden = true;
  $('localStatus').hidden = !localMode;
  if (localMode) {service('livekitStatus','not used'); service('geminiStatus','not used'); service('localStatus','connecting');}
  try {
    const response = await fetch('/session?mode=' + encodeURIComponent($('mode').value) + '&planner=' + (localMode ? 'local' : 'gemini') + '&presentation=' + (presentationMode==='demo-live'?'demo-live':'live'), {method:'POST'});
    if (!response.ok) throw new Error('Could not start a session (' + response.status + ')');
    const data = await response.json();
    if(!localMode)validateSessionPresentation(data,presentationMode);
    if(callAssistActive){assistWorkspace=data.workspace_backend;assistSupersededTime=null;assistCalendar=null;assistProjectionKey=null;}
    if (localMode) {
      activeRoom = data.room; cursor = 0; polling = false; agentClosed = false; agentReady = true; currentPlan = null; seen.clear();
      $('planCard').hidden = true; $('confirmPlan').disabled = true; $('mode').disabled = true; $('planner').disabled = true;
      startedAt = Date.now(); clockTick(); clockTimer = setInterval(clockTick,1000); pollTimer = setInterval(poll,600);
      service('localStatus','connected'); showStatus('Text connected');
      ['end','text','send'].forEach(id => $(id).disabled = false); $('mute').disabled = true;
      await poll(); return;
    }
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
      if (track.kind === Track.Kind.Audio && epoch === generation) { const el = track.attach(); el.dataset.agentAudio='true'; el.muted=audioMuted; document.body.append(el); }
    });
    room.on(RoomEvent.Disconnected, () => { if (epoch === generation && activeRoom) closeSession(); });
    await room.connect(data.url,data.token); await room.startAudio();
    const activated=await connectVoiceInput(room,presentationMode);
    if(epoch!==generation)return;
    activationState=activated.state;activationEpoch=activated.epoch;renderActivation();
    service('livekitStatus','connected');
    activeRoom = data.room; cursor = 0; polling = false; muted = false; agentClosed = false; agentReady = false; liveSegments.clear(); committed.user=''; committed.assistant=''; seen.clear(); currentPlan = null;
    $('planCard').hidden = true; $('confirmPlan').disabled = true; $('mode').disabled = true; $('planner').disabled = true;
    $('mute').textContent = 'Mute microphone'; $('mute').setAttribute('aria-pressed','false');
    startedAt=Date.now(); clockTick(); clockTimer=setInterval(clockTick,1000); pollTimer=setInterval(poll,600);
    if(callAssistActive){$('phoneSurface').setAttribute('data-call-connected','true');assistCall();}
    append({title:'Session connected',text:$('mode').value === 'productivity' ? 'Google Workspace' : 'Simulated tools'});
    showStatus('Connected'); ['end','mute','speaker','text','send'].forEach(id => $(id).disabled=false); await poll();
  } catch (error) { notice(error.message || 'Connection failed'); await closeSession(); }
};
$('end').onclick = closeSession;
$('mute').onclick = async () => {
  const target = room; if (!target) return; $('mute').disabled = true;
  try { await target.localParticipant.setMicrophoneEnabled(muted); muted = !muted;
    $('mute').textContent = muted ? 'Unmute microphone' : 'Mute microphone'; $('mute').setAttribute('aria-pressed',String(muted));
    micIndicator.textContent=muted?'Mic muted':'Mic on';
    append({title:muted ? 'Microphone muted' : 'Microphone enabled'});
    if(callAssistActive&&phone.current.app==='call')assistCall({confirmation:phone.current.confirmation,confirmationDetail:phone.current.confirmationDetail});
  } catch {notice('Microphone could not be changed.');}
  finally {$('mute').disabled = !activeRoom;}
};
$('speaker').onclick = async () => {
  if (!room || !activeRoom || localMode || $('speaker').disabled) return;
  const target = room, epoch = generation, nextMuted = !audioMuted;
  $('speaker').disabled = true;
  try {
    if (!nextMuted) await target.startAudio();
    if (epoch !== generation || target !== room || !activeRoom) return;
    audioMuted = nextMuted;
    document.querySelectorAll('[data-agent-audio]').forEach(el => el.muted = audioMuted);
    $('speaker').setAttribute('aria-pressed',String(audioMuted));
    $('speaker').textContent = audioMuted ? 'Audio off' : 'Audio on';
    if(callAssistActive&&phone.current.app==='call')assistCall({confirmation:phone.current.confirmation,confirmationDetail:phone.current.confirmationDetail});
  } catch { if (epoch === generation) notice('Audio playback could not be enabled.'); }
  finally { if (epoch === generation) $('speaker').disabled = !activeRoom || localMode; }
};
function scrollTimeline(left) {
  const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
  $('activity').scrollBy({left,behavior});
}
$('timelineBack').onclick = () => scrollTimeline(-520);
$('timelineNext').onclick = () => scrollTimeline(520);
$('chat').onsubmit = async event => {
  event.preventDefault(); const text = $('text').value.trim(); if (!text || !activeRoom || (!room && !localMode)) return;
  $('send').disabled = true;
  try {await sendMessage(text); $('text').value='';}
  catch {notice('Message could not be sent.');} finally {$('send').disabled=!activeRoom;}
};
async function sendMessage(text) {
  if (localMode) {
    const response = await fetch('/local/' + encodeURIComponent(activeRoom) + '/message', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text})});
    if (!response.ok) throw new Error('Local message rejected');
  } else await room.localParticipant.sendText(text,{topic:'lk.chat'});
}
window.addEventListener('beforeunload', () => {
  pauseDemo();
  if (localMode && activeRoom) fetch('/local/' + encodeURIComponent(activeRoom) + '/end', {method:'POST', keepalive:true}).catch(()=>{});
  room?.disconnect();
});
function when(value) {
  return new Intl.DateTimeFormat(undefined, {dateStyle: 'medium', timeStyle: 'short', timeZone: planningTimeZone}).format(new Date(value));
}
function renderPlan(plan) {
  if (!plan) return;
  if(!demoActive){livePlan=plan;actionCard.hidden=true;}
  if(callAssistActive&&currentPlan&&plan.plan_id!==currentPlan.plan_id&&(plan.revision||0)<(currentPlan.revision||0))return;
  currentPlan = plan;
  $('planCard').hidden = false;
  $('planTitle').textContent = plan.title;
  $('planStatus').textContent = plan.simulated ? actionLabel({status:plan.status}) + ' · simulated' : plan.approved && plan.status === 'prepared' ? 'Confirmed' : plan.requires_confirmation && plan.status === 'prepared' ? 'Confirmation required' : actionLabel({status:plan.status});
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
    if (plan.simulated && item.simulation_id) {const proof = document.createElement('p'); proof.textContent='Preview ID · '+item.simulation_id; row.append(proof);}
    if (item.google_id && !plan.simulated) {const proof = document.createElement('p'); proof.textContent = (item.status === 'completed' ? (callAssistActive&&assistWorkspace==='mock'?'Mock confirmed · ':'Google confirmed · ') : 'Resource ID · ') + [item.delivery_result, item.google_id].filter(Boolean).join(' · '); row.append(proof);}
    if (item.url && item.result !== 'deleted' && item.resource_result !== 'deleted' && item.result !== 'undone' && item.resource_result !== 'undone' && /^https:\/\//.test(item.url)) {const link = document.createElement('a'); link.href = item.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'Open in Google ↗'; row.append(link);}
    if (item.error) {const error = document.createElement('p'); error.textContent = item.error; row.append(error);}
    $('planActions').append(row);
  }
  $('confirmPlan').hidden = plan.requires_confirmation === false || plan.status !== 'prepared';
  $('confirmPlan').disabled = demoActive || plan.simulated || (!googleConnected&&!(callAssistActive&&assistWorkspace==='mock')) || !activeRoom || agentClosed || plan.status !== 'prepared' || plan.requires_confirmation === false || (callAssistActive&&plan.approved);
  $('planNote').textContent = '';
  if(callAssistActive){const key=JSON.stringify([plan,$('confirmPlan').disabled]);if(key!==assistProjectionKey){assistProjectionKey=key;projectAssistPlan(plan);}}
}
async function checkGoogle() {
  $('phoneClock').textContent = new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false});
  try {
    const response = await fetch('/google/status', {cache: 'no-store', signal:AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error();
    const value = await response.json(); googleConnected = value.connected; planningTimeZone = value.time_zone;
    $('googleStatus').textContent = value.connected ? (value.gmail_read_requires_reauthorization ? 'Google connected · reconnect for Gmail search' : 'Google connected') : value.configured ? 'Google not connected' : 'Google OAuth setup needed';
    $('connectGoogle').textContent = value.connected ? 'Reconnect Google ↗' : 'Connect Google ↗';
    if (currentPlan) renderPlan(currentPlan);
  } catch {$('googleStatus').textContent = 'Google connection unavailable'; googleConnected = false;if(currentPlan&&callAssistActive&&assistWorkspace==='mock')renderPlan(currentPlan);else $('confirmPlan').disabled = true;}
}
$('confirmPlan').onclick = async () => {
  if (demoActive || currentPlan?.simulated || !currentPlan || !activeRoom || (!room && !localMode)) return;
  $('confirmPlan').disabled = true;
  try {
    const response = await fetch('/plans/' + encodeURIComponent(activeRoom) + '/' + encodeURIComponent(currentPlan.plan_id) + '/approve', {method: 'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:currentPlan.revision})});
    if (!response.ok) throw new Error('The plan changed. Review the current plan again.');
    renderPlan((await response.json()).plan);
    await sendMessage('Confirm the plan.');
    $('planNote').textContent = 'Confirmed';
  } catch (error) {notice(error.message || 'Confirmation failed'); if (currentPlan) renderPlan(currentPlan);}
};
presentDataSource();
checkGoogle();
setInterval(checkGoogle,5000);
}
