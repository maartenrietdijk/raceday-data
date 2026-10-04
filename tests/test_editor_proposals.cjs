const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const logic=require('../proposal-logic.js');
const fixture=JSON.parse(fs.readFileSync('tests/fixtures/supercars-gold-coast-proposals.json'));
const html=fs.readFileSync('raceday-editor.html','utf8');
for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
const clone=x=>JSON.parse(JSON.stringify(x));
function setup(proposals=fixture.proposals){
 let saved=0,messages=[];
 const state={calendarFiles:{'supercars_2026.json':[clone(fixture.event)]},proposals:clone(proposals),proposalUndo:{},dirtyProposalFiles:[],proposalDecisionsDirty:false};
 const ctx=vm.createContext({state,RaceDayProposals:logic,proposalIsStale:()=>false,renderProposalSurfaces(){},save(){saved++;},showStatus:(...x)=>messages.push(x)});
 vm.runInContext(html.slice(html.indexOf('function proposalByFingerprint('),html.indexOf('function rejectProposal(')),ctx);
 return {state,run:()=>vm.runInContext("acceptEventProposals('supercars_2026.json','supercars-2026-12')",ctx),saved:()=>saved,messages};
}
let t=setup();t.run();assert.equal(t.saved(),1);assert.equal(t.state.calendarFiles['supercars_2026.json'][0].sessions.length,8);assert(t.state.proposals.every(x=>x.status==='accepted'));
// Repeat in a browser containing a colliding draft ID from an earlier scan.
t=setup();let added=clone(fixture.event.sessions[0]);added.id=fixture.proposals[1].proposed.sessionId;added.name='My extra session';t.state.calendarFiles['supercars_2026.json'][0].sessions.push(added);t.run();assert.equal(t.saved(),1);assert(t.state.proposals.every(x=>x.status==='accepted'));assert.equal(t.state.calendarFiles['supercars_2026.json'][0].sessions.find(x=>x.id===added.id).name,'My extra session');
// A real failure must roll back every edit and decision, with no partial save.
let broken=clone(fixture.proposals);broken.at(-1).sessionId='missing';broken.at(-1).current.timeLocal='00:01';t=setup(broken);let before=clone(t.state);t.run();assert.deepEqual(clone(t.state),before);assert.equal(t.saved(),0);assert.match(t.messages.at(-1)[0],/Sessie niet gevonden/);
// Existing sessions must adopt the proposed title and undo must restore it.
const files={'supercars_2026.json':[clone(fixture.event)]},p=clone(fixture.proposals[0]);let undo=logic.apply(files,p);assert.equal(files['supercars_2026.json'][0].sessions.find(x=>x.id===p.sessionId).name,'Practice 1');logic.undo(files,p,undo);assert.equal(files['supercars_2026.json'][0].sessions.find(x=>x.id===p.sessionId).name,'Practice');
console.log('Real Gold Coast batch, draft ID collision, atomic rollback, title updates and undo passed');

// A local concept with different IDs still has a single matching original slot.
t=setup();t.state.calendarFiles['supercars_2026.json'][0].sessions.forEach((x,i)=>x.id=`local-${i}`);t.run();assert.equal(t.saved(),1);assert(t.state.proposals.every(x=>x.status==='accepted'));assert.equal(t.state.calendarFiles['supercars_2026.json'][0].sessions.find(x=>x.id==='local-0').name,'Practice 1');
// Ambiguous local slots must stay intact instead of guessing.
t=setup();const duplicate=clone(t.state.calendarFiles['supercars_2026.json'][0].sessions[0]);duplicate.id='other-practice';t.state.calendarFiles['supercars_2026.json'][0].sessions[0].id='local-practice';t.state.calendarFiles['supercars_2026.json'][0].sessions.push(duplicate);const unchanged=clone(t.state);t.run();assert.equal(t.saved(),0);assert.deepEqual(clone(t.state),unchanged);assert.match(t.messages.at(-1)[0],/Meerdere conceptsessies/);
console.log('Different local IDs accepted; ambiguous or changed concepts protected');

const remote=clone(fixture.proposals);const lost=clone(remote);lost.forEach(x=>x.status='accepted');const calendars={'supercars_2026.json':[clone(fixture.event)]};assert(logic.mergeLocalDecisions(remote,lost,calendars).every(x=>x.status==='open'));
const stale=clone(remote);stale.forEach(x=>x.status='superseded');assert(logic.mergeLocalDecisions(remote,stale,calendars).every(x=>x.status==='open'));
const applied=clone(remote);for(const x of applied){logic.apply(calendars,x);x.status='accepted';}assert(logic.mergeLocalDecisions(remote,applied,calendars).every(x=>x.status==='accepted'));
console.log('Lost local edits and obsolete local supersession no longer hide fresh remote results');
const resultsContext=vm.createContext({state:{proposals:clone(fixture.proposals)},RaceDayProposals:logic,activeCalendarFilename:()=> 'supercars_2026.json'});
vm.runInContext(html.slice(html.indexOf('function activeEventProposals('),html.indexOf('function officialScanStatusView(')),resultsContext);
assert.equal(vm.runInContext("activeEventProposals({id:'supercars-2026-12'}).length",resultsContext),8);
resultsContext.state.proposals.forEach(x=>x.status='accepted');assert.equal(vm.runInContext("activeEventProposals({id:'supercars-2026-12'}).length",resultsContext),0);
resultsContext.state.proposals.forEach(x=>x.status='verified');assert.equal(vm.runInContext("activeEventProposals({id:'supercars-2026-12'}).length",resultsContext),0);
console.log('Only active event results remain visible');

// Legacy F1 numeric placeholders must not be shown or accepted, even before reconciliation.
const oldF1=clone(fixture.proposals[0]);oldF1.seriesId='f1';
assert.equal(logic.openCount([oldF1]),0);assert.equal(logic.filtered([oldF1],{status:'active'}).length,0);
assert.throws(()=>logic.apply(calendars,oldF1),/betrouwbaar/);
logic.reconcile([oldF1],calendars);assert.equal(oldF1.status,'superseded');
const confirmedF1=clone(oldF1);confirmedF1.status='open';confirmedF1.source.confirmationPolicy='f1-published-schedule-v1';assert.equal(logic.openCount([confirmedF1]),1);
console.log('Legacy F1 placeholders blocked; confirmed timetable proposals allowed');

// Accepted rows stay hidden before and after publication. Failed upload keeps
// decisions and calendar edits pending, so the user can retry safely.
(async()=>{
 for(const fail of [false,true]){
  const items=clone(fixture.proposals);items.forEach(x=>{x.status='accepted';x.decisionAt='2026-10-03T12:00:00Z';});
  const state={github:{token:'gh-test',repo:'owner/repo'},calendarFiles:{'supercars_2026.json':[clone(fixture.event)]},proposals:items,dirtyProposalFiles:['supercars_2026.json'],proposalDecisionsDirty:true};
  const calls=[];
  const ctx=vm.createContext({state,RaceDayProposals:logic,activeCalendarFilename:()=> 'supercars_2026.json',parseCalendarFilename:()=>({seriesId:'supercars'}),buildCalendarFileJSON:()=> '{}',buildProposalStoreJSON:()=> '{}',uploadGitHubTextFile:async(file)=>{calls.push(file);if(fail)throw Error('test upload failed');},save(){},renderMain(){},showStatus(){}});
  vm.runInContext(html.slice(html.indexOf('function activeEventProposals('),html.indexOf('function officialScanStatusView(')),ctx);
  vm.runInContext(html.slice(html.indexOf('async function publishProposalChanges('),html.indexOf('// ── Add Series')),ctx);
  assert.equal(vm.runInContext("activeEventProposals({id:'supercars-2026-12'}).length",ctx),0);
  assert.equal(await vm.runInContext('publishProposalChanges()',ctx),!fail);
  assert.equal(state.proposalDecisionsDirty,fail);
  assert.equal(state.dirtyProposalFiles.length,fail?1:0);
  assert.equal(vm.runInContext("activeEventProposals({id:'supercars-2026-12'}).length",ctx),0);
  if(!fail)assert.deepEqual(calls,['supercars_2026.json','.raceday/session-time-proposals.json']);
 }
 console.log('Published proposals hidden; failed publication retains pending changes');
})().catch(error=>{console.error(error);process.exitCode=1;});
