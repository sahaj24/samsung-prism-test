/* Scripted One UI 7 presentation. Synthetic data only; no network or device access.
 * Speaker identity and “Hey Reprise” activation are authored playback events,
 * not a microphone wake-word detector. Live mode is unchanged.
 */
const ReprisePhone = (() => {
  const booking={id:'demo-mail-flight',subject:'Your IndiGo booking is confirmed',sender:'IndiGo',pnr:'K7P2XQ',flight:'6E-5423',destination:'Chennai',departure:'Saturday, 7:10 PM',provenance:'Synthetic final demo script',body:'Booking confirmed\n\nIndiGo 6E-5423\nChennai flight\nSaturday, 7:10 PM\n\nBooking reference (PNR): K7P2XQ'};
  const meeting={id:'demo-coffee',title:'Coffee with Karthick',day:'Sunday',time:'6:00 PM',location:'',provenance:'Synthetic final demo script'};
  const manager={id:'demo-contact-rajesh',name:'Rajesh',label:'Manager',email:'rajesh.manager@example.com'};
  const draft={id:'demo-draft-leave',to:manager,subject:'Leave request for Monday',body:"Hi Rajesh,\n\nI'd like to request leave on Monday as I'll be returning late from a trip on Sunday. Please let me know if that works.\n\nThanks,\nNaithick"};
  const event=(event,values={})=>({event,...values,simulated:true});
  const dialogue=(speaker,text)=>event('conversation_item',{role:'user',speaker,text});
  const activation=(state,speaker='Naithick')=>event('demo_activation',{state,speaker});
  const nav=(app,step)=>event('demo_navigation',{app,step});
  const proposal=(id,fn,args)=>event('tool_raw_proposed',{proposal_id:id,function:fn,args,authorized_by:'Naithick'});
  const dispatch=(id,fn,args)=>event('tool_dispatch',{proposal_id:id,function:fn,args,authorized_by:'Naithick'});
  const success=(id,fn,resource_id)=>event('tool_executed',{proposal_id:id,function:fn,resource_id,outcome:'success'});
  const result=(text,resource_id)=>event('demo_result',{text,resource_id});
  const screen=(app,view,values={})=>({app,view,...values});
  const call=values=>screen('call','call',values);
  const frame=(durationMs,screen,events)=>({durationMs,screen,events});
  const edit=(values={})=>screen('calendar','edit',{item:meeting,time:'5:00 PM',activation:'active',saveState:'pending',...values});
  const workflows={
    booking:{label:'Chennai flight PNR',frames:[
      frame(7000,call({activation:'standby'}),[dialogue('Karthick',"Hey Naithick, quick thing. I'm at the airport counter and they're asking for the booking reference for our Chennai flight. Can you share the ticket number?"),activation('standby')]),
      frame(3500,call({activation:'active'}),[dialogue('Naithick','Oh, yeah, hold on. Hey Reprise, find our Chennai flight booking in my email.'),activation('active')]),
      frame(2000,screen('gmail','inbox',{activation:'active'}),[nav('Gmail','Open inbox')]),
      frame(2000,screen('gmail','search',{query:'IndiGo Chennai',activation:'active',searching:true}),[proposal('demo-flight-lookup','search_gmail',{query:'IndiGo Chennai'}),dispatch('demo-flight-lookup','search_gmail',{query:'IndiGo Chennai'}),success('demo-flight-lookup','search_gmail',booking.id),nav('Gmail','Search IndiGo booking')]),
      frame(2500,screen('gmail','message',{item:booking,activation:'active'}),[proposal('demo-flight-read','get_gmail_message',{id:booking.id}),dispatch('demo-flight-read','get_gmail_message',{id:booking.id}),success('demo-flight-read','get_gmail_message',booking.id),nav('Gmail','Open flight confirmation')]),
      frame(3000,call({pnr:booking,activation:'active'}),[result('PNR K7P2XQ · IndiGo 6E-5423 · Chennai · Saturday, 7:10 PM',booking.id),nav('Phone','Return to call')]),
      frame(2000,call({pnr:booking,activation:'active'}),[dialogue('Naithick',"Got it. It's K, 7, P, 2, X, Q.")]),
      frame(4000,call({pnr:booking,activation:'standby'}),[dialogue('Karthick','Perfect, they accepted it. Thanks!'),activation('standby')]),
      frame(4000,call({pnr:booking,activation:'standby'}),[activation('standby')])
    ]},
    calendar:{label:'Coffee · 5 to 6 PM',frames:[
      frame(5000,call({activation:'standby'}),[dialogue('Karthick',"Also, let's meet for coffee on Sunday at 5. Remember that, okay?"),activation('standby')]),
      frame(5000,call({activation:'active'}),[dialogue('Naithick','Sure. Hey Reprise, add coffee with Karthick to my calendar, Sunday at 5 PM.'),activation('active')]),
      frame(2000,screen('calendar','month',{item:meeting,activation:'active',time:'5:00 PM'}),[nav('Calendar','Open Sunday')]),
      frame(3000,screen('calendar','day',{item:meeting,activation:'active',time:'5:00 PM',available:true}),[dispatch('demo-free-five','list_calendar_events',{day:'Sunday',time:'5:00 PM'}),success('demo-free-five','list_calendar_events'),result('Sunday, 5 PM · Available')]),
      frame(4000,edit(),[proposal('demo-coffee-five','create_calendar_event',{title:meeting.title,day:'Sunday',time:'5:00 PM'})]),
      frame(4000,edit({activation:'paused',saveState:'paused'}),[dialogue('Karthick','No, no, wait, put 6 PM. I have something at 5.'),activation('paused','Karthick'),event('demo_action_paused',{proposal_id:'demo-coffee-five',reason:'Waiting for Naithick'})]),
      frame(2000,edit({time:'6:00 PM',previousTime:'5:00 PM',saveState:'revising'}),[dialogue('Naithick','Actually, make that 6 PM instead.'),activation('active'),event('intent_revised',{reason:'Naithick changed Sunday coffee from 5 PM to 6 PM'}),event('tool_superseded',{proposal_id:'demo-coffee-five',function:'create_calendar_event',reason:'5 PM never dispatched'}),proposal('demo-coffee-six','create_calendar_event',{title:meeting.title,day:'Sunday',time:'6:00 PM'})]),
      frame(3000,edit({time:'6:00 PM',saveState:'checking'}),[dispatch('demo-free-six','list_calendar_events',{day:'Sunday',time:'6:00 PM'}),success('demo-free-six','list_calendar_events'),result('Sunday, 6 PM · Available')]),
      frame(3000,edit({time:'6:00 PM',saveState:'saving'}),[dispatch('demo-coffee-six','create_calendar_event',{title:meeting.title,day:'Sunday',time:'6:00 PM'})]),
      frame(3000,call({activation:'active',confirmation:'Coffee with Karthick · Sunday, 6 PM'}),[success('demo-coffee-six','create_calendar_event',meeting.id),result('Coffee with Karthick · Sunday, 6 PM',meeting.id),nav('Phone','Return to call')]),
      frame(3000,call({activation:'active',confirmation:'Coffee with Karthick · Sunday, 6 PM'}),[dialogue('Naithick','Done, 6 it is.')]),
      frame(3000,call({activation:'standby',confirmation:'Coffee with Karthick · Sunday, 6 PM'}),[dialogue('Karthick','Great.'),activation('standby')])
    ]},
    draft:{label:'Leave email draft',frames:[
      frame(5000,call({activation:'standby'}),[dialogue('Karthick',"And just to remind you, you have to mail your boss about the leave. You said you'd do it today."),activation('standby')]),
      frame(5000,call({activation:'active'}),[dialogue('Naithick',"Oh, right, I totally forgot. Hey Reprise, draft an email to Rajesh asking for Monday off. I'm returning late from the trip."),activation('active')]),
      frame(2000,screen('contacts','list',{activation:'active'}),[nav('Contacts','Open contacts')]),
      frame(2500,screen('contacts','search',{query:'Rajesh',item:manager,activation:'active'}),[dispatch('demo-manager','find_contact',{name:'Rajesh'}),nav('Contacts','Search Rajesh')]),
      frame(2000,screen('contacts','detail',{item:manager,activation:'active'}),[success('demo-manager','find_contact',manager.id)]),
      frame(3500,screen('gmail','compose',{item:draft,activation:'active'}),[nav('Gmail','Open compose'),proposal('demo-leave-draft','create_gmail_draft',{to:manager.email,subject:draft.subject,body:draft.body})]),
      frame(2000,screen('gmail','drafts',{item:draft,activation:'active'}),[dispatch('demo-leave-draft','create_gmail_draft',{to:manager.email,subject:draft.subject,body:draft.body}),success('demo-leave-draft','create_gmail_draft',draft.id)]),
      frame(2000,call({activation:'active',confirmation:'Draft saved',confirmationDetail:'Not sent'}),[result('Draft saved',draft.id),nav('Phone','Return to call')]),
      frame(3500,call({activation:'active',confirmation:'Draft saved',confirmationDetail:'Not sent'}),[dialogue('Naithick',"Okay, the draft is ready. I'll review it and send it after the call.")]),
      frame(2500,call({activation:'standby',confirmation:'Draft saved',confirmationDetail:'Not sent'}),[dialogue('Karthick',"Cool, don't forget. See you Sunday at 6!"),activation('standby')])
    ]}
  };
  function freeze(value){if(value && typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;}
  for(const [scene,workflow] of Object.entries(workflows))for(const frame of workflow.frames)frame.scene=scene;
  workflows.story={label:'Full demo · 1:40',frames:[...workflows.booking.frames,...workflows.calendar.frames,...workflows.draft.frames]};
  let elapsedMs=0;
  for(const frame of workflows.story.frames){frame.elapsedMs=elapsedMs;frame.screen.callSeconds=elapsedMs/1000;frame.screen.inCall=true;elapsedMs+=frame.durationMs;}
  freeze(workflows);
  function createPlayback(onFrame){
    let key='booking',position=0;
    const operations=new Map();
    return {
      reset(value){if(!Object.hasOwn(workflows,value))throw new Error('Unknown demo workflow');key=value;position=0;operations.clear();},
      // Proposals are unsaved. Only dispatches enter this per-run mock ledger;
      // a matching receipt confirms the result, without touching Google.
      get calendarWrites(){return [...operations.values()].filter(write=>write.function==='create_calendar_event').map(write=>({...write,args:{...write.args}}));},
      get operations(){return [...operations.values()].map(operation=>({...operation,args:{...operation.args}}));},
      get position(){return position;},get length(){return workflows[key].frames.length;},
      get elapsedMs(){return workflows[key].frames[Math.max(0,position-1)].elapsedMs;},
      get durationMs(){return workflows[key].frames[Math.max(0,position-1)].durationMs;},
      advance(){
        const frame=workflows[key].frames[position];if(!frame)return false;
        const events=frame.events.map(e=>{
          if(e.event==='tool_dispatch'&&!operations.has(e.proposal_id))operations.set(e.proposal_id,{proposal_id:e.proposal_id,function:e.function,args:{...e.args},outcome:'running'});
          if(e.event==='tool_executed'){
            const operation=operations.get(e.proposal_id);
            if(!operation||operation.function!==e.function)return {...e,outcome:'unknown',reason:'No matching mock dispatch'};
            if(e.outcome==='success'&&e.function.startsWith('create_')&&!e.resource_id){operation.outcome='unknown';return {...e,outcome:'unknown',reason:'Mock write returned no resource receipt'};}
            operation.outcome=e.outcome;operation.resource_id=e.resource_id;
          }
          return e;
        });
        const confirmed=(fn,id)=>[...operations.values()].some(o=>o.function===fn&&o.outcome==='success'&&o.resource_id===id);
        const view={...frame.screen};
        if(view.pnr&&!confirmed('get_gmail_message',view.pnr.id))delete view.pnr;
        if(view.confirmation&&!confirmed(view.confirmation==='Draft saved'?'create_gmail_draft':'create_calendar_event',view.confirmation==='Draft saved'?draft.id:meeting.id)){delete view.confirmation;delete view.confirmationDetail;}
        if(view.app==='gmail')view.lookupConfirmed=confirmed(view.view==='message'?'get_gmail_message':view.view==='drafts'?'create_gmail_draft':'search_gmail',view.view==='drafts'?draft.id:booking.id);
        const groundedEvents=events.map(e=>e.event==='demo_result'&&e.resource_id&&![...operations.values()].some(o=>o.outcome==='success'&&o.resource_id===e.resource_id)?{...e,text:'Outcome unknown',outcome:'unknown'}:e);
        position++;onFrame({...frame,screen:view,events:groundedEvents});return true;
      }
    };
  }
  function createPhone(root,doc,onNavigate=()=>{},onState=()=>{},onLiveAction=()=>{}){
    let current=screen('home','home'),history=[],calendarTime,calendarEnd,calendarSave,calendarOld,calendarCorrection,calendarSaveButton,calendarCancel,assistantIndicator,callTime,callCaption;
    let callContext={active:false,seconds:0,last:null};
    const el=(tag,cls,text)=>{const e=doc.createElement(tag);e.className=cls||'';if(text!==undefined)e.textContent=text;return e;};
    const paths={back:'m15 5-7 7 7 7',search:'M21 21l-5-5 M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14',more:'M12 5h.01 M12 12h.01 M12 19h.01',menu:'M4 6h16 M4 12h16 M4 18h16',plus:'M12 4v16 M4 12h16',phone:'M5 3h4l2 5-3 2a14 14 0 0 0 6 6l2-3 5 2v4c0 2-3 2-4 2C9 20 4 15 3 7c0-1 0-4 2-4z',message:'M4 4h16v12H9l-5 4z',speaker:'M4 9h4l5-4v14l-5-4H4z M16 8q5 4 0 8 M18 5q8 7 0 14',mute:'M9 5v7a3 3 0 0 0 6 0V5a3 3 0 0 0-6 0 M6 10v2a6 6 0 0 0 12 0v-2 M12 18v4 M3 3l18 18',keypad:'M6 5h.01 M12 5h.01 M18 5h.01 M6 11h.01 M12 11h.01 M18 11h.01 M6 17h.01 M12 17h.01 M18 17h.01 M12 22h.01',bluetooth:'m7 6 10 12-5 4V2l5 4L7 18',mail:'M3 5h18v14H3z M3 5l9 7 9-7',video:'M3 6h12v12H3z M15 10l6-4v12l-6-4',send:'m3 3 18 9-18 9 4-9z M7 12h14',attach:'m8 16 8-8a3 3 0 0 0-4-4L4 12a5 5 0 0 0 7 7l9-9',archive:'M3 3h18v4H3z M5 7v14h14V7 M10 11h4',trash:'M4 6h16 M9 6V3h6v3 M6 6l1 15h10l1-15',unread:'M3 7l9 7 9-7 M3 7v14h18V7 M7 3h10',star:'m12 3 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1z',edit:'m4 16 12-12 4 4L8 20H4z',clock:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v6l4 2',location:'M12 22S4 14 4 9a8 8 0 0 1 16 0c0 5-8 13-8 13 M12 6a3 3 0 1 0 0 6 3 3 0 0 0 0-6',calendar:'M4 5h16v16H4z M4 10h16 M8 2v6 M16 2v6',bell:'M6 16V9a6 6 0 0 1 12 0v7l2 2H4z M10 21h4',repeat:'M4 8h14l-4-4 M20 16H6l4 4 M18 8l2 2 M6 16l-2-2',note:'M5 3h14v18H5z M8 8h8 M8 12h8 M8 16h5',person:'M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M4 22v-4a8 8 0 0 1 16 0v4'};
    function icon(name){
      const ns='http://www.w3.org/2000/svg';
      // A supplied document keeps rendering testable; SVGs use DOM paths, never markup.
      if(!doc.createElementNS)return el('span','phone-icon',name);
      const svg=doc.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('class','phone-icon');svg.setAttribute('aria-hidden','true');if(name==='keypad')svg.setAttribute('data-solid-dots','true');
      const path=doc.createElementNS(ns,'path');path.setAttribute('d',paths[name]||paths.more);svg.append(path);return svg;
    }
    const button=(label,cls,fn,iconName)=>{const b=el('button',cls);b.type='button';b.setAttribute('aria-label',label);b.onclick=()=>{if(onNavigate()!==false)fn?.();};if(iconName)b.append(icon(iconName));else if(['Compose','Cancel','Save'].includes(label))b.textContent=label;return b;};
    function open(value){
      history.push(current);
      const sameEvent=current.app==='calendar'&&current.view==='edit'&&value.app==='calendar'&&value.view==='edit'&&((current.live&&value.live&&current.item?.title===value.item?.title&&current.startDate===value.startDate&&current.endDate===value.endDate&&current.item?.location===value.item?.location&&current.item?.notes===value.item?.notes)||current.item?.id===value.item?.id);
      const sameCall=current.app==='call'&&value.app==='call'&&current.pnr?.id===value.pnr?.id&&current.confirmation===value.confirmation&&current.confirmationDetail===value.confirmationDetail&&current.connected===value.connected&&current.ownerConfirm===value.ownerConfirm&&current.muted===value.muted&&current.audioMuted===value.audioMuted;
      if(value.inCall!==undefined)callContext.active=value.inCall;
      if(value.callSeconds!==undefined)callContext.seconds=value.callSeconds;
      current={...value,inCall:callContext.active,callSeconds:callContext.seconds};
      if(current.app==='call')callContext.last=current;
      if(sameEvent&&calendarTime){updateCalendar();updateSurface();return;}
      // Dialogue and clock updates must not remount/fade an unchanged result card.
      if(sameCall&&callTime){
        callTime.textContent=formatDuration(current.callSeconds||0);
        if(callCaption)callCaption.textContent=current.callCaption||'Mobile · India';
        if(assistantIndicator)assistantIndicator.hidden=!current.activation||current.activation==='standby';
        else if(current.activation&&current.activation!=='standby')overlay(root.children[0]);
        updateSurface();return;
      }
      paint();
    }
    function home(){open(screen('home','home'));}
    function reset(){callContext={active:false,seconds:0,last:null};history=[];current=screen('home','home');paint();}
    function returnToCall(){if(callContext.active)open({...callContext.last,app:'call',view:'call',inCall:true,callSeconds:callContext.seconds});}
    function setElapsed(seconds){callContext.seconds=seconds;if(callTime)callTime.textContent=formatDuration(seconds);onState({...current,callSeconds:seconds});}
    function formatDuration(seconds){return String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(Math.floor(seconds%60)).padStart(2,'0');}
    function recents(){const apps=[current,...history.slice().reverse()].filter((v,i,all)=>!['home','recents'].includes(v.app)&&all.findIndex(a=>a.app===v.app)===i).slice(0,3);open(screen('recents','list',{apps}));}
    function back(){const previous=history.pop()||screen('home','home');current={...previous,inCall:callContext.active,callSeconds:callContext.seconds};if(current.app==='call')callContext.last=current;paint();}
    function updateSurface(){root.setAttribute('data-app',current.app);root.setAttribute('data-view',current.view);root.setAttribute('data-source',current.live?'live':'simulated');root.setAttribute('data-oneui','7');root.setAttribute('data-activation',current.activation||'standby');root.setAttribute('data-in-call',String(!!current.inCall));onState(current);if(assistantIndicator)assistantIndicator.setAttribute('aria-label',current.activation==='paused'?'Reprise paused':'Reprise active');}
    function searchBar(query,placeholder,fn,gmail=false){const form=el('form','native-search'+(gmail?' gmail-search':''));const field=el('input');field.value=query||'';field.placeholder=placeholder;field.setAttribute('aria-label',placeholder);form.append(icon(query?'back':'menu'),field);if(gmail)form.append(el('span','gmail-account','N'));else form.append(icon('search'));form.onsubmit=e=>{e.preventDefault();if(onNavigate()!==false)fn(field.value);};return form;}
    function header(title,{backButton=true,actions=[]}={}){const h=el('div','native-header');h.append(button(backButton?'Back':'Menu','native-icon-button',backButton?back:()=>{},backButton?'back':'menu'));if(title)h.append(el('strong','',title));const right=el('div','native-header-actions');actions.forEach(([label,name,fn])=>right.append(button(label,'native-icon-button',fn,name)));h.append(right);return h;}
    function overlay(page){
      if(!current.activation||current.activation==='standby')return;
      const indicator=el('div','reprise-indicator');assistantIndicator=indicator;indicator.setAttribute('role','status');indicator.setAttribute('aria-label',current.activation==='paused'?'Reprise paused':'Reprise active');indicator.append(el('span','reprise-spark','✦'));page.append(indicator);
    }
    function mailRow(isDraft=false){
      const row=button('Open '+(isDraft?'leave draft':'IndiGo email'),'mail-row',()=>open(screen('gmail',isDraft?'compose':'message',{item:isDraft?draft:booking})));
      row.append(el('span','mail-avatar',isDraft?'N':'I'));const text=el('span','mail-summary');
      text.append(el('strong','',isDraft?'Draft':booking.sender),el('b','',isDraft?draft.subject:booking.subject),el('span','',isDraft?"Hi Rajesh, I'd like to request leave…":'Chennai · PNR K7P2XQ · Saturday, 7:10 PM'));row.append(text,el('time','','Today'),icon('star'));return row;
    }
    function updateCalendar(){
      calendarTime.textContent=current.time;calendarEnd.textContent=current.endTime||(current.time==='6:00 PM'?'7:00 PM':'6:00 PM');
      calendarOld.textContent=current.previousTime||'';calendarOld.hidden=!current.previousTime;
      calendarTime.setAttribute('data-revised',String(!!current.previousTime));
      calendarSave.setAttribute('data-save-state',current.saveState||'pending');
      const label={paused:'Not saved · paused',checking:'Checking availability · not saved',revising:'6 PM selected · not saved',saving:'Saving',pending:'Not saved'}[current.saveState]||'Not saved';
      calendarSave.textContent=({checking:current.live?'Checking availability…':'Rechecking 6 PM…',saving:'Saving…'})[current.saveState]||(current.live&&current.ownerConfirm?'Owner confirmation':'Not saved');
      calendarSave.setAttribute('aria-label',label);root.setAttribute('data-save-state',current.saveState||'pending');
      calendarCorrection.textContent=current.live?(current.previousTime?current.previousTime+' superseded · Not saved':current.correctionText||''):current.previousTime?'5 PM superseded · Not saved':['pending','paused'].includes(current.saveState)?'5 PM proposal · Not saved':'';
      calendarCorrection.hidden=!calendarCorrection.textContent;calendarCorrection.setAttribute('data-phase',current.saveState||'pending');
      calendarSaveButton.hidden=current.saveState==='saving';calendarCancel.hidden=current.saveState==='saving';
      if(current.live){calendarSaveButton.textContent='Owner confirm';calendarSaveButton.disabled=!current.ownerConfirm;calendarSaveButton.onclick=()=>onLiveAction('approve');calendarCancel.hidden=true;}
    }
    function paint(){
      root.replaceChildren();assistantIndicator=null;updateSurface();calendarTime=null;callTime=null;
      if(current.app==='home'){
        const top=el('div','galaxy-home-title');top.append(el('span','home-date','Saturday'),el('h3','','7:00'),el('p','','Chennai'));
        const grid=el('div','home-apps');[['call','Phone','phone'],['gmail','Gmail','mail'],['calendar','Calendar','calendar'],['contacts','Contacts','person'],['messages','Messages','message']].forEach(([app,label,name])=>{const b=button('Open '+label+' preview','home-app',()=>open(screen(app,app==='gmail'?'inbox':app==='calendar'?'month':app==='call'?'call':'list',{item:meeting})));const tile=el('span','app-icon '+app);tile.append(icon(name));b.append(tile,el('span','',label));grid.append(b);});root.append(top,grid);return;
      }
      const page=el('div','native-page '+current.app);const body=el('div','native-body');
      if(current.app==='call'){
        body.className+=' native-call-body';
        const top=el('div','call-heading');callTime=el('p','native-call-state',formatDuration(current.callSeconds||0));callCaption=el('p','native-call-caption',current.callCaption||'Mobile · India');top.append(callTime,el('h3','native-call-name',current.caller||'Karthick'),callCaption);
        const more=button('Call options','call-more',()=>{},'more');more.disabled=true;top.append(more);
        const identity=el('div','call-identity');const portrait=el('div','call-avatar');portrait.append(icon('person'));identity.append(portrait);body.append(top,identity);
        if(current.pnr){const card=el('div','pnr-overlay');const source=el('div','pnr-source');source.append(icon('mail'),el('span','pnr-airline','IndiGo · Booking confirmation'));card.append(source,el('strong','pnr-code','K7P2XQ'),el('span','pnr-departure','Chennai · Saturday, 7:10 PM'),el('span','pnr-flight','6E-5423'));body.append(card);}
        if(current.confirmation){const result=el('div','call-result');const text=el('span','call-result-copy',current.confirmation);if(current.confirmationDetail)text.append(el('small','call-result-detail',current.confirmationDetail));if(current.live&&current.ownerConfirm){const approve=el('button','call-owner-confirm','Owner confirm');approve.onclick=()=>onLiveAction('approve');text.append(approve);}result.append(icon(current.confirmation==='Draft saved'?'mail':'calendar'),text);body.append(result);}
        const panel=el('div','call-control-panel');
        [['Add call','plus'],['Message','message'],['Bluetooth','bluetooth'],['Speaker','speaker'],['Mute','mute'],['Keypad','keypad']].forEach(([label,name])=>{const b=button(label,'native-call-control',()=>{},name);b.append(el('span','native-call-control-label',label));b.disabled=true;if(current.live&&current.connected&&['Speaker','Mute'].includes(label)){b.disabled=false;b.onclick=()=>onLiveAction(label==='Mute'?'mute':'speaker');}panel.append(b);});
        const assist=button('Call assist','native-call-assist',()=>{});assist.disabled=true;assist.append(el('span','','✦'),el('span','','Call assist'));
        const end=button('End call','native-call-end',()=>open(screen('home','home',{inCall:false})),'phone');if(current.live){end.disabled=!current.connected;end.onclick=()=>onLiveAction('end');}body.append(assist,panel,end);page.append(body);
      }else if(current.app==='gmail'){
        if(['inbox','search','drafts'].includes(current.view)){
          body.append(searchBar(current.view==='drafts'?'in:drafts':current.query,'Search in emails',query=>open(screen('gmail','search',{query})),true),el('p','native-section',current.view==='drafts'?'Drafts':current.view==='search'?'Search results':'Primary'));
          const matches=!current.query||/indigo|chennai|booking|k7p2xq/i.test(current.query);
          if(current.searching)page.setAttribute('data-searching','true');
          if(current.view==='drafts'&&current.lookupConfirmed!==false)body.append(mailRow(true));
          else if(current.view==='search'&&matches&&current.lookupConfirmed!==false){const row=mailRow();row.className+=' search-result';body.append(el('div','gmail-search-progress'),row);}
          else if(current.view==='search')body.append(el('p','native-empty',current.lookupConfirmed===false?'Searching…':'No results found'));
          if(current.view==='inbox'){
            for(const [sender,title,snippet] of [['Google','Your account is protected','Review your recent security activity'],['Rajesh','Re: Weekly catch-up','Thanks, see you on Monday.']]){
              const row=el('div','mail-row');row.append(el('span','mail-avatar',sender[0]));const summary=el('span','mail-summary');summary.append(el('strong','',sender),el('b','',title),el('span','',snippet));row.append(summary,el('time','','Fri'),icon('star'));body.append(row);
            }
          }
          body.append(button('Compose','mail-compose',()=>open(screen('gmail','compose',{item:draft})),'edit'));const bottom=el('div','native-bottom gmail-tabs');const mail=el('span','selected');mail.append(icon('mail'),el('small','','Mail'));const meet=el('span');meet.append(icon('video'),el('small','','Meet'));bottom.append(mail,meet);page.append(body,bottom);
        }else if(current.view==='compose'){
          const item=current.item||draft;
          const top=header('Compose',{actions:[['Attach','attach'],['Send','send'],['More','more']]});
          // The real Gmail send affordance is shown but cannot send in this simulation.
          Array.from(top.children[top.children.length-1].children).forEach(b=>{b.disabled=true;});page.append(top);
          [['From','Naithick'],['To',item.to.name+' ('+item.to.label+')'],['Subject',item.subject]].forEach(([label,value])=>{const row=el('div','compose-field');row.append(el('span','',label),el('span',label==='To'?'recipient-chip':'',value));body.append(row);});
          body.append(el('pre','compose-body',item.body));page.append(body);
        }else if(current.lookupConfirmed===false){
          page.append(header('Gmail'),el('p','native-empty','Message unavailable'));
        }else{
          const item=current.item||booking;page.append(header('',{actions:[['Archive','archive'],['Delete','trash'],['Mark unread','unread'],['More','more']]}));
          body.append(el('h3','email-subject',item.subject));const from=el('div','gmail-sender');from.append(el('span','mail-avatar','I'));const who=el('div');who.append(el('strong','','IndiGo'),el('p','','to me ▾'));from.append(who,el('time','','Today'));body.append(from);
          const ticket=el('div','airline-ticket');ticket.append(el('div','airline-wordmark','IndiGo'),el('h3','','Booking confirmed'),el('p','ticket-caption','Your Chennai flight'),el('strong','ticket-pnr',item.pnr),el('span','ticket-pnr-label','Booking reference (PNR)'),el('div','ticket-divider'),el('strong','',item.flight),el('p','','Saturday, 7:10 PM'),el('p','ticket-footer','Please carry a valid photo ID.'));body.append(ticket);page.append(body);
        }
      }else if(current.app==='calendar'){
        if(current.view==='edit'){
          page.append(header('Event',{actions:[['More','more']]}));body.className+=' calendar-editor';body.append(el('h3','calendar-title',current.item?.title||meeting.title));
          const group=el('div','calendar-fields');const all=el('div','calendar-field');all.append(el('span','','All day'),el('span','samsung-switch'));group.append(all);
          function timeRow(label,end=false){const row=el('div','calendar-field event-time-row');const date=el('span','event-date-label');date.append(el('small','event-field-label',label),el('span','calendar-date',current.live?(end?current.endDate:current.startDate):'Sunday'));row.append(date);const time=el('div','event-time-value');if(!end){calendarOld=el('s','old-event-time');calendarTime=el('strong','current-event-time');time.append(calendarOld,calendarTime);}else{calendarEnd=el('strong','end-event-time');time.append(calendarEnd);}row.append(time);row.setAttribute('aria-label',label);return row;}
          group.append(timeRow('Start'),timeRow('End',true));body.append(group);
          calendarCorrection=el('p','calendar-correction','5 PM superseded · Not saved');body.append(calendarCorrection);
          const extras=el('div','calendar-fields');[['location',current.live?(current.item?.location||'Location'):'Location'],['calendar','My calendars'],['bell',current.live?'Calendar reminders':'10 minutes before'],['repeat','Does not repeat'],['note',current.live?(current.item?.notes||'Notes'):'Notes']].forEach(([name,text])=>{const row=el('div','calendar-field');row.append(icon(name),el('span','',text));extras.append(row);});body.append(extras);
          calendarSave=el('span','calendar-saving');const bottom=el('div','calendar-editor-bottom');calendarSaveButton=button('Save','calendar-save',()=>{});calendarSaveButton.disabled=true;calendarCancel=button('Cancel','calendar-cancel',back);bottom.append(calendarCancel,calendarSave,calendarSaveButton);page.append(body,bottom);updateCalendar();
        }else{
          page.append(header('October',{backButton:false,actions:[['Search','search',()=>open(screen('calendar','search',{query:'Coffee',item:meeting}))],['More','more']]}));
          if(current.view==='search')body.append(searchBar(current.query,'Search events',query=>open(screen('calendar','search',{query}))));
          if(current.view==='month'){
            const grid=el('div','month-grid');['S','M','T','W','T','F','S','','','','',...Array.from({length:31},(_,i)=>String(i+1))].forEach((day,i)=>grid.append(el('span',i>6&&day==='25'?'selected':'',day)));body.append(grid);
          }
          body.append(el('p','calendar-day-heading','Sunday'));
          for(const time of ['4 PM','5 PM','6 PM','7 PM']){const row=el('div','calendar-hour');row.append(el('time','',time),el('span','hour-line'));if(current.available&&time==='5 PM')row.append(el('span','availability-label','Available'));body.append(row);}
          page.append(body,button('Add event','calendar-fab',()=>open(edit()),'plus'));
        }
      }else if(current.app==='contacts'){
        const item=current.item||manager;
        if(current.view==='detail'){
          page.append(header('',{actions:[['More','more']]}));body.className+=' contact-detail';const avatar=el('div','contact-portrait');avatar.append(icon('person'));body.append(avatar,el('h3','contact-name',item.name),el('p','contact-role',item.label));
          const actions=el('div','contact-actions');[['Call','phone'],['Message','message'],['Video call','video']].forEach(([label,name])=>actions.append(button(label,'contact-action',()=>{},name)));body.append(actions);
          const data=el('div','contact-data');data.append(el('small','','Work'),el('p','',item.email));body.append(data,button('Email','contact-email',()=>open(screen('gmail','compose',{item:draft})),'mail'));const footer=el('div','native-bottom contacts-bottom');['Edit','Share','More'].forEach(label=>footer.append(el('span','',label)));page.append(body,footer);
        }else{
          page.append(header(current.view==='search'?'':'Contacts',{backButton:current.view==='search',actions:[['Search','search',()=>open(screen('contacts','search',{query:'Rajesh'}))],['More','more']]}));
          if(current.view==='search')body.append(searchBar(current.query,'Search contacts',query=>open(screen('contacts','search',{query}))));else body.append(el('div','contacts-profile','My profile'),el('div','contacts-groups','Groups'));
          body.append(el('p','contact-letter','R'));
          if(!current.query||/rajesh|manager/i.test(current.query)){const row=button('Rajesh (Manager)','contact-row',()=>open(screen('contacts','detail',{item})));const portrait=el('span','contact-mini');portrait.append(icon('person'));row.append(portrait,el('span','','Rajesh'),el('small','','Manager'));body.append(row);}else body.append(el('p','native-empty','No contacts found'));
          page.append(body,button('Create contact','calendar-fab',()=>{},'plus'));
        }
      }else if(current.app==='recents'){
        page.append(header(''));body.className+=' recent-apps';
        (current.apps||[]).forEach(app=>{const card=button('Return to '+app.app,'recent-app',()=>open({...app,inCall:callContext.active,callSeconds:callContext.seconds}));card.append(icon(({call:'phone',gmail:'mail',calendar:'calendar',contacts:'person',messages:'message'})[app.app]),el('strong','',({call:'Phone',gmail:'Gmail',calendar:'Calendar',contacts:'Contacts',messages:'Messages'})[app.app]),el('div','recent-surface'));body.append(card);});page.append(body);
      }else if(current.app==='messages'){
        // Manual browsing only. No additional dialogue or send operation is authored.
        page.append(header('Messages',{backButton:false,actions:[['Search','search'],['More','more']]}));body.className+=' messages-body';body.append(el('h3','messages-heading','Messages'),el('p','messages-count','No unread messages'));
        const row=button('Karthick','message-thread',()=>open(screen('messages','thread')));row.append(el('span','contact-mini','K'),el('strong','','Karthick'));body.append(row);
        if(current.view==='thread'){body.replaceChildren(el('p','thread-contact','Karthick'),el('div','message-compose'));}
        page.append(body,el('div','native-bottom','Conversations                   Contacts'));
      }
      overlay(page);root.append(page);
    }
    paint();return {show:open,home,reset,back,recents,returnToCall,setElapsed,get current(){return current;}};
  }
  return {workflows,createPlayback,createPhone};
})();
if(typeof module!=='undefined')module.exports=ReprisePhone;
