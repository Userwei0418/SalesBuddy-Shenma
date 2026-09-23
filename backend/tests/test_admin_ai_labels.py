"""Run both console renderers against known, new and incomplete AI audit records."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sales_backend.domain.agent_management import management_metadata
from sales_backend.services.agent_platform.inference import AgentBinding

ASSETS = Path(__file__).parents[1] / "src/sales_backend/web/assets"


@pytest.mark.parametrize("page_name", ["calls", "runs"])
def test_ai_call_and_agent_audit_lists_and_details_use_readable_business_names(page_name):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for actual web renderer checks")
    script = (
        'import assert from "node:assert/strict";\n'
        f"const core=await import({json.dumps((ASSETS / 'core.js').as_uri())});\n"
        f"const pages=await import({json.dumps((ASSETS / 'pages.js').as_uri())});\n"
        f"const audit=await import({json.dumps((ASSETS / 'agent-audit.js').as_uri())});\n"
        f"const pageName={json.dumps(page_name)};\n"
    ) + r"""
        const records=[
          {code:'competency_review',label:'销售六维能力复盘'},
          {code:'opportunity_change',label:'商机变化评估'},
          {code:'future<agent>',label:'未识别的业务'},
          {code:'__proto__',label:'未识别的业务'},
          {code:null,label:'未记录业务能力'},
        ].map((r,i)=>({...r,id:String(i),case_id:String(i),operation_code:r.code,
          capability:r.code,actor_name:'演示销售',actor_role:'sales',actor_role_code:'sales',
          attempts:[{provider:'agent_platform',model:'agent:01a09022-b58f-7214-85eb-29d035ea6f2c',status:'succeeded'}],
          model_id:'recorded-model',endpoint_code:null,status:'succeeded',
          record_kind:'provider_attempt',inference_status:'accepted',operations:[],
          started_at:'2026-09-13T12:00:00+08:00',input_tokens:null,output_tokens:null,
          latency_ms:null}));
        const responses={
          '/api/v1/console/ai/overview':{summary:{calls:4,succeeded:4,failed:0,cancelled:0,
            running:0,input_tokens:null,output_tokens:null,unknown_token_calls:4,
            average_latency_ms:null,business_operations:4,audio_seconds:null},trend:[],roles:[]},
          '/api/v1/console/ai/calls':{items:records,total:records.length},
          '/api/v1/console/ai/rules':{items:[],alerts:[]},
          '/api/v1/console/ai/runs':{items:records,total:records.length,statistics:{}},
        };
        globalThis.fetch=async (url,options)=>{
          assert.equal(options.method,'GET');
          const path=url.split('?')[0]; assert.ok(Object.hasOwn(responses,path),path);
          return {ok:true,status:200,json:async()=>responses[path]};
        };
        core.state.token='renderer-test-only';
        core.state.actor={workspace_id:'test-workspace',user_id:'test-admin',role:'administrator'};
        core.state.filters={period:'month',capability:'competency_review'};
        const page=await (pageName==='calls'?pages.aiUsage():audit.agentRuns());
        const dialog={innerHTML:'',querySelectorAll:()=>[],showModal(){}};
        globalThis.document={querySelector:selector=>selector==='#dialog'?dialog:null};
        const regions={};
        const root={querySelector:selector=>selector.startsWith('[data-region=')
          ? regions[selector] ||= {innerHTML:'',setAttribute(){}} : null};
        await page.bind(root);
        const rendered=page.html+Object.values(regions).map(region=>region.innerHTML).join('');
        for(const record of records) assert.ok(rendered.includes(record.label),record.label);
        assert.doesNotMatch(rendered, /future<agent>|\[object Object\]/);
        if(pageName==='runs') assert.match(rendered,/销售智助·商机新建更新判断/);
        if(pageName==='runs') assert.match(rendered,
          /value="competency_review" selected>销售六维能力复盘<\/option>/);
        for(const record of records){
          const button={dataset:{action:pageName==='calls'?'call':'detail',id:record.id},disabled:false};
          await root.onclick({target:{closest:()=>button}});
          assert.ok(dialog.innerHTML.includes(record.label),record.label);
          assert.match(dialog.innerHTML,/业务能力/);
          assert.doesNotMatch(dialog.innerHTML,/future<agent>|\[object Object\]/);
          if(pageName==='calls'){
            assert.match(dialog.innerHTML,/业务标识/);
            assert.match(dialog.innerHTML,/接口/);
          }
        }
    """
    result = subprocess.run(  # noqa: S603 - fixed local Node renderer, no user commands.

        [node, "--input-type=module"], input=script, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_recorded_agent_identity_is_not_inferred_from_business_purpose():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required")
    module = json.dumps((ASSETS / "ai-audit-labels.js").as_uri())
    script = f'import {{calledAgents, stopStates, label}} from {module};\n' + r"""
import assert from 'node:assert/strict';
const row={capability:'opportunity_change',configuration:{agent_id:'01a09022-b58f-7214-85eb-29d035ea6f2c'},
  attempts:[{provider:'agent_platform',model:'agent:01a09684-1968-77da-bfd0-e5e0ab59f015'}]};
assert.equal(calledAgents(row),'销售智助·商机经营建议（第一版）');
row.attempts[0].model='agent:01a09fca-4a3b-7da4-8522-e68ca2b78074';
assert.equal(calledAgents(row),'销售智助·商机经营建议（第二版）');
row.attempts[0].model='agent:01a09022-b58f-7214-85eb-29d035ea6f2c';
assert.equal(calledAgents(row),'销售智助·商机新建更新判断');
row.attempts[0].network_dispatch_suppressed=true;
assert.equal(calledAgents(row),'未记录中台请求');
assert.equal(label(stopStates,'not_requested'),'无需停止请求');
assert.equal(label(stopStates,'__proto__'),'历史未记录');
"""
    result = subprocess.run(  # noqa: S603 - fixed local Node renderer, no user commands.
        [node, "--input-type=module"], input=script, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_rule_binding_cards_show_configuration_and_link_to_actual_run_evidence():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for actual web renderer checks")
    registered = "01a09021-b848-7e7f-9567-e20859d29bf8"
    wrong_capability = "01a09022-b58f-7214-85eb-29d035ea6f2c"
    cases = [
        (registered, "已加载绑定", True),
        (None, "未绑定", False),
        (wrong_capability, "绑定能力不匹配", False),
        ("unregistered", "已加载绑定", False),
    ]
    fixtures = []
    for agent_id, status, can_navigate in cases:
        bindings = {
            "battle_map_review": AgentBinding(agent_id=agent_id, snapshot_id="expected-only"),
        } if agent_id else {}
        fixtures.append({
            "management": management_metadata("agent_execution.battle_map_review", bindings),
            "status": status,
            "can_navigate": can_navigate,
        })
    module = json.dumps((ASSETS / "company-rules.js").as_uri())
    script = f'import {{ruleManagement}} from {module};\nconst fixtures={json.dumps(fixtures)};\n' + r"""
import assert from 'node:assert/strict';
for (const fixture of fixtures) {
  const html=ruleManagement(fixture);
  assert.ok(html.includes('绑定配置：'+fixture.status));
  assert.match(html,/href="#agentRuns"/);
  assert.doesNotMatch(html,/href="#agentRuns\?/);
  assert.doesNotMatch(html,/实际执行快照|尚未核验|badge green/);
  assert.match(html,/实际调用以运行审计为准/);
  assert.equal(html.includes('前往中台配置'),fixture.can_navigate);
  if(fixture.status==='已加载绑定') assert.match(html,/badge blue/);
  if(fixture.status==='绑定能力不匹配') assert.match(html,/badge red/);
}
"""
    result = subprocess.run(  # noqa: S603 - fixed local Node renderer, no user commands.
        [node, "--input-type=module"], input=script, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
