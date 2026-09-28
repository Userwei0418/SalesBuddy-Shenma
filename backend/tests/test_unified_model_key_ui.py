"""Exercise the real UI module with synthetic API and DOM boundaries."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_unified_key_single_input_readonly_and_safe_submission():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for browser module behavior tests")
    module = Path(__file__).resolve().parents[1] / "src/sales_backend/web/assets/model-api.js"
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8').replace(/^import .*\n/gm, '')
  .replace('export async function', 'async function');
const state = {actor:{role:'administrator'},company:{id:'company-one',name:'Synthetic company'}};
let info={managed:true,can_manage:false,configured:true,key_hint:'已配置',revision:3,
  rotation_status:'ready',message:'可用'};
let response={...info,can_manage:true,revision:4};
let sent=null, resolvePost, rejectPost;
const context={state, console, esc:s=>String(s||'').replace(/</g,'&lt;'), date:s=>s, head:()=>'',
  bindConnectivityTests:()=>{},
  api:async(path,options)=>{
    if(options) {
      sent={path,options}; return await new Promise((resolve,reject)=>{resolvePost=resolve;rejectPost=reject;});
    }
    return path.endsWith('unified-model-key')?info:{locked:true,items:[{purpose:'text',label:'文字',impact:'用途',
      configuration:{mode:'custom'},key_hint:'已配置'}]};
  }};
vm.createContext(context);vm.runInContext(source,context);
(async()=>{
  let page=await vm.runInContext('modelApis()',context);
  assert(!page.html.includes('type="password"'));
  assert(!page.html.includes('data-edit='));assert(!page.html.includes('data-connectivity-kind='));
  assert(page.html.includes('仅指定维护账号'));
  assert(page.html.includes('更新后对所有公司生效'));
  assert(!page.html.includes('当前操作仅影响'));
  info={...info,can_manage:true,rotation_status:'pending'};
  page=await vm.runInContext('modelApis()',context);
  assert.equal((page.html.match(/type="password"/g)||[]).length,1);
  assert(!page.html.includes('disabled')); // A pending journal must remain recoverable by resubmission.
  assert(!page.html.includes('name="endpoint_url"'));assert(!page.html.includes('name="provider_name"'));
  const input={value:'synthetic-key'}, button={disabled:false}, status={}, hint={};
  const form={elements:{api_key:input},querySelector:()=>button,reportValidity:()=>true};
  const root={isConnected:true,querySelector:s=>({'[data-unified-key-form]':form,
    '[data-unified-key-status]':status,'[data-unified-key-hint]':hint}[s]),querySelectorAll:()=>[]};
  page.bind(root);
  const first=form.onsubmit({preventDefault(){}});
  assert.equal(input.value,'');assert.equal(button.disabled,true);
  assert.equal(sent.path,'/api/v1/admin/unified-model-key');
  assert.equal(sent.options.body.api_key,'synthetic-key');assert.equal(sent.options.body.expected_revision,3);
  await form.onsubmit({preventDefault(){}}); // Duplicate submit cannot enqueue a second request.
  resolvePost(response);await first;
  assert.equal(button.disabled,false);assert.equal(input.value,'');
  input.value='synthetic-next';
  const second=form.onsubmit({preventDefault(){}});
  assert.equal(sent.options.body.expected_revision,4);
  rejectPost(new Error('统一密钥验证失败'));await second;
  assert.equal(input.value,'');assert.equal(button.disabled,false);assert.equal(status.textContent,'统一密钥验证失败');
  input.value='never-send-after-switch';sent=null;state.company.id='company-two';
  await form.onsubmit({preventDefault(){}});assert.equal(input.value,'');assert.equal(sent,null);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", script, str(module)], text=True, capture_output=True)  # noqa: S603
    assert result.returncode == 0, result.stderr
