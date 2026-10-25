const {test} = require('node:test');
const assert = require('node:assert/strict');
const {describeEvent} = require('./app.js');
test('outcomes are reported without inventing success', () => {
  assert.match(describeEvent({event:'tool_executed',function:'execute_plan',outcome:'unknown'}).title, /Unknown outcome/);
  assert.match(describeEvent({event:'tool_executed',function:'prepare_plan',outcome:'approval_required'}).title, /Approval required/);
  assert.doesNotMatch(describeEvent({event:'tool_dispatch',function:'execute_plan'}).title, /Completed/);
});
test('interruption and supersession have distinct meanings', () => {
  assert.equal(describeEvent({event:'conversation_item',role:'assistant',text:'Okay',interrupted:true}).title, 'Reprise · interrupted');
  assert.match(describeEvent({event:'stale_result_suppressed'}).title, /Earlier result/);
  assert.match(describeEvent({event:'tool_caller_detached'}).title, /Response interrupted/);
});
test('raw arguments and actual receipts are retained', () => {
  const event={event:'tool_raw_proposed',function:'find',args:{id:'K2'}};
  assert.deepEqual(describeEvent(event).details.args,{id:'K2'});
  assert.equal(describeEvent({event:'external_api_completed',outcome:'success',google_id:'receipt-1'}).details.google_id,'receipt-1');
});
test('interim speech is distinguished from final conversation', () => {
  assert.equal(describeEvent({event:'transcript_received',text:'tomorrow',is_final:false}).title,'You · interim');
  assert.equal(describeEvent({event:'conversation_item',role:'user',text:'tomorrow'}).title,'You');
  assert.equal(describeEvent({event:'model_metrics'}),null);
});

const vm = require('node:vm');
const fs = require('node:fs');
function harness({rejectEarlyConfiguration=false,deferAudioResume=false,reducedMotion=false,deferSession=false} = {}) {
  class Element {
    constructor() {this.children=[];this.textContent='';this.disabled=false;this.value='';this.scrollHeight=0;this.scrollTop=0;this.clientHeight=400;}
    append(...items) {this.children.push(...items);}
    replaceChildren(...items) {this.children=items;}
    querySelector() {return null;}
    setAttribute(key,value) {this[key]=value;}
    scrollBy(value) {this.lastScroll=value;}
  }
  const elements=Object.fromEntries([...fs.readFileSync(__dirname+'/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  elements.mode.value='productivity';
  if(elements.experience)elements.experience.value='live';
  if(elements.demoWorkflow)elements.demoWorkflow.value='booking';
  const intervals=[], requests=[]; let batch=[];
  const handlers={}, streams={}; let audioStarts=0, resumeAudio, resumeSession;
  const participant={identity:'user-1',setMicrophoneEnabled:async()=>{},sendText:async()=>{}};
  class Room {constructor(options){this.options=options;this.localParticipant=participant;} on(name,fn){handlers[name]=fn;} registerTextStreamHandler(topic,fn){streams[topic]=fn;} async connect(){if(rejectEarlyConfiguration && this.options.singlePeerConnection !== false) throw new Error("Failed to execute 'setConfiguration' on 'RTCPeerConnection'");} async startAudio(){audioStarts++; if(deferAudioResume && audioStarts===2) await new Promise(resolve=>{resumeAudio=resolve;});} async disconnect(){}}
  const context={document:{getElementById:id=>{assert.ok(elements[id],id);return elements[id];},createElement:()=>new Element(),querySelectorAll:selector=>selector==='[data-agent-audio]'?context.document.body.children.filter(el=>el.dataset?.agentAudio==='true' && !el.removed):[],body:new Element()},
    LivekitClient:{Room,RoomEvent:{TrackSubscribed:"track",Disconnected:"disconnected",ConnectionStateChanged:"connection",AudioPlaybackStatusChanged:"playback",ParticipantDisconnected:"participantLeft"},Track:{Kind:{Audio:"audio"}}},window:{addEventListener(){},matchMedia:()=>({matches:reducedMotion})},setInterval:fn=>{intervals.push(fn);return intervals.length;},clearInterval(){},Date,Intl,Set,AbortSignal,
    fetch:async (url,options)=>{requests.push({url,options});if(deferSession && url.startsWith('/session'))await new Promise(resolve=>{resumeSession=resolve;});return {ok:true,json:async()=>url==='/google/status'?{connected:true,time_zone:'Asia/Kolkata'}:url.startsWith('/session')?{room:'room-1',url:'mock',token:'mock'}:url.startsWith('/events')?{events:batch,cursor:batch.length}:{plan:{plan_id:'p',title:'Plan',status:'approved',actions:[]}}};}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/phone.js','utf8'),context);
  vm.runInNewContext(fs.readFileSync(__dirname+'/app.js','utf8'),context);
  return {elements,intervals,requests,handlers,streams,setBatch:value=>batch=value,resumeAudio:()=>resumeAudio(),resumeSession:()=>resumeSession()};
}
test('session controls and polling display only received events, deduplicate replay, preserve history', async()=>{
  const h=harness(); await h.elements.start.onclick();
  assert.equal(h.elements.status.textContent,'Connected');
  h.setBatch([{event_id:'1',event:'tool_dispatch',function:'execute_plan',args:{plan_id:'p'}},
    {event_id:'2',event:'conversation_item',role:'assistant',text:'<script>not markup</script>',interrupted:true}]);
  const poll=h.intervals[2]; await poll(); const count=h.elements.activity.children.length;
  await poll(); assert.equal(h.elements.activity.children.length,count);
  assert.equal(h.elements.status.textContent,'Interrupted');
  await h.elements.mute.onclick(); assert.equal(h.elements.mute.textContent,'Unmute microphone');
  await h.elements.end.onclick(); assert.ok(h.elements.activity.children.length>=count);
  assert.equal(h.elements.confirmPlan.disabled,true); assert.equal(h.elements.text.disabled,true);
});
test('revoked approval is disabled even after Google status refresh', async()=>{
  const h=harness(); await h.elements.start.onclick();
  h.setBatch([{event_id:'p1',event:'plan_updated',plan:{plan_id:'p',title:'Plan',status:'prepared',actions:[]}}]);
  await h.intervals[2](); assert.equal(h.elements.confirmPlan.disabled,false);
  h.setBatch([{event_id:'p2',event:'approval_revoked',plan_id:'p',status:'needs_review'}]);
  await h.intervals[2](); await h.intervals[0]();
  assert.equal(h.elements.confirmPlan.disabled,true);
});

test('browser configuration rejection uses the deferred LiveKit handshake', async()=>{
  const h=harness({rejectEarlyConfiguration:true});
  await h.elements.start.onclick();
  assert.equal(h.elements.status.textContent,'Connected');
  assert.equal(h.elements.notice.textContent,'');
  assert.equal(h.elements.end.disabled,false);
});
test('speech state updates stay in status rather than flooding history', async()=>{
  const h=harness(); await h.elements.start.onclick();
  const count=h.elements.activity.children.length;
  h.setBatch([{event_id:'state1',event:'agent_state',state:'speaking'}, {event_id:'state2',event:'speech_started'}]);
  await h.intervals[2]();
  assert.equal(h.elements.activity.children.length,count);
  assert.equal(h.elements.status.textContent,'User speaking');
});
test('interface omits promotional footer and boilerplate',()=>{
  const html=fs.readFileSync(__dirname+'/index.html','utf8');
  assert.doesNotMatch(html, /<footer>|Samsung Galaxy integration planned/);
});

test('LiveKit connection does not imply Gemini connection; provider lifecycle is separate',async()=>{
  const h=harness(); await h.elements.start.onclick();
  assert.equal(h.elements.livekitStatus.textContent,'LiveKit · connected');
  assert.equal(h.elements.geminiStatus.textContent,'Gemini · waiting for agent');
  h.setBatch([{event:'agent_ready'}, {event:'gemini_connection',state:'connected'}]);
  await h.intervals[2]();
  assert.equal(h.elements.geminiStatus.textContent,'Gemini · connected');
  h.setBatch([{event:'gemini_connection',state:'reconnecting'}]); await h.intervals[2]();
  assert.equal(h.elements.geminiStatus.textContent,'Gemini · reconnecting');
  await h.elements.end.onclick();
  assert.equal(h.elements.livekitStatus.textContent,'LiveKit · disconnected');
  assert.equal(h.elements.geminiStatus.textContent,'Gemini · disconnected');
});
test('native transcription streams show user and agent chunks before final conversation',async()=>{
  const h=harness(); await h.elements.start.onclick();
  const stream=(id,chunks)=>({info:{id,attributes:{'lk.segment_id':id}},async *[Symbol.asyncIterator](){for(const chunk of chunks) yield chunk;}});
  await h.streams['lk.transcription'](stream('u1',['Hello there']),{identity:'user-1'});
  assert.equal(h.elements.partial.textContent,'You · Hello there');
  await h.streams['lk.transcription'](stream('a1',['Hi',' there']),{identity:'agent-1'});
  assert.equal(h.elements.agentPartial.textContent,'Reprise · Hi there');
  h.setBatch([{event:'conversation_item',role:'assistant',text:'Hi there'}]);
  await h.intervals[2](); assert.equal(h.elements.agentPartial.textContent,'');
  await h.elements.end.onclick();
  await h.streams['lk.transcription'](stream('late',['stale']),{identity:'agent-1'});
  assert.equal(h.elements.agentPartial.textContent,'');
});

test('voice agent departure clears Gemini health while LiveKit stays connected',async()=>{
  const h=harness(); await h.elements.start.onclick();
  h.setBatch([{event:'gemini_connection',state:'connected'}]); await h.intervals[2]();
  h.handlers.participantLeft({isAgent:true});
  assert.equal(h.elements.geminiStatus.textContent,'Gemini · disconnected');
  assert.equal(h.elements.livekitStatus.textContent,'LiveKit · connected');
  assert.equal(h.elements.confirmPlan.disabled,true);
});

test('conversation transcripts and action events go to separate chronological panels',async()=>{
  const h=harness(); await h.elements.start.onclick();
  const before=h.elements.activity.children.length;
  h.setBatch([{event:'conversation_item',role:'user',text:'Move coffee to six'},
    {event:'tool_dispatch',function:'execute_plan',args:{plan_id:'p'}},
    {event:'conversation_item',role:'assistant',text:'Updated coffee to six.'}]);
  await h.intervals[2]();
  assert.equal(h.elements.conversation.children.length,2);
  assert.equal(h.elements.activity.children.length,before+1);
  assert.equal(h.elements.conversation.children[0].className,'entry user');
  assert.equal(h.elements.conversation.children[1].className,'entry assistant');
});
test('personal plan has no confirmation button; actual updates and undo are labeled',async()=>{
  const h=harness(); await h.elements.start.onclick();
  h.setBatch([{event:'plan_updated',plan:{plan_id:'p',title:'Coffee',status:'completed',requires_confirmation:false,
    actions:[{kind:'calendar',title:'Coffee',start:'2030-10-01T18:00:00+05:30',end:'2030-10-01T18:30:00+05:30',status:'completed',result:'updated',google_id:'real-id'}]}}]);
  await h.intervals[2]();
  assert.equal(h.elements.confirmPlan.hidden,true);
  assert.equal(h.elements.planActions.children[0].children[1].textContent,'Updated');
  assert.match(describeEvent({event:'plan_updated',plan:{title:'Coffee',status:'completed',actions:[{title:'Coffee',status:'completed',result:'undone'}]}}).text,/Undone/);
});

test('latest action view uses verified current values while keeping original receipts',async()=>{
  const h=harness(); await h.elements.start.onclick();
  h.setBatch([{event:'plan_updated',plan:{plan_id:'p',title:'Task',status:'completed',requires_confirmation:false,
    actions:[{kind:'task',title:'Old title',status:'completed',result:'completed',resource_result:'updated',google_id:'id',current_values:{title:'Corrected title',notes:'New notes'}}]}}]);
  await h.intervals[2]();
  const row=h.elements.planActions.children[0];
  assert.equal(row.children[1].textContent,'Updated');
  assert.equal(row.children[2].textContent,'Corrected title');
});

test('retrieved tasks and events are readable action rows, not conversation transcripts',async()=>{
  const event={event:'workspace_items',resource_kind:'task',items:[
    {number:1,id:'task-A',title:'Review',due_date:'2030-10-02',task_status:'completed',notes:'Check figures'},
    {number:2,id:'task-B',title:'Collect',due_date:'',task_status:'needsAction'}],more_available:true};
  const entry=describeEvent(event);
  assert.match(entry.text,/Review/); assert.match(entry.text,/task-A/);
  assert.match(entry.text,/2030-10-02/); assert.match(entry.text,/Complete/);
  assert.match(entry.text,/Collect/); assert.match(entry.text,/More available/);
  const h=harness(); await h.elements.start.onclick(); h.setBatch([event]); await h.intervals[2]();
  assert.equal(h.elements.conversation.children.length,0);
  assert.match(h.elements.activity.children.at(-1).children[1].textContent,/Review/);
  const calendar=describeEvent({event:'workspace_items',kind:'calendar',items:[{id:'e',title:'Coffee',start:'2030-10-02T18:00:00+05:30',end:'2030-10-02T18:30:00+05:30',reminders:{useDefault:false,overrides:[{method:'popup',minutes:20}]}}]});
  assert.match(calendar.text,/18:00/); assert.match(calendar.text,/popup.*20/);
});

test('local planner uses text transport, accepts correction while processing, and has no microphone',async()=>{
  const h=harness();
  h.elements.planner.value='local';
  await h.elements.start.onclick();
  assert.match(h.requests.find(r=>r.url.startsWith('/session')).url,/planner=local/);
  assert.equal(h.elements.mute.disabled,true);
  assert.match(h.elements.status.textContent,/Text/);
  h.elements.text.value='Add a task';
  await h.elements.chat.onsubmit({preventDefault(){}});
  h.elements.text.value='Actually cancel it';
  await h.elements.chat.onsubmit({preventDefault(){}});
  const messages=h.requests.filter(r=>r.url.includes('/local/room-1/message'));
  assert.equal(messages.length,2);
  assert.equal(JSON.parse(messages[1].options.body).text,'Actually cancel it');
  h.setBatch([{event:'conversation_item',role:'user',text:'Actually cancel it'},
    {event:'tool_superseded',function:'execute_plan'}]);
  await h.intervals[2]();
  assert.equal(h.elements.conversation.children.length,1);
  assert.match(h.elements.activity.children.at(-1).children[0].children[0].textContent,/superseded/);
  await h.elements.end.onclick();
  assert.ok(h.requests.some(r=>r.url==='/local/room-1/end'));
});

test('local End retains event polling until in-flight receipt and session_closed',async()=>{
  const h=harness(); h.elements.planner.value='local'; await h.elements.start.onclick();
  await h.elements.end.onclick();
  assert.equal(h.elements.status.textContent,'Ending');
  h.setBatch([{event:'tool_executed',function:'execute_plan',outcome:'success'}, {event:'session_closed'}]);
  await h.intervals[2]();
  assert.equal(h.elements.status.textContent,'Disconnected');
  assert.ok(h.elements.activity.children.some(row=>row.children[0].children[0].textContent.includes('Completed')));
});

test('phone and conversation share real transcript and session state',async()=>{
  const h=harness(); await h.elements.start.onclick();
  assert.equal(h.elements.phoneStatus.textContent,'Connected');
  h.setBatch([{event:'conversation_item',role:'user',text:'Move the review to six'},
    {event:'agent_state',state:'thinking'}]);
  await h.intervals[2]();
  assert.equal(h.elements.phoneTranscript.textContent,'Move the review to six');
  assert.equal(h.elements.phoneSpeaker.textContent,'You');
  assert.equal(h.elements.phoneStatus.textContent,'Processing');
  assert.equal(h.elements.phoneSurface['data-state'],'thinking');
  assert.equal(h.elements.conversation.children.length,1);
  await h.elements.end.onclick();
  assert.equal(h.elements.phoneStatus.textContent,'Disconnected');
});
test('timeline distinguishes interrupted speech, superseded work and unknown writes',async()=>{
  const {timelineState}=require('./app.js');
  assert.equal(timelineState({event:'tool_caller_detached'}),'interrupted');
  assert.equal(timelineState({event:'tool_superseded'}),'cancelled');
  assert.equal(timelineState({event:'tool_outcome_unknown'}),'unknown');
  assert.equal(timelineState({event:'tool_dispatch'}),'running');
  assert.equal(timelineState({event:'tool_raw_proposed'}),'pending');
  const h=harness(); await h.elements.start.onclick();
  h.setBatch([{event_id:'r1',event:'tool_dispatch',proposal_id:'p1',function:'execute_plan'},
    {event_id:'r2',event:'tool_caller_detached',proposal_id:'p1',function:'execute_plan'},
    {event_id:'r3',event:'tool_outcome_unknown',proposal_id:'p1',function:'execute_plan'}]);
  await h.intervals[2]();
  assert.equal(h.elements.activity.children.at(-2)['data-state'],'interrupted');
  assert.equal(h.elements.activity.children.at(-1)['data-state'],'unknown');
  assert.equal(h.elements.phoneActivityState.textContent,'Outcome unknown');
  assert.ok(h.elements.activity.children.some(row=>row['data-state']==='running'));
});
test('correction is visible without pretending a dispatched action was cancelled',async()=>{
  const h=harness(); await h.elements.start.onclick();
  h.setBatch([{event:'tool_dispatch',function:'execute_plan'},
    {event:'intent_revised',reason:'time corrected'},
    {event:'tool_superseded',function:'prepare_plan',reason:'newer revision'}]);
  await h.intervals[2]();
  assert.equal(h.elements.phoneActivityState.textContent,'Superseded');
  assert.equal(h.elements.activity.children.at(-3)['data-state'],'running');
  assert.equal(h.elements.activity.children.at(-2)['data-state'],'corrected');
  assert.equal(h.elements.activity.children.at(-1)['data-state'],'cancelled');
  assert.match(h.elements.phoneActivityText.textContent,/newer revision/);
});
test('phone audio control changes actual attached audio and resets with session',async()=>{
  const h=harness(); await h.elements.start.onclick();
  assert.equal(h.elements.speaker.disabled,false);
  await h.elements.speaker.onclick();
  assert.equal(h.elements.speaker['aria-pressed'],'true');
  assert.equal(h.elements.speaker.textContent,'Audio off');
  await h.elements.speaker.onclick();
  assert.equal(h.elements.speaker['aria-pressed'],'false');
  await h.elements.end.onclick();
  assert.equal(h.elements.speaker.disabled,true);
});
test('lookup success and plan preparation remain different from a completed mutation',()=>{
  const {timelineState}=require('./app.js');
  assert.equal(timelineState({event:'tool_executed',function:'prepare_resource_change',outcome:'success'}),'pending');
  assert.match(describeEvent({event:'tool_executed',function:'prepare_resource_change',outcome:'success'}).title,/Prepared/);
  assert.equal(timelineState({event:'tool_executed',function:'execute_plan',outcome:'approval_required'}),'pending');
  assert.equal(timelineState({event:'tool_executed',function:'execute_plan',outcome:'success'}),'completed');
});

test('withheld results and revoked consent do not masquerade as cancelled operations',()=>{
  const {timelineState}=require('./app.js');
  assert.equal(timelineState({event:'stale_result_suppressed'}),'info');
  assert.equal(timelineState({event:'approval_revoked'}),'pending');
  assert.equal(timelineState({event:'tool_caller_detached'}),'interrupted');
});
test('a resource ID is not itself proof that an unknown operation succeeded',async()=>{
  const h=harness(); await h.elements.start.onclick();
  h.setBatch([{event:'plan_updated',plan:{plan_id:'p',title:'Review',status:'unknown',requires_confirmation:false,
    actions:[{kind:'task',title:'Review',status:'unknown',google_id:'known-id'}]}}]);
  await h.intervals[2]();
  const row=h.elements.planActions.children[0];
  assert.ok(row.children.some(el=>el.textContent.includes('Resource ID · known-id')));
  assert.ok(!row.children.some(el=>el.textContent.includes('Google confirmed')));
});

test('phone retains the correction marker alongside the replacement plan',async()=>{
  const h=harness(); await h.elements.start.onclick();
  h.setBatch([{event:'tool_superseded',function:'prepare_plan'},
    {event:'plan_updated',plan:{plan_id:'replacement',title:'Review at six',status:'prepared',requires_confirmation:false,actions:[]}}]);
  await h.intervals[2]();
  assert.equal(h.elements.phoneRevision.hidden,false);
  assert.equal(h.elements.phoneRevision.textContent,'Earlier action superseded');
  assert.equal(h.elements.phoneActivity.hidden,true);
  assert.equal(h.elements.planCard.hidden,false);
});

test('completed work never retains an interruption marker claiming its outcome is pending',async()=>{
  const h=harness(); await h.elements.start.onclick();
  h.setBatch([{event:'tool_caller_detached',proposal_id:'old-response'},
    {event:'plan_updated',plan:{plan_id:'p',title:'Review',status:'completed',requires_confirmation:false,actions:[]}}]);
  await h.intervals[2]();
  assert.match(h.elements.phoneRevision.textContent,/interrupted/);
  assert.doesNotMatch(h.elements.phoneRevision.textContent,/pending/);
});
test('late audio resume cannot toggle playback in a replacement session',async()=>{
  const h=harness({deferAudioResume:true}); await h.elements.start.onclick();
  await h.elements.speaker.onclick();
  const oldResume=h.elements.speaker.onclick();
  await h.elements.end.onclick(); await h.elements.start.onclick();
  h.resumeAudio(); await oldResume;
  assert.equal(h.elements.speaker.textContent,'Audio on');
  assert.equal(h.elements.speaker['aria-pressed'],'false');
  assert.equal(h.elements.speaker.disabled,false);
});
test('timeline navigation honors reduced motion while keeping horizontal navigation',()=>{
  const h=harness({reducedMotion:true});
  h.elements.timelineNext.onclick();
  assert.equal(h.elements.activity.lastScroll.behavior,'auto');
  assert.ok(h.elements.activity.lastScroll.left>0);
  h.elements.timelineBack.onclick();
  assert.ok(h.elements.activity.lastScroll.left<0);
});

test('audio button mutes actual tracks, including tracks arriving while muted',async()=>{
  const h=harness(); await h.elements.start.onclick();
  const first={dataset:{},remove(){this.removed=true;}};
  h.handlers.track({kind:'audio',attach:()=>first});
  assert.equal(first.muted,false);
  await h.elements.speaker.onclick(); assert.equal(first.muted,true);
  const second={dataset:{},remove(){this.removed=true;}};
  h.handlers.track({kind:'audio',attach:()=>second}); assert.equal(second.muted,true);
  await h.elements.speaker.onclick();
  assert.equal(first.muted,false); assert.equal(second.muted,false);
  await h.elements.end.onclick();
  assert.equal(first.removed,true); assert.equal(second.removed,true);
});

test('scripted workflows stay outside live session, tool dispatch and approval endpoints',async()=>{
 const h=harness();h.elements.experience.value='scripted';h.elements.experience.onchange();
 h.elements.demoWorkflow.value='calendar';h.elements.demoWorkflow.onchange();
 const frames=require('./phone.js').workflows.calendar.frames;
 for(let i=0;i<frames.length;i++)h.elements.demoNext.onclick();
 assert.equal(h.elements.planCard.hidden,true);
 assert.equal(h.elements.confirmPlan.disabled,true);
 await h.elements.confirmPlan.onclick();
 assert.ok(!h.requests.some(r=>r.options?.method==='POST'));
 assert.ok(h.elements.conversation.children.every(row=>row['data-source']==='scripted'));
 assert.ok(h.elements.activity.children.every(row=>row['data-source']==='scripted'));
 assert.equal(h.elements.livekitStatus.textContent,'LiveKit · not used');
 assert.equal(h.elements.phoneSurface['data-experience'],'scripted');
});
test('switching out of playback prevents scheduled frames affecting live mode',()=>{
 const h=harness();h.elements.experience.value='scripted';h.elements.experience.onchange();
 h.elements.demoPlay.onclick();const oldTick=h.intervals.at(-1);
 h.elements.experience.value='live';h.elements.experience.onchange();
 const count=h.elements.conversation.children.length;oldTick();
 assert.equal(h.elements.conversation.children.length,count);
 assert.equal(h.elements.start.disabled,false);
 assert.equal(h.elements.planCard.hidden,true);
 assert.equal(h.elements.phoneSurface['data-experience'],'live');
});
test('live sessions lock scripted controls until the session ends',async()=>{
 const h=harness();await h.elements.start.onclick();
 assert.equal(h.elements.experience.disabled,true);
 h.elements.experience.value='scripted';h.elements.experience.onchange();
 assert.equal(h.elements.start.disabled,true);
 await h.elements.end.onclick();
 assert.equal(h.elements.experience.disabled,false);
});

test('timeline keeps following the latest action when a plan opens and narrows the rail',async()=>{
 const h=harness();await h.elements.start.onclick();
 const rail=h.elements.activity;
 rail.scrollWidth=1500;rail.clientWidth=600;rail.scrollLeft=900;
 Object.defineProperty(h.elements.planCard,'hidden',{get:()=>false,set:value=>{if(!value)rail.clientWidth=300;},configurable:true});
 h.setBatch([{event:'plan_updated',plan:{plan_id:'p',title:'Review',status:'prepared',requires_confirmation:false,actions:[]}}]);
 await h.intervals[2]();
 assert.ok(rail.scrollLeft+rail.clientWidth>=rail.scrollWidth);
});
test('timeline does not steal position when a user is reviewing older actions',async()=>{
 const h=harness();await h.elements.start.onclick();
 const rail=h.elements.activity;
 rail.scrollWidth=1500;rail.clientWidth=600;rail.scrollLeft=200;
 h.setBatch([{event:'tool_dispatch',function:'search_flights'}]);await h.intervals[2]();
 assert.equal(rail.scrollLeft,200);
});

test('execution strip uses short captions while keeping original arguments in history',()=>{
 const {timelineCaption}=require('./app.js');
 assert.equal(timelineCaption({event:'tool_raw_proposed',function:'search_flights',args:{destination:'Mumbai'}}),'Mumbai');
 assert.equal(timelineCaption({event:'tool_superseded',function:'book_flight'}),'Booking cancelled');
 assert.equal(timelineCaption({event:'demo_result',text:'No booking made.'}),'No booking made');
});
test('superseding a pending proposal visually drops its original step',async()=>{
 const h=harness();await h.elements.start.onclick();
 h.setBatch([{event:'tool_raw_proposed',proposal_id:'old-search',function:'search_flights',args:{destination:'Bengaluru'}},
 {event:'tool_superseded',proposal_id:'old-search',function:'search_flights'}]);await h.intervals[2]();
 const original=h.elements.activity.children.find(row=>row['data-proposal-id']==='old-search');
 assert.equal(original['data-state'],'cancelled');
 assert.equal(original['data-superseded'],'true');
 assert.deepEqual(original.children[1].textContent,'destination: Bengaluru');
});
test('speech interruption and supersession cannot retroactively cancel a dispatched action',async()=>{
 const h=harness();await h.elements.start.onclick();
 h.setBatch([{event:'tool_raw_proposed',proposal_id:'write',function:'execute_plan'},
 {event:'tool_dispatch',proposal_id:'write',function:'execute_plan'},
 {event:'tool_caller_detached',proposal_id:'write',function:'execute_plan'},
 {event:'tool_superseded',proposal_id:'write',function:'execute_plan'}]);await h.intervals[2]();
 const original=h.elements.activity.children.find(row=>row['data-proposal-id']==='write');
 assert.notEqual(original['data-superseded'],'true');
});
test('a correction emphasizes the latest user line without discarding transcript history',async()=>{
 const h=harness();await h.elements.start.onclick();
 h.setBatch([{event:'conversation_item',role:'user',text:'Bengaluru'},
 {event:'conversation_item',role:'assistant',text:'Searching—',interrupted:true},
 {event:'conversation_item',role:'user',text:'Actually Mumbai'},
 {event:'intent_revised',reason:'destination changed'}]);await h.intervals[2]();
 assert.equal(h.elements.conversation.children.length,3);
 assert.equal(h.elements.conversation.children[0]['data-current'],'false');
 assert.equal(h.elements.conversation.children[2]['data-current'],'true');
 assert.equal(h.elements.conversation.children[2]['data-correction'],'true');
});

test('opening a simulated phone app selects Demo instead of posing as live account activity',()=>{
 const h=harness();
 const apps=h.elements.galaxyApp.children.find(node=>node.className==='home-apps');
 apps.children.find(node=>node['aria-label']==='Open Gmail preview').onclick();
 assert.equal(h.elements.experience.value,'scripted');
 assert.equal(h.elements.galaxyApp['data-app'],'gmail');
 assert.ok(!h.requests.some(r=>r.options?.method==='POST'));
});
test('an active live session cannot be replaced with a synthetic phone app',async()=>{
 const h=harness();await h.elements.start.onclick();
 const apps=h.elements.galaxyApp.children.find(node=>node.className==='home-apps');
 apps.children[0].onclick();
 assert.equal(h.elements.experience.value,'live');
 assert.equal(h.elements.galaxyApp['data-app'],'home');
});

test('phone navigation is blocked during a pending live connection',async()=>{
 const h=harness({deferSession:true});const starting=h.elements.start.onclick();
 const apps=h.elements.galaxyApp.children.find(node=>node.className==='home-apps');apps.children[0].onclick();
 assert.equal(h.elements.experience.value,'live');
 assert.equal(h.elements.galaxyApp['data-app'],'home');
 h.resumeSession();await starting;
 assert.equal(h.elements.galaxyApp['data-app'],'home');
});
test('switching Live hides fictional history while preserving its data for Demo',()=>{
 const h=harness();h.elements.experience.value='scripted';h.elements.experience.onchange();h.elements.demoNext.onclick();
 const fictional=h.elements.conversation.children[0];
 h.elements.experience.value='live';h.elements.experience.onchange();
 assert.equal(fictional.hidden,true);
 assert.equal(fictional['data-source'],'scripted');
 assert.equal(h.elements.conversation.children.length,1);
});
test('superseded mutations are not described as dropped searches',()=>{
 const {timelineCaption}=require('./app.js');
 assert.equal(timelineCaption({event:'tool_superseded',function:'execute_plan'}),'Action superseded');
});

// Final-script attribution coverage; not executed during the no-test presentation pass.
test('scripted conversation shows Naithick and Karthick without changing live speaker labels',()=>{
 assert.equal(describeEvent({event:'conversation_item',role:'user',speaker:'Karthick',simulated:true,text:'Hello'}).title,'Karthick');
 assert.equal(describeEvent({event:'conversation_item',role:'user',speaker:'Karthick',text:'Hello'}).title,'You');
});

test('scripted app navigation shows a call indicator, and Live clears it',()=>{
 const h=harness();h.elements.experience.value='scripted';h.elements.experience.onchange();
 for(let i=0;i<3;i++)h.elements.demoNext.onclick();
 assert.equal(h.elements.phoneOngoingCall.hidden,false);assert.equal(h.elements.phoneClock.hidden,true);assert.equal(h.elements.phoneCallTime.textContent,'00:10');
 h.elements.phoneOngoingCall.onclick();assert.equal(h.elements.galaxyApp['data-app'],'call');assert.equal(h.elements.phoneOngoingCall.hidden,true);
 h.elements.experience.value='live';h.elements.experience.onchange();assert.equal(h.elements.phoneOngoingCall.hidden,true);assert.equal(h.elements.phoneClock.hidden,false);
 assert.ok(!h.requests.some(r=>r.options?.method==='POST'));
});
test('superseded pending proposal and pause collapse without removing their inspectable history',()=>{
 const h=harness();h.elements.experience.value='scripted';h.elements.experience.onchange();h.elements.demoWorkflow.value='calendar';h.elements.demoWorkflow.onchange();
 for(let i=0;i<7;i++)h.elements.demoNext.onclick();
 const entries=h.elements.activity.children.filter(e=>e['data-proposal-id']==='demo-coffee-five');
 assert.equal(entries.length,3);assert.ok(entries.every(e=>e['data-superseded']==='true'));
 assert.ok(entries.every(e=>e.hidden!==true));
 assert.ok(entries.some(e=>e.children[0].children[2].textContent==='Paused'));
 h.elements.inspectActions.checked=true;h.elements.inspectActions.onchange();assert.equal(h.elements.activity.children.filter(e=>e['data-proposal-id']==='demo-coffee-five').length,3);
});
test('calendar timeline separates checks, unsaved plans, saving and one confirmed write',()=>{
 const h=harness();h.elements.experience.value='scripted';h.elements.experience.onchange();h.elements.demoWorkflow.value='calendar';h.elements.demoWorkflow.onchange();
 const rows=()=>h.elements.activity.children.filter(e=>!e.hidden&&e['data-quiet']!=='true');
 const labels=()=>rows().map(e=>e.children[0].children[2].textContent);
 for(let i=0;i<5;i++)h.elements.demoNext.onclick();
 assert.ok(labels().includes('5 PM · check'));assert.ok(labels().includes('5 PM · not saved'));
 const oldPlan=rows().find(e=>e['data-proposal-id']==='demo-coffee-five');assert.equal(oldPlan['data-state'],'pending');
 for(let i=0;i<2;i++)h.elements.demoNext.onclick();
 assert.equal(oldPlan['data-superseded'],'true');assert.ok(labels().includes('6 PM · pending'));
 h.elements.demoNext.onclick();assert.ok(labels().includes('6 PM · available'));
 h.elements.demoNext.onclick();
 const plan=rows().find(e=>e['data-proposal-id']==='demo-coffee-six');assert.equal(plan['data-state'],'running');
 assert.ok(labels().includes('6 PM · saving'));assert.ok(!labels().some(s=>s.includes('1 write')));
 h.elements.demoNext.onclick();
 assert.equal(plan['data-quiet'],'true');assert.ok(labels().includes('6 PM saved · 1 write'));
 const writes=h.elements.activity.children.filter(e=>e['data-event']==='tool_dispatch'&&e['data-proposal-id']==='demo-coffee-six');assert.equal(writes.length,1);
 assert.ok(!h.elements.activity.children.some(e=>e['data-event']==='tool_dispatch'&&e['data-proposal-id']==='demo-coffee-five'));
 assert.ok(!h.elements.activity.children.some(e=>e.children[0].children[2].textContent==='Flight details'));
});
test('calendar replay and mid-scene restart isolate mock receipts and old timeline references',()=>{
 const h=harness();h.elements.experience.value='scripted';h.elements.experience.onchange();h.elements.demoWorkflow.value='calendar';h.elements.demoWorkflow.onchange();
 for(const stop of [5,7,9,12,12]){
  const old=h.elements.activity.children.slice();const before=JSON.stringify(old);
  for(let i=0;i<stop;i++)h.elements.demoNext.onclick();
  assert.equal(JSON.stringify(old),before,'a replay must not mutate previous-run history');
  const visible=h.elements.activity.children.filter(e=>!e.hidden);
  assert.equal(visible.filter(e=>e['data-event']==='tool_dispatch'&&e['data-proposal-id']==='demo-coffee-six').length,stop>=9?1:0);
  assert.equal(visible.filter(e=>e['data-event']==='tool_executed'&&e['data-proposal-id']==='demo-coffee-six').length,stop>=10?1:0);
  h.elements.demoRestart.onclick();assert.equal(h.elements.demoProgress.textContent,'0 / 12');
  assert.ok(h.elements.activity.children.every(e=>e.hidden));assert.equal(h.elements.galaxyApp['data-app'],'home');
 }
 // Play after the final frame automatically resets; stale callbacks cannot advance a restarted run.
 for(let i=0;i<12;i++)h.elements.demoNext.onclick();h.elements.demoPlay.onclick();
 assert.equal(h.elements.demoProgress.textContent,'1 / 12');const oldTick=h.intervals.at(-1);
 for(let i=0;i<11;i++)h.elements.demoNext.onclick();
 assert.equal(h.elements.activity.children.filter(e=>!e.hidden&&e['data-event']==='tool_executed'&&e['data-proposal-id']==='demo-coffee-six').length,1);
 assert.ok(h.elements.activity.children.filter(e=>!e.hidden).some(e=>e.children[0].children[2].textContent==='6 PM saved · 1 write'));
 h.elements.demoRestart.onclick();oldTick();assert.equal(h.elements.demoProgress.textContent,'0 / 12');
 assert.ok(!h.requests.some(r=>r.options?.method==='POST'));
});
