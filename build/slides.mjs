import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {Presentation, PresentationFile} from '@oai/artifact-tool';

const workspaceDir='/Users/sahaj/Desktop/samsung';
const SKILL_DIR='/Users/sahaj/.codex/plugins/cache/openai-primary-runtime/presentations/26.904.11930/skills/presentations';
const TMP_DIR=path.join(workspaceDir,'build','slides');
const FINAL_PPTX=path.join(workspaceDir,'dist','Reprise_Theme05_Submission.pptx');
const RUNTIME_PYTHON='/Users/sahaj/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3';
const {resolvePresentationFont,finalizePresentation}=await import(pathToFileURL(path.join(SKILL_DIR,'container_tools/artifact_tool_utils.mjs')).href);
const font=resolvePresentationFont({fontFamily:'Helvetica Neue'});
const p=Presentation.create({slideSize:{width:1280,height:720}});
const C={paper:'#F7F9F6',ink:'#132B36',blue:'#3159D9',quiet:'#71818A',line:'#D9E2DE',mint:'#DDECE3',white:'#FFFFFF'};
function text(slide,content,x,y,w,h,size=28,color=C.ink,bold=false){
  const shape=slide.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
  shape.text=content;shape.text.style={typeface:font,fontSize:size,color,bold,autoFit:'none'};return shape;
}
function frame(title,num,sub=''){
  const s=p.slides.add();s.background.fill=C.paper;
  text(s,'REPRISE  /  SAMSUNG THEME 05',70,34,550,22,14,C.blue,true);
  text(s,String(num).padStart(2,'0')+'  /  08',1135,34,88,24,14,C.quiet);
  text(s,title,70,102,1130,110,46,C.ink,true);
  if(sub)text(s,sub,72,210,1110,55,22,C.quiet);
  return s;
}
function note(s,content){s.speakerNotes.textFrame.setText(content);}

// 1
{
 const s=p.slides.add();s.background.fill=C.ink;
 text(s,'REPRISE',70,42,420,38,25,C.white,true);
 text(s,'Voice assistance that\nstays with the conversation',70,178,1120,215,57,C.white,true);
 text(s,'A revision-aware LiveKit agent powered by Gemini 3.8 Live',74,476,985,70,28,'#C9D6DB');
 text(s,'Samsung Theme 05  •  27 September 2026',74,630,650,29,16,'#93AFBC');
 note(s,'Participant prototype. The user requested gemini-3.8-live as the base model. This deck describes measured development evidence and a working extension.');
}
// 2
{
 const s=frame('The benchmark rewards finished work',2,'Full-Duplex-Bench v3 evaluates real speech, corrections and tool use.');
 const metrics=[['100','recorded conversations'],['79','unique requests'],['12','speakers'],['12','mock tools']];
 metrics.forEach(([value,label],i)=>{let x=70+i*300;text(s,value,x,325,250,100,68,C.blue,true);text(s,label,x,435,258,58,19,C.ink);});
 text(s,'Strict pass: every required tool call and its arguments must be right, with no extra calls.',72,572,1100,70,24,C.ink);
 note(s,'Task definition: Theme05_Participant_Guide_UPDATED_FBD.docx and https://github.com/DanielLin94144/Full-Duplex-Bench/blob/main/v3/README.md . The older PDF describes a different evaluation; organizer clarification is still needed.');
}
// 3
{
 const s=frame('A correction can arrive at any time',3,'The difficult cases span listening, tool latency and spoken response.');
 const rows=[['01','Listen','“Search for shoes… actually, hiking boots.”'],['02','Act','A slow lookup or cart change is already pending.'],['03','Recover','The answer must use the corrected request and report real actions.']];
 rows.forEach(([n,label,body],i)=>{let y=306+i*112;text(s,n,72,y,80,58,27,C.blue,true);text(s,label,178,y,160,58,28,C.ink,true);text(s,body,355,y,810,72,25,C.ink);});
 note(s,'Research: https://arxiv.org/html/2605.13360v2 and https://arxiv.org/html/2609.20995v1 . The slide summarizes the failure modes addressed by Reprise.');
}
// 4
{
 const s=frame('A revision and commit controller',4,'The model proposes actions; the controller decides what can execute.');
 text(s,'AUDIO  →  GEMINI LIVE  →  TOOL PROPOSAL',72,302,1140,62,30,C.blue,true);
 text(s,'↓',82,372,50,58,34,C.blue);
 text(s,'VALIDATE  →  WAIT FOR SETTLED INPUT  →  EXECUTE',72,435,1140,70,28,C.ink,true);
 text(s,'Read calls can be cancelled. Dispatched writes stay in the ledger. Duplicate calls share one result.',72,556,1090,90,23,C.quiet);
 note(s,'Implementation: src/reprise/coordinator.py. The controller keeps room-local revisions, validates JSON schemas, verifies dependent identifiers from tool results, and records all dispatched calls. Research rationale in docs/RESEARCH.md.');
}
// 5
{
 const runName=process.env.REPRISE_DECK_RUN||'released-v8';
 const reportPath=path.join(workspaceDir,'runs',runName,'report.json');
 let report;try{report=JSON.parse(await fs.readFile(reportPath,'utf8'));}catch{}
 const s=frame('Measured development evidence',5,'All 100 released benchmark recordings.');
 const selected=report?.selected??12, completed=report?.completed??0, passed=report?.passed??0;
 text(s,`${passed} / ${completed}`,72,305,490,120,75,C.blue,true);
 text(s,'strict exact-match passes',75,424,600,43,25,C.ink);
 text(s,'21',735,305,310,120,75,C.blue,true);
 text(s,'local behavior and integration tests pass',738,424,465,80,23,C.ink);
 text(s,`Released recordings: ${completed} of ${selected} completed. Official organizer score: pending.`,72,568,1120,75,22,C.quiet);
 note(s,`Source: runs/${runName}/manifest.json and report.json; tests via uv run pytest. This is a provisional exact-match tool score, not the organizer score. The original semantic judge needs separate credentials; response quality, ASR and organizer normalization are not included. Incomplete sample must be labeled as such.`);
}
// 6
{
 const s=frame('Extension: help with washer error codes',6,'Voice can identify an error code, including a correction.');
 text(s,'“It says 4C… wait, it says 5C.”',72,292,1090,70,34,C.ink,true);
 text(s,'5C  →  water drainage',72,390,1050,85,43,C.blue,true);
 text(s,'First step: check the drain hose and waste connection for kinks or blockages.',72,495,1090,95,24,C.ink);
 text(s,'Recorded test with synthetic speech  •  General Samsung UK guidance, not a model diagnosis',72,629,1105,45,16,C.quiet);
 note(s,'Working test: runs/reprise-demo-5844034f7bab4f/events.jsonl and runs/extension-response.wav. Source: https://www.samsung.com/uk/support/home-appliances/what-do-the-codes-on-my-washing-machine-mean/ . The displayed example was tested with synthetic spoken input; do not describe it as a human live take.');
}
// 7
{
 const s=frame('A rerun can audit every decision',7,'Pinned upstream commit, locked dependencies, preserved traces.');
 const cmds=['./reproduce.sh all --concurrency 2 --name review-rerun','Review runs/review-rerun/report.json','Inspect per-room events and actual tool calls','Review saved evidence from released-v8'];
 cmds.forEach((cmd,i)=>{text(s,String(i+1).padStart(2,'0'),72,302+i*72,75,52,22,C.blue,true);text(s,cmd,165,300+i*72,1015,58,25,C.ink);});
 text(s,'Each run records its selected inputs, source digest, per-room events, calls and failures.',72,625,1100,54,20,C.quiet);
 note(s,'See README.md and src/reprise/evaluation.py. Public benchmark commit: 3e799c45a045256f47d5f1c9cda90157e2d2ec9e. Local exact-match screening uses the original streaming client and scorer, but does not replace the organizer evaluation.');
}
// 8
{
 const s=frame('What is ready for review',8,'Agent, recorded evaluation, extension and source-backed documentation.');
 const items=['Native voice and twelve public benchmark tools','Revision ledger with interruption and duplicate control','Reproducible evaluation with full call traces','Local washer-help demo grounded in Samsung support'];
 items.forEach((item,i)=>{text(s,'•',74,302+i*74,36,45,28,C.blue);text(s,item,124,302+i*74,1070,60,25,C.ink);});
 text(s,'The organizer’s pinned judge and hidden rerun determine the final ranking.',72,623,1080,55,20,C.quiet);
 note(s,'No official normalized score has been claimed. The participant should confirm the authoritative guide because the PDF and updated Word guide differ.');
}

await fs.mkdir(TMP_DIR,{recursive:true});await fs.mkdir(path.dirname(FINAL_PPTX),{recursive:true});
for(let i=0;i<p.slides.items.length;i++){
 const image=await p.export({slide:p.slides.items[i],format:'png',scale:1});
 await fs.writeFile(path.join(TMP_DIR,`slide-${i+1}.png`),new Uint8Array(await image.arrayBuffer()));
}
const stagingDir=path.join(workspaceDir,'.codex-finalizer');await fs.mkdir(stagingDir,{recursive:true});
const candidatePath=path.join(stagingDir,'candidate.pptx');
await (await PresentationFile.exportPptx(p)).save(candidatePath);
const result=await finalizePresentation({
  workspaceDir,candidatePath,finalPath:FINAL_PPTX,
  pythonExecutable:RUNTIME_PYTHON,
  integrityValidatorPath:path.join(SKILL_DIR,'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath:path.join(SKILL_DIR,'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-heading-fit'],
  explicitTotalSlideCount:8,requiredNativeTableOwnerSlides:[],
  fontPolicy:{basis:'design',families:[font]},verifyArtifactToolImport:true,
  receiptPath:path.join(stagingDir,'Reprise_Theme05_Submission.validation.json'),
});
console.log(JSON.stringify({slides:p.slides.items.length,font,output:FINAL_PPTX,result}));
