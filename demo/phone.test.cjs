const {test}=require('node:test');
const assert=require('node:assert/strict');
const {workflows,createPlayback,createPhone}=require('./phone.js');
const eventsOf=key=>workflows[key].frames.flatMap(frame=>frame.events);
const dispatches=key=>eventsOf(key).filter(e=>e.event==='tool_dispatch');

// Updated for the final authored script; deliberately not executed in this pass.
test('the three scenes keep their 30/40/30 second timing and fixed full-story order',()=>{
 assert.deepEqual(Object.keys(workflows),['booking','calendar','draft','story']);
 for(const [key,seconds] of [['booking',30],['calendar',40],['draft',30],['story',100]])
  assert.equal(workflows[key].frames.reduce((sum,f)=>sum+f.durationMs,0),seconds*1000);
 assert.deepEqual(workflows.story.frames,[...workflows.booking.frames,...workflows.calendar.frames,...workflows.draft.frames]);
});
test('PNR comes from the final synthetic ticket and returns to the real-looking call',()=>{
 const frames=workflows.booking.frames;
 assert.equal(frames[0].screen.activation,'standby');
 assert.equal(frames[1].screen.activation,'active');
 const email=frames.find(f=>f.screen.view==='message').screen.item;
 for(const value of ['K7P2XQ','6E-5423','Chennai','Saturday, 7:10 PM'])assert.ok(email.body.includes(value));
 const returned=frames.find(f=>f.screen.pnr);
 assert.equal(returned.screen.app,'call');assert.equal(returned.screen.pnr.id,email.id);
 assert.equal(frames.at(-1).screen.activation,'standby');
 assert.ok(eventsOf('booking').some(e=>e.speaker==='Naithick'&&e.text==="Got it. It's K, 7, P, 2, X, Q."));
});
test('ordinary conversation stays silent; only Naithick activates and authorizes operations',()=>{
 for(const key of ['booking','calendar','draft']){
  const frames=workflows[key].frames;
  assert.equal(frames[0].events[0].speaker,'Karthick');
  assert.equal(frames[0].events.filter(e=>e.event==='tool_dispatch').length,0);
  assert.ok(frames[1].events.some(e=>e.speaker==='Naithick'&&/Hey Reprise/.test(e.text)));
  assert.equal(frames.at(-1).screen.activation,'standby');
 }
 for(const event of eventsOf('story')){
  if(event.event==='tool_dispatch'||event.event==='tool_raw_proposed')assert.equal(event.authorized_by,'Naithick');
  assert.notEqual(event.role,'assistant','Do not add spoken Reprise lines to this script');
 }
});
test('Karthick pauses but does not change or dispatch the pending 5 PM event',()=>{
 const pause=workflows.calendar.frames.find(f=>f.screen.activation==='paused');
 assert.equal(pause.screen.time,'5:00 PM');
 assert.ok(pause.events.some(e=>e.speaker==='Karthick'&&e.event==='demo_activation'));
 assert.equal(pause.events.filter(e=>['tool_dispatch','intent_revised'].includes(e.event)).length,0);
 const revision=workflows.calendar.frames.find(f=>f.screen.previousTime);
 assert.equal(revision.durationMs,2000);assert.equal(revision.screen.previousTime,'5:00 PM');assert.equal(revision.screen.time,'6:00 PM');
 assert.ok(revision.events.some(e=>e.event==='conversation_item'&&e.speaker==='Naithick'&&!/Hey Reprise/.test(e.text)));
 assert.ok(revision.events.some(e=>e.event==='tool_superseded'&&e.proposal_id==='demo-coffee-five'));
});
test('only 6 PM is saved once, after its availability check, with one synthetic resource ID',()=>{
 const calls=dispatches('calendar');const writes=calls.filter(e=>e.function==='create_calendar_event');
 assert.equal(writes.length,1);assert.equal(writes[0].args.time,'6:00 PM');assert.equal(writes[0].args.title,'Coffee with Karthick');
 assert.equal(calls.filter(e=>e.proposal_id==='demo-coffee-five').length,0);
 const events=eventsOf('calendar');
 assert.ok(events.findIndex(e=>e.event==='tool_executed'&&e.proposal_id==='demo-free-six')<events.indexOf(writes[0]));
 const success=events.find(e=>e.event==='tool_executed'&&e.function==='create_calendar_event');
 assert.equal(success.resource_id,'demo-coffee');assert.equal(success.outcome,'success');
 assert.ok(events.some(e=>e.event==='demo_result'&&e.resource_id===success.resource_id&&e.text==='Coffee with Karthick · Sunday, 6 PM'));
});
test('Contacts supplies the exact draft recipient and the final message is saved without sending',()=>{
 const frames=workflows.draft.frames;const contact=frames.find(f=>f.screen.app==='contacts'&&f.screen.view==='detail').screen.item;
 const compose=frames.find(f=>f.screen.view==='compose').screen.item;
 assert.equal(contact.name,'Rajesh');assert.equal(contact.label,'Manager');assert.equal(compose.to.id,contact.id);
 assert.equal(compose.subject,'Leave request for Monday');
 assert.equal(compose.body,"Hi Rajesh,\n\nI'd like to request leave on Monday as I'll be returning late from a trip on Sunday. Please let me know if that works.\n\nThanks,\nNaithick");
 const writes=dispatches('draft').filter(e=>e.function==='create_gmail_draft');assert.equal(writes.length,1);assert.equal(writes[0].args.to,contact.email);
 assert.ok(!eventsOf('story').some(e=>/send_email|send_gmail|book_flight|search_flights/.test(e.function||'')));
 assert.ok(frames.some(f=>f.screen.app==='call'&&f.screen.confirmation==='Draft saved'));
});
test('all authored events retain simulation metadata and never manufacture actual API receipts',()=>{
 for(const frame of workflows.story.frames)for(const e of frame.events){
  assert.equal(e.simulated,true);assert.equal(e.google_id,undefined);assert.equal(e.event_id,undefined);assert.notEqual(e.event,'external_api_completed');
  if(e.resource_id)assert.match(e.resource_id,/^demo-/);
 }
});
test('playback is frozen, bounded and resettable without retaining the old scene cursor',()=>{
 const original=JSON.stringify(workflows),played=[];const player=createPlayback(f=>played.push(f));
 player.reset('calendar');player.advance();assert.equal(player.durationMs,5000);
 player.reset('draft');while(player.advance()){}assert.equal(player.position,workflows.draft.frames.length);
 const count=played.length;assert.equal(player.advance(),false);assert.equal(played.length,count);
 player.reset('story');while(player.advance()){}assert.equal(JSON.stringify(workflows),original);
 assert.ok(Object.isFrozen(workflows.calendar.frames[6].screen));assert.throws(()=>player.reset('__proto__'),/Unknown demo workflow/);
});
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.attributes={};}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}
 setAttribute(k,v){this.attributes[k]=v;}
}
const descendants=n=>[n,...n.children.flatMap(descendants)];
const content=n=>descendants(n).map(e=>e.textContent).join(' ');
const makePhone=(guard=()=>{})=>{const root=new Element();return {root,phone:createPhone(root,{createElement:tag=>new Element(tag)},guard)};};

test('every scene renders as One UI 7 with no repeated simulation badge or added send action',()=>{
 const {root,phone}=makePhone();
 for(const frame of workflows.story.frames){phone.show(frame.screen);assert.equal(root.attributes['data-source'],'simulated');assert.equal(root.attributes['data-oneui'],'7');assert.doesNotMatch(content(root),/synthetic|scripted|preview/i);}
});
test('the actual call is with Karthick and keeps the Samsung six-control tray',()=>{
 const {root,phone}=makePhone();phone.show(workflows.booking.frames[0].screen);
 assert.match(content(root),/Karthick/);
 const controls=descendants(root).filter(e=>e.className==='native-call-control');assert.equal(controls.length,6);assert.ok(controls.every(e=>e.disabled));
 for(const label of ['Add call','Message','Bluetooth','Speaker','Mute','Keypad'])assert.ok(controls.some(e=>e.attributes['aria-label']===label));
});
test('standby has no overlay; active and paused scenes have a subtle indicator',()=>{
 const {root,phone}=makePhone();phone.show(workflows.booking.frames[0].screen);assert.ok(!descendants(root).some(e=>e.className==='reprise-indicator'));
 phone.show(workflows.booking.frames[1].screen);assert.ok(descendants(root).some(e=>e.className==='reprise-indicator'));
});
test('the coffee editor is preserved in place across pause, revision and final dispatch',()=>{
 const {root,phone}=makePhone();const frames=workflows.calendar.frames.filter(f=>f.screen.view==='edit');phone.show(frames[0].screen);
 const original=root.children[0];const time=descendants(root).find(e=>e.className==='current-event-time');assert.equal(time.textContent,'5:00 PM');
 for(const frame of frames.slice(1)){
  phone.show(frame.screen);assert.equal(root.children[0],original);assert.equal(root.attributes['data-activation'],frame.screen.activation);
 }
 assert.equal(time.textContent,'6:00 PM');
});
test('calendar preview stays explicitly unsaved until the sole 6 PM write is dispatched',()=>{
 const {root,phone}=makePhone();
 for(const frame of workflows.calendar.frames.filter(f=>f.screen.view==='edit')){
  phone.show(frame.screen);
  const status=descendants(root).find(e=>e.className==='calendar-saving');
  if(['pending','paused','revising'].includes(frame.screen.saveState))assert.equal(status.textContent,'Not saved');
  else if(frame.screen.saveState==='checking')assert.equal(status.textContent,'Rechecking 6 PM…');
  else assert.equal(status.textContent,'Saving…');
 }
});
test('calendar mock writes reset on restart and never include the superseded preview',()=>{
 const player=createPlayback(()=>{});player.reset('calendar');
 for(let run=0;run<3;run++){
  assert.deepEqual(player.calendarWrites,[]);
  for(let i=0;i<8;i++){assert.equal(player.advance(),true);assert.deepEqual(player.calendarWrites,[]);}
  player.advance();assert.equal(player.calendarWrites.length,1);
  assert.equal(player.calendarWrites[0].args.time,'6:00 PM');assert.equal(player.calendarWrites[0].outcome,'running');
  player.advance();assert.equal(player.calendarWrites[0].outcome,'success');assert.equal(player.calendarWrites[0].resource_id,'demo-coffee');
  while(player.advance()){}assert.equal(player.calendarWrites.length,1);
  player.reset('calendar');
 }
 // A restart during an unsaved edit or a dispatched write starts a fresh mock run.
 for(const steps of [5,7,9]){
  for(let i=0;i<steps;i++)player.advance();
  player.reset('calendar');assert.deepEqual(player.calendarWrites,[]);assert.equal(player.position,0);
 }
 player.reset('story');while(player.advance()){}assert.equal(player.calendarWrites.length,1);
 player.reset('draft');while(player.advance()){}assert.deepEqual(player.calendarWrites,[]);
});
test('Gmail compose shows the final message with a disabled send affordance',()=>{
 const {root,phone}=makePhone();phone.show(workflows.draft.frames.find(f=>f.screen.view==='compose').screen);
 assert.match(content(root),/Leave request for Monday/);assert.match(content(root),/I'd like to request leave on Monday/);
 const send=descendants(root).find(e=>e.attributes['aria-label']==='Send');assert.ok(send);assert.equal(send.disabled,true);
});
test('manual app navigation stays local and back preserves the previous screen',()=>{
 const {root,phone}=makePhone();descendants(root).find(e=>e.attributes['aria-label']==='Open Gmail preview').onclick();assert.equal(phone.current.app,'gmail');
 assert.ok(!descendants(root).some(e=>e.attributes['aria-label']==='Open IndiGo email'));
 const search=descendants(root).find(e=>e.className==='native-search gmail-search');search.children.find(e=>e.tag==='input').value='IndiGo Chennai';search.onsubmit({preventDefault(){}});
 descendants(root).find(e=>e.attributes['aria-label']==='Open IndiGo email').onclick();assert.equal(phone.current.view,'message');phone.back();assert.equal(phone.current.view,'search');phone.back();assert.equal(phone.current.view,'inbox');phone.home();assert.equal(phone.current.app,'home');
});
test('blocked navigation cannot bypass the host through search or app icons',()=>{
 const {root,phone}=makePhone(()=>false);descendants(root).find(e=>e.attributes['aria-label']==='Open Calendar preview').onclick();assert.equal(phone.current.app,'home');
 phone.show({app:'gmail',view:'inbox'});const form=descendants(root).find(e=>e.tag==='form');form.children.find(e=>e.tag==='input').value='IndiGo';form.onsubmit({preventDefault(){}});assert.equal(phone.current.view,'inbox');
});

test('all three scenes retain an ongoing call when navigating into other apps',()=>{
 const states=[];const root=new Element();const phone=createPhone(root,{createElement:tag=>new Element(tag)},()=>{},view=>states.push(view));
 for(const frame of workflows.story.frames){phone.show(frame.screen);assert.equal(states.at(-1).inCall,true);assert.equal(states.at(-1).callSeconds,frame.elapsedMs/1000);}
 phone.reset();assert.equal(states.at(-1).inCall,undefined);assert.equal(phone.current.app,'home');
});
test('returning through recents or back cannot rewind the ongoing call clock',()=>{
 const states=[];const root=new Element();const phone=createPhone(root,{createElement:tag=>new Element(tag)},()=>{},s=>states.push(s));
 phone.show(workflows.booking.frames[1].screen);phone.show(workflows.booking.frames[2].screen);phone.setElapsed(21);
 phone.returnToCall();assert.equal(phone.current.app,'call');assert.equal(phone.current.callSeconds,21);assert.match(content(root),/00:21/);
 phone.setElapsed(23);phone.back();assert.equal(phone.current.app,'gmail');assert.equal(phone.current.callSeconds,23);assert.equal(phone.current.inCall,true);
 phone.recents();const card=descendants(root).find(e=>e.attributes['aria-label']==='Return to call');phone.setElapsed(25);card.onclick();assert.equal(phone.current.callSeconds,25);
});
test('ending the simulated call clears its indicator and back does not reactivate it',()=>{
 const {root,phone}=makePhone();phone.show(workflows.booking.frames[0].screen);
 descendants(root).find(e=>e.attributes['aria-label']==='End call').onclick();assert.equal(phone.current.inCall,false);phone.back();assert.equal(phone.current.inCall,false);
});
