const {test}=require('node:test');
const assert=require('node:assert/strict');
const {validateActivation,validateSessionPresentation,connectVoiceInput}=require('./app.js');
const reply=(mode='demo-live')=>({enabled:mode==='demo-live',state:mode==='demo-live'?'standby':'active',activation_version:2,presentation:mode,epoch:0});
test('old or mismatched servers cannot advertise a guarded Demo Live session',()=>{
 assert.throws(()=>validateSessionPresentation({room:'old'},'demo-live'));
 assert.throws(()=>validateSessionPresentation({activation_version:2,presentation:'live'},'demo-live'));
 assert.doesNotThrow(()=>validateSessionPresentation(reply(),'demo-live'));
});
test('the agent must acknowledge the actual guard, not merely the selected UI mode',()=>{
 assert.throws(()=>validateActivation({enabled:false,state:'active'},'demo-live'));
 assert.throws(()=>validateActivation({...reply(),enabled:false},'demo-live'));
 assert.equal(validateActivation(reply(),'demo-live'),'standby');
 assert.equal(validateActivation(reply('live'),'live'),'active');
});
test('microphone remains unpublished if activation acknowledgement fails',async()=>{
 const published=[];
 const target={remoteParticipants:new Map([['agent',{identity:'agent',isAgent:true,attributes:{'reprise.activation_ready':'2'}}]]),localParticipant:{
  performRpc:async()=>JSON.stringify({...reply(),enabled:false}),
  setMicrophoneEnabled:async value=>published.push(value)}};
 await assert.rejects(connectVoiceInput(target,'demo-live'));
 assert.deepEqual(published,[]);
});
test('activation negotiation precedes microphone input',async()=>{
 const order=[];
 const target={remoteParticipants:new Map([['agent',{identity:'agent',isAgent:true,attributes:{'reprise.activation_ready':'2'}}]]),localParticipant:{
  performRpc:async data=>{order.push('guard');assert.equal(JSON.parse(data.payload).activation_version,2);return JSON.stringify(reply());},
  setMicrophoneEnabled:async value=>order.push(value?'microphone':'muted')}};
 await connectVoiceInput(target,'demo-live');
 assert.deepEqual(order,['guard','microphone']);
});

test('agent presence alone does not mean its activation RPC is registered',async()=>{
 const order=[],handlers={};const agent={identity:'agent',isAgent:true,attributes:{}};
 const target={remoteParticipants:new Map([['agent',agent]]),on:(n,f)=>handlers[n]=f,off:n=>delete handlers[n],localParticipant:{
 performRpc:async()=>{order.push('rpc');return JSON.stringify(reply());},setMicrophoneEnabled:async()=>order.push('microphone')}};
 const connecting=connectVoiceInput(target,'demo-live');await Promise.resolve();assert.deepEqual(order,[]);
 agent.attributes['reprise.activation_ready']='2';handlers.participantAttributesChanged({},agent);
 await connecting;assert.deepEqual(order,['rpc','microphone']);assert.deepEqual(Object.keys(handlers),[]);
});
