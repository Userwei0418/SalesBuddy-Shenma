const test=require('node:test'),assert=require('node:assert/strict');
const {plotAxis}=require('../miniprogram/utils/quadrant');
test('地图按每次评分保存的规则分区，偏移不能跨越象限',()=>{
 const old={potential:72,relationship:70};
 const newer={...old,quadrant_policy:{version:2,definition:{potential_threshold:75,relationship_threshold:70,inclusive:false}}};
 assert.ok(plotAxis(old,'potential',-50)>50);
 assert.ok(plotAxis(newer,'potential',50)<50);
 assert.ok(plotAxis(old,'relationship')>50);
 assert.ok(plotAxis(newer,'relationship')<50);
 assert.ok(plotAxis(old,'potential')>50); // Reading a new version did not mutate historical defaults.
});

const flow = require("../miniprogram/utils/visitFlow");
test("拜访按审核保存的公司门槛判断，保留旧评分标准", () => {
  const quality = { follow_up_score: 75, admission_policy: { score_threshold: 75, inclusive: false, good_score: 85, excellent_score: 95 } };
  assert.equal(flow.scorePasses(quality), false);
  assert.equal(flow.grade(75, quality), "待完善");
  assert.equal(flow.grade(85, quality), "良好");
  assert.equal(flow.admissionRequirement(quality), "高于 75 分");
  assert.equal(flow.scorePasses({ ...quality, admission_policy: { ...quality.admission_policy, inclusive: true } }), true);
  assert.equal(flow.scorePasses({ follow_up_score: 61 }), true);
  assert.equal(flow.scorePasses({ follow_up_score: 101 }), false);
});

const path = require('node:path');
const {pathToFileURL} = require('node:url');
const assets = path.resolve(__dirname, '../../backend/src/sales_backend/web/assets');
const agentId = '01a09021-b848-7e7f-9567-e20859d29bf8';
const boundAgent = {
  capability: 'battle_map_review', capability_label: '客户作战地图评估',
  agent_name: 'Raccoon SalesBuddy-客户作战地图评估', agent_id: agentId,
  expected_snapshot_id: 'published-fixture', bound: true,
  configuration_url: `https://123.207.235.181/agents/${agentId}/configure`,
};
const guidance = {
  code: 'agent_business.battle_map_review', label: '客户作战地图业务指引',
  current: {source:'workspace',version:3,id:'policy',definition:{schema_version:1,guidance:'按公司业务事实判断',calibration_examples:''}},
  fields: [{key:'guidance',label:'业务指引',type:'textarea',required:false,max:2000},{key:'calibration_examples',label:'校准样例',type:'textarea',required:false,max:2000}], versions:[],
};
const executionItem = {
  code:'agent_execution.battle_map_review',label:'客户作战地图评估',
  current:{source:'baseline',version:1,definition:{platform_seconds:12,total_seconds:45}},
  fields:[{key:'platform_seconds',label:'中台预算',type:'number'},{key:'total_seconds',label:'总预算',type:'number'}],versions:[],
  business_rule:guidance,
  management:{scope:'当前公司',executor:'中台判断与后端校验',validation_status:'未核验',validation_note:'已配置不代表真实调用通过',related_agents:[boundAgent]},
};
async function consoleHarness(data) {
  const core=await import(pathToFileURL(path.join(assets,'core.js')).href);
  const page=await import(pathToFileURL(path.join(assets,'company-rules.js')).href);
  core.clearSession();
  Object.assign(core.state,{token:'fixture-only',actor:{workspace_id:'company-a',user_id:'operator',role:'operations'},company:{id:'company-a',name:'当前甲公司'},filters:{}});
  global.fetch=async()=>({ok:true,status:200,json:async()=>data});
  return {core,page};
}
test('中台跳转仅接受当前绑定编号的已核验 HTTPS 配置地址',async()=>{
  const {page}=await consoleHarness({});
  assert.equal(page.agentConfigurationUrl(boundAgent),boundAgent.configuration_url);
  for(const changes of [
    {bound:false},{agent_id:'not-an-agent'},
    {configuration_url:`https://evil.example/agents/${agentId}/configure`},
    {configuration_url:`http://123.207.235.181/agents/${agentId}/configure`},
    {configuration_url:`https://123.207.235.181/agents/${agentId}/configure?token=secret`},
    {configuration_url:`https://someone@123.207.235.181/agents/${agentId}/configure`},
    {configuration_url:'javascript:alert(1)'},
    {configuration_url:'https://123.207.235.181/agents/another-agent/configure'},
  ]) assert.equal(page.agentConfigurationUrl({...boundAgent,...changes}),'');
});
test('运营可编辑业务指引但不可编辑运行配置，规则数与能力数分别展示',async()=>{
  const {page}=await consoleHarness({items:[executionItem],can_publish:false,management:{workspace_id:'company-a',rule_count:8,capability_count:12}});
  const view=await page.agentExecution();
  assert.match(view.html,/当前甲公司/);
  assert.match(view.html,/>8<\/strong><span>公司规则/);
  assert.match(view.html,/>12<\/strong><span>业务能力/);
  assert.match(view.html,/data-action="edit-business"/);
  assert.match(view.html,/data-action="business-versions"/);
  assert.doesNotMatch(view.html,/data-action="edit"/);
  assert.match(view.html,/公司 V3/);
  assert.match(view.html,/当前公司/);
  assert.match(view.html,/中台判断与后端校验/);
  assert.match(view.html,/target="_blank" rel="noopener noreferrer"/);
  assert.doesNotMatch(view.html,/验收通过|调用已通过|绑定已就绪/);
});
test('公司未绑定时不借用已发布资源宣称就绪，也不暴露中台跳转',async()=>{
  const item=structuredClone(executionItem);
  item.management.validation_status='未绑定';
  item.management.related_agents=[{...boundAgent,bound:false,agent_name:'其他公司的智能体'}];
  const {page}=await consoleHarness({items:[item],can_publish:true,management:{rule_count:8,capability_count:12}});
  const view=await page.agentExecution();
  assert.match(view.html,/当前公司未绑定中台/);
  assert.match(view.html,/绑定后可前往中台/);
  assert.doesNotMatch(view.html,/href="https:\/\/123|其他公司的智能体/);
  assert.match(page.ruleManagement({}),/关联信息未核验/);
});
test('后台规则与中台名称只按文本显示，配置检查不是模型验收',async()=>{
  const {page,core}=await consoleHarness({items:[executionItem],can_publish:false,management:{rule_count:8,capability_count:12}});
  const view=await page.agentExecution();
  const output={innerHTML:''}, preview={}, form={querySelector:()=>({}),addEventListener(){}};
  const modal={innerHTML:'',querySelectorAll:()=>[],querySelector:s=>s==='#preview-rule'?preview:s==='#rule-preview'?output:form,showModal(){}};
  global.document={querySelector:s=>s==='#dialog'?modal:s==='#dialog-form'?form:null};
  const root={querySelector:()=>null};
  view.bind(root);
  await root.onclick({target:{closest:()=>({dataset:{action:'edit-business',id:executionItem.code}})}});
  assert.match(modal.innerHTML,/配置检查/);
  assert.match(modal.innerHTML,/固定返回契约与权限优先/);
  assert.match(modal.innerHTML,/name="guidance"/);
  assert.match(modal.innerHTML,/name="guidance" maxlength="2000"/);
  assert.doesNotMatch(modal.innerHTML,/<textarea[^>]*required/);
  const originalFormData=global.FormData;
  global.FormData=class {get(name){return name==='reason'?'修改原因':'';} entries(){return [];} };
  let checkedRequest;
  global.fetch=async(url,options)=>{
    checkedRequest={url,body:JSON.parse(options.body)};
    return {ok:true,status:200,json:async()=>({note:'只检查配置内容，不调用模型。',columns:['配置项','当前规则','草稿规则'],samples:[{label:'业务判断指引',before:'原指引',after:'沿用基础规则'}]})};
  };
  try {await preview.onclick();} finally {global.FormData=originalFormData;}
  assert.match(checkedRequest.url,/agent_business\.battle_map_review\/preview$/);
  assert.equal(checkedRequest.body.definition.guidance,'');
  assert.equal(checkedRequest.body.definition.calibration_examples,'');
  assert.match(output.innerHTML,/配置检查不代表真实 Agent 验收/);
  assert.match(output.innerHTML,/沿用基础规则/);
  const unsafe=structuredClone(executionItem);
  unsafe.management.related_agents[0].agent_name='<img src=x onerror=alert(1)>';
  assert.match(page.ruleManagement(unsafe),/&lt;img/);
  assert.doesNotMatch(page.ruleManagement(unsafe),/<img/);
  core.clearSession();
});
