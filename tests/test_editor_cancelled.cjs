const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('raceday-editor.html', 'utf8');
for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
const session = {id:'s1', name:'Practice 1', kind:'practice', date:'2026-10-25', timeLocal:'12:30', durationMinutes:60};
const rounds = [{id:'event', sessions:[session]}];
const checkbox = {checked:true}, nameInput = {value:session.name};
let saves=0, confirmations=0, answer=false;
const ctx=vm.createContext({state:{activeSeries:'f1'},editableRounds:()=>rounds,
 document:{getElementById:id=>id==='cancelled_0_0'?checkbox:id==='sessionName_0_0'?nameInput:null},
 confirm:message=>{confirmations++;assert.match(message,/Practice 1/);return answer;},
 save:()=>saves++, KIND_OPTIONS:[], esc:s=>s, editorDragEnabled:()=>true});
vm.runInContext(html.slice(html.indexOf('function sessionIsCancelled('),html.indexOf('// ── Data mutations')),ctx);
vm.runInContext(html.slice(html.indexOf('function updateSession('),html.indexOf('function toggleTBC(')),ctx);
vm.runInContext(html.slice(html.indexOf('function buildCalendarFileJSON('),html.indexOf('// ── Fetch Results')),ctx);
const before=JSON.stringify(session);
vm.runInContext('toggleCancelled(0,0,true)',ctx);
assert.equal(JSON.stringify(session),before);assert.equal(checkbox.checked,false);assert.equal(saves,0);assert.equal(confirmations,1);
answer=true;checkbox.checked=true;
vm.runInContext('toggleCancelled(0,0,true)',ctx);
assert.equal(session.name,'Practice 1 (cancelled)');assert.equal(nameInput.value,session.name);assert.equal(saves,1);assert.equal(confirmations,2);
assert.equal(session.date,'2026-10-25');assert.equal(session.timeLocal,'12:30');
vm.runInContext('toggleCancelled(0,0,true)',ctx);assert.equal(session.name,'Practice 1 (cancelled)');assert.equal(confirmations,2);
let exported=JSON.parse(vm.runInContext("buildCalendarFileJSON(editableRounds(),'f1')",ctx));
assert.equal(exported[0].sessions[0].name,'Practice 1 (cancelled)');
ctx.imported=exported[0].sessions[0];assert.equal(vm.runInContext('sessionIsCancelled(imported)',ctx),true);
assert.match(vm.runInContext('renderSession(imported,0,0)',ctx),/id="cancelled_0_0" checked/);
checkbox.checked=false;vm.runInContext('toggleCancelled(0,0,false)',ctx);
assert.equal(session.name,'Practice 1');assert.equal(confirmations,2);assert.equal(checkbox.checked,false);
assert.equal(session.timeLocal,'12:30');assert.equal(session.durationMinutes,60);
vm.runInContext("updateSession(0,0,'name','Practice 1 (canceled)')",ctx);assert.equal(checkbox.checked,true);
vm.runInContext('toggleCancelled(0,0,false)',ctx);assert.equal(session.name,'Practice 1');
vm.runInContext("updateSession(0,0,'name','Qualifying')",ctx);assert.equal(checkbox.checked,false);
console.log('Cancellation confirmation, dismissal, undo, legacy names, export/reload and unchanged session times passed');
