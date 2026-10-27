"""Run both console renderers against known, new and incomplete AI audit records."""

import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sales_backend.domain.agent import AgentMode
from sales_backend.domain.agent_management import management_metadata
from sales_backend.domain.company_rules import TECHNICAL_CAPABILITIES
from sales_backend.services.agent_platform.inference import AgentBinding

ASSETS = Path(__file__).parents[1] / "src/sales_backend/web/assets"


def test_all_emitted_ai_business_codes_have_console_labels():
    """New Agent modes, capabilities, audio uses and direct observers need display names."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the actual shared console registry")
    source = ASSETS.parents[1]
    emitted = {mode.value for mode in AgentMode} | set(TECHNICAL_CAPABILITIES)
    for path in source.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            name = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)
            if name == "DatabaseModelObserver" and len(call.args) > 2:
                operation = call.args[2]
                if isinstance(operation, ast.Constant) and isinstance(operation.value, str):
                    emitted.add(operation.value)
            # InferenceService and audit_original write the exact business mode,
            # including derived modes such as opportunity_change.
            if name in {"evaluate", "audit_original"}:
                for keyword in call.keywords:
                    if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                        emitted.add(keyword.value.value)
    audio_tree = ast.parse((source / "api/audio.py").read_text(encoding="utf-8"))
    purposes = next(
        ast.literal_eval(item.value)
        for item in audio_tree.body
        if isinstance(item, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "PURPOSES" for target in item.targets)
    )
    emitted.update("audio." + purpose for purpose in purposes)
    module = json.dumps((ASSETS / "ai-capabilities.js").as_uri())
    result = subprocess.run(  # noqa: S603 - fixed local Node registry, no user commands.
        [node, "--input-type=module"],
        input=f"import {{capabilityLabels}} from {module}; console.log(JSON.stringify(capabilityLabels));",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    labels = json.loads(result.stdout)
    assert not emitted - labels.keys(), f"Missing AI business labels: {sorted(emitted - labels.keys())}"
    assert all(isinstance(labels[code], str) and labels[code].strip() for code in emitted)


def test_errors_are_provider_neutral_and_keep_unclassified_codes():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for console labels")
    module = json.dumps((ASSETS / "ai-audit-labels.js").as_uri())
    script = f"import {{errorName,modelName}} from {module};\n" + r"""
import assert from 'node:assert/strict';
const codes=['TimeoutError','InvalidAgentResult','ProviderUnavailable','SenseAudioError','CancelledError',
  'CallQuotaExceeded','AIServiceUnavailable','CircuitOpenError','AgentUnavailable','ReconciliationRequired'];
for(const code of codes) {
  assert.doesNotMatch(errorName(code),/中台|未归类错误/);
}
assert.match(errorName('CancelledError'),/可能由超时或取消/);
assert.equal(errorName('new<error>'),'未归类错误（new<error>）');
assert.equal(errorName('__proto__'),'未归类错误（__proto__）');
assert.equal(errorName(null),'未记录异常');
assert.equal(modelName('recorded-model'),'recorded-model');
assert.equal(modelName(null),'未记录模型');
"""
    result = subprocess.run(  # noqa: S603 - fixed local Node renderer, no user commands.
        [node, "--input-type=module"], input=script, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


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
          {code:'task_import',label:'周会任务整理'},
          {code:'visit_import',label:'拜访录音导入转写'},
          {code:'model_connection_test',label:'模型接口连通性测试'},
          {code:'audio.chatbi',label:'经营问数语音转写'},
          {code:'audio.visit_entry',label:'拜访记录语音转写'},
          {code:'audio.customer_create',label:'客户建档语音转写'},
          {code:'audio.management_task',label:'管理任务语音转写'},
          {code:'audio.demo_scene',label:'演示场景语音转写'},
          {code:'future<agent>',label:'未登记业务（future<agent>）'},
          {code:'__proto__',label:'未登记业务（__proto__）'},
          {code:null,label:'未记录业务能力'},
        ].map((r,i)=>({...r,id:String(i),case_id:String(i),operation_code:r.code,
          capability:r.code,actor_name:'演示销售',actor_role:'sales',actor_role_code:'sales',
          attempts:[{provider:'agent_platform',model:'agent:01a09022-b58f-7214-85eb-29d035ea6f2c',status:'succeeded'}],
          model_id:'recorded-model<&>',endpoint_code:null,status:'failed',
          operation_id:'operation-'+i,error_code:'SenseAudioError',
          record_kind:'provider_attempt',inference_status:'accepted',operations:[],
          started_at:'2026-09-13T12:00:00+08:00',input_tokens:null,output_tokens:null,
          latency_ms:null}));
        for (const row of records) row.operations=[{
          id:row.operation_id,capability:row.capability,inference_status:'failed',error_code:'SenseAudioError',
          events:[{phase:'route_failed',provider:'senseaudio',error_code:'future<error>',elapsed_ms:123}],
          attempts:[{id:'attempt-'+row.id,provider:'senseaudio',model:row.model_id,
            status:'failed',error_code:'CancelledError',latency_ms:14339}],
        }];
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
        for(const record of records) assert.ok(rendered.includes(core.esc(record.label)),record.label);
        assert.doesNotMatch(rendered, /future<agent>|\[object Object\]/);
        if(pageName==='runs') assert.match(rendered,/Raccoon SalesBuddy·商机新建更新判断/);
        if(pageName==='runs') assert.match(rendered,
          /value="competency_review" selected>销售六维能力复盘<\/option>/);
        const knownRecords=records.filter(r=>r.code && !['future<agent>','__proto__'].includes(r.code));
        if(pageName==='runs') for(const record of knownRecords)
          assert.ok(rendered.includes('value="'+record.code+'"'),record.code);
        for(const record of records){
          const button={dataset:{action:pageName==='calls'?'call':'detail',id:record.id},disabled:false};
          await root.onclick({target:{closest:()=>button}});
          assert.ok(dialog.innerHTML.includes(core.esc(record.label)),record.label);
          assert.match(dialog.innerHTML,/业务能力/);
          assert.doesNotMatch(dialog.innerHTML,/future<agent>|\[object Object\]/);
          if(record.code) assert.ok(dialog.innerHTML.includes(core.esc(record.code)));
          assert.ok(dialog.innerHTML.includes(core.esc(record.model_id)));
          assert.ok(dialog.innerHTML.includes(record.operation_id));
          assert.match(dialog.innerHTML,/SenseAudioError/);
          assert.match(dialog.innerHTML,/模型接口请求未成功/);
          assert.doesNotMatch(dialog.innerHTML,/返回字段、类型或引用不符合业务契约/);
          if(pageName==='calls'){
            assert.match(dialog.innerHTML,/业务标识/);
            assert.match(dialog.innerHTML,/接口/);
          }else{
            assert.match(dialog.innerHTML,/CancelledError/);
            assert.match(dialog.innerHTML,/请求已中止/);
            assert.match(dialog.innerHTML,/future&lt;error&gt;/);
            assert.doesNotMatch(dialog.innerHTML,/future<error>/);
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
assert.equal(calledAgents(row),'Raccoon SalesBuddy·商机经营建议（第一版）');
row.attempts[0].model='agent:01a09fca-4a3b-7da4-8522-e68ca2b78074';
assert.equal(calledAgents(row),'Raccoon SalesBuddy·商机经营建议（第二版）');
row.attempts[0].model='agent:01a09022-b58f-7214-85eb-29d035ea6f2c';
assert.equal(calledAgents(row),'Raccoon SalesBuddy·商机新建更新判断');
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
    core = json.dumps((ASSETS / "core.js").as_uri())
    script = (f'import {{ruleManagement}} from {module};\nimport {{state}} from {core};\n'
              f'const fixtures={json.dumps(fixtures)};\n') + r"""
import assert from 'node:assert/strict';
state.actor={role:'administrator'};
state.permissions={'ai.run_read':true};
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
// The display role never substitutes for an explicit audit-read grant.
// Missing/denied grants also cover an open card after permission revocation.
for (const permissions of [{},{'ai.run_read':false}]) {
  state.permissions=permissions;
  for (const fixture of fixtures) {
    const html=ruleManagement(fixture);
    assert.doesNotMatch(html,/href="#agentRuns/);
    assert.ok(html.includes('绑定配置：'+fixture.status));
    assert.match(html,/实际调用以运行审计为准/);
    assert.equal(html.includes('前往中台配置'),fixture.can_navigate);
  }
}
"""
    result = subprocess.run(  # noqa: S603 - fixed local Node renderer, no user commands.
        [node, "--input-type=module"], input=script, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
