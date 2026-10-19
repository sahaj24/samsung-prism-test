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
function harness({rejectEarlyConfiguration=false} = {}) {
  class Element {
    constructor() {this.children=[];this.textContent='';this.disabled=false;this.value='';this.scrollHeight=0;this.scrollTop=0;this.clientHeight=400;}
    append(...items) {this.children.push(...items);}
    replaceChildren(...items) {this.children=items;}
    querySelector() {return null;}
    setAttribute(key,value) {this[key]=value;}
  }
  const elements=Object.fromEntries([...fs.readFileSync(__dirname+'/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  elements.mode.value='productivity';
  const intervals=[], requests=[]; let batch=[];
  const handlers={}, streams={};
  const participant={identity:'user-1',setMicrophoneEnabled:async()=>{},sendText:async()=>{}};
  class Room {constructor(options){this.options=options;this.localParticipant=participant;} on(name,fn){handlers[name]=fn;} registerTextStreamHandler(topic,fn){streams[topic]=fn;} async connect(){if(rejectEarlyConfiguration && this.options.singlePeerConnection !== false) throw new Error("Failed to execute 'setConfiguration' on 'RTCPeerConnection'");} async startAudio(){} async disconnect(){}}
  const context={document:{getElementById:id=>{assert.ok(elements[id],id);return elements[id];},createElement:()=>new Element(),querySelectorAll:()=>[],body:new Element()},
    LivekitClient:{Room,RoomEvent:{TrackSubscribed:"track",Disconnected:"disconnected",ConnectionStateChanged:"connection",AudioPlaybackStatusChanged:"playback",ParticipantDisconnected:"participantLeft"},Track:{Kind:{Audio:"audio"}}},window:{addEventListener(){}},setInterval:fn=>{intervals.push(fn);return intervals.length;},clearInterval(){},Date,Intl,Set,AbortSignal,
    fetch:async (url,options)=>{requests.push({url,options});return {ok:true,json:async()=>url==='/google/status'?{connected:true,time_zone:'Asia/Kolkata'}:url.startsWith('/session')?{room:'room-1',url:'mock',token:'mock'}:url.startsWith('/events')?{events:batch,cursor:batch.length}:{plan:{plan_id:'p',title:'Plan',status:'approved',actions:[]}}};}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/app.js','utf8'),context);
  return {elements,intervals,requests,handlers,streams,setBatch:value=>batch=value};
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
