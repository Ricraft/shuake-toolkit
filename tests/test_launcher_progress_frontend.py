"""Run the production launcher update dialog against slow/failing backend states."""
import json
import subprocess
from pathlib import Path


def test_launcher_dialog_stays_open_until_prepared_and_can_retry_failure():
    source = (Path(__file__).resolve().parents[1] / 'web/app.js').read_text(encoding='utf-8')
    dialog = source[source.index('        function showLauncherUpdateModal('):source.index('        let autovisorUpdateChecking')]
    progress = source[source.index('        function setLauncherUpdateBusy('):]
    harness = r'''
const assert = require('assert');
let launcherUpdateConfirming = false, retryCalls = 0, apiCalls = 0;
const timers = [], items = new Map();
function element(id) {
 if(!items.has(id)) items.set(id, {disabled:false, hidden:true, dataset:{}, style:{}, textContent:'', innerHTML:'', classList:{active:false,add(){this.active=true},remove(){this.active=false}}});
 return items.get(id);
}
const document = {getElementById:element};
const window = {pendingLauncherUpdateDialog:null};
const setText = (id,value)=>element(id).textContent=value;
const showToast = ()=>{};
const formatUpdateBytes = n=>`${n} B`;
const formatUpdateDuration = n=>`${n}s`;
const setTimeout = cb=>timers.push(cb);
const openLauncherUpdateDialog = ()=>retryCalls++;
const apiCall = async ()=>{apiCalls++;return {ok:true};};
const handleWebActionResult = result=>result.ok;
eval(SOURCE);
(async()=>{
 showLauncherUpdateModal({latestVersion:'v1.8.0',confirmationToken:'token'});
 await confirmLauncherUpdate();
 assert.equal(apiCalls,1);
 assert.equal(element('launcher-update-modal').classList.active,true);
 assert.equal(element('launcher-update-progress').hidden,false);
 assert.equal(element('launcher-update-confirm-btn').disabled,true);
 closeLauncherUpdateModal();
 assert.equal(element('launcher-update-modal').classList.active,true);
 handleLauncherUpdateRuntime({phase:'downloading',percent:50,total:100,downloaded:50,speed:10,eta:5});
 assert.equal(element('launcher-update-progress-bar').style.width,'50%');
 assert(element('launcher-update-progress-detail').textContent.includes('10 B/s'));
 handleLauncherUpdateRuntime({phase:'failed',percent:50,message:'checksum mismatch'});
 assert.equal(element('launcher-update-modal').classList.active,true);
 assert.equal(element('launcher-update-progress').dataset.phase,'failed');
 assert(element('launcher-update-progress-detail').textContent.includes('checksum mismatch'));
 assert.equal(element('launcher-update-confirm-btn').disabled,false);
 element('launcher-update-confirm-btn').onclick();
 assert.equal(retryCalls,1);
 showLauncherUpdateModal({latestVersion:'v1.8.0',confirmationToken:'fresh-token'});
 await confirmLauncherUpdate();
 handleLauncherUpdateRuntime({phase:'verifying',percent:100,message:'verify'});
 assert.equal(element('launcher-update-confirm-btn').disabled,true);
 handleLauncherUpdateRuntime({phase:'ready',percent:100,message:'ready to apply'});
 assert.equal(element('launcher-update-progress').dataset.phase,'completed');
 assert.equal(element('launcher-update-modal').classList.active,true);
 timers[0]();
 assert.equal(element('launcher-update-modal').classList.active,false);
})().catch(e=>{console.error(e);process.exitCode=1});
'''.replace('SOURCE', json.dumps(dialog + progress))
    result = subprocess.run(['node', '-e', harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
