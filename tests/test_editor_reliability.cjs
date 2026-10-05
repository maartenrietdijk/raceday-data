const assert=require('node:assert/strict');
const logic=require('../proposal-logic.js');
const clone=x=>JSON.parse(JSON.stringify(x));
function setup(){
 const session={id:'s',name:'Practice 1',kind:'practice',date:'2026-10-09',timeLocal:'12:00',durationMinutes:30};
 const files={'test.json':[{id:'e',sessions:[session]}]};
 const proposal={status:'open',seriesId:'dtm',calendarFile:'test.json',eventId:'e',sessionId:'s',proposalType:'time-update',current:clone(session),proposed:{...clone(session),sessionId:'s',timeLocal:'12:10'}};
 return {session,files,proposal};
}
for(const [field,value] of [['timeLocal','12:05'],['date','2026-10-10'],['name','Warm Up'],['kind','qualifying'],['durationMinutes',60],['_tbcMode',true]]){
 const {session,files,proposal}=setup();session[field]=value;const before=clone(files);
 assert.throws(()=>logic.apply(files,proposal),/gewijzigd|ander sessietype/);assert.deepEqual(files,before);
}
{
 const {session,files,proposal}=setup();session._tbcMode=true;proposal.proposed.timeLocal='12:00';
 assert.equal(logic.isFulfilled(files,proposal),false);
}
{
 const {files,proposal}=setup();proposal.stale=true;assert.throws(()=>logic.apply(files,proposal),/betrouwbaar/);
}
{
 const {files,proposal}=setup();files['test.json'][0].sessions.push(clone(files['test.json'][0].sessions[0]));
 assert.throws(()=>logic.apply(files,proposal),/hetzelfde ID/);
 assert.equal(logic.isFulfilled(files,proposal),false);
}
{
 const {files,proposal}=setup();proposal.sessionId=null;proposal.proposalType='new-session';proposal.proposed.sessionId='new';proposal.proposed.name='Free Practice 1';
 assert.throws(()=>logic.apply(files,proposal),/bestaat al/);assert.equal(files['test.json'][0].sessions.length,1);
}
for(const seriesId of ['sf','supergt','f1academy','elms']){
 const {files,proposal}=setup();proposal.seriesId=seriesId;proposal.proposed.timeLocal='12:00';proposal.proposed.durationMinutes=45;
 assert.equal(logic.isFulfilled(files,proposal),false);logic.apply(files,proposal);assert.equal(logic.isFulfilled(files,proposal),true);
}
console.log('Changed drafts, TBC, stale proposals, duplicate IDs, alias collisions and duration corrections protected');

{
 const {session,files,proposal}=setup();session.name='Warm Up';proposal.current.name='Warm Up';
 assert.throws(()=>logic.apply(files,proposal),/ander sessietype/);
}

// Accept and undo the four proposals generated from the real Portimao PDF.
{
 const fixture=require('./fixtures/elms/portimao-proposals.json');
 const files={'elms_2026.json':[clone(fixture.event)]};const before=clone(files);
 const undo=[];
 for(const proposal of fixture.proposals){undo.push(logic.apply(files,proposal));assert.equal(logic.isFulfilled(files,proposal),true);}
 assert.equal(files['elms_2026.json'][0].sessions.length,4);
 const race=files['elms_2026.json'][0].sessions.find(x=>x.kind==='race');assert.equal(race.timeLocal,'15:30');assert.equal(race.durationMinutes,240);
 for(let i=fixture.proposals.length-1;i>=0;i--)logic.undo(files,fixture.proposals[i],undo[i]);
 assert.deepEqual(files,before);
}
console.log('Real ELMS proposals update existing slots and restore correctly on undo');

// Legacy FD UTC values must reconcile and remain correct through apply/undo.
{
 const fixture=require('./fixtures/bsb_fd/long-beach2-proposals.json');
 const files={'fd_2026.json':[clone(fixture.event)]};
 for(const proposal of fixture.proposals) assert.equal(logic.isFulfilled(files,proposal),true);
 const p=clone(fixture.proposals[2]);p.status='open';p.proposalType='time-update';p.proposed.timeLocal='23:35';
 const before=clone(files);const undo=logic.apply(files,p);
 assert.equal(files['fd_2026.json'][0].sessions.length,7);
 const warm=files['fd_2026.json'][0].sessions.find(x=>x.id===p.sessionId);
 assert.equal(warm.kind,'testing');assert.equal(warm.timeLocal,'23:35');assert.equal(warm.timeUTC,undefined);
 assert.equal(logic.isFulfilled(files,p),true);logic.undo(files,p,undo);assert.deepEqual(files,before);
 files['fd_2026.json'][0].sessions[2].timeUTC='23:31';
 assert.throws(()=>logic.apply(files,p),/gewijzigd/);
}
console.log('Real FD UTC calendar reconciles; distinct warmups, edits and undo protected');
