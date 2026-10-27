import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';
import {adviceEntries, resolveComponent, invokeAdvice} from '../department-ui/advice-bridge.js';
import * as bridge from '../department-ui/advice-bridge.js';
import * as insights from '../department-ui/insights-presentation.js';
import * as riskPresentation from '../department-ui/risk-presentation.js';
import {inspectStyles} from '../scripts/check-ui-contract.mjs';

function component(file) {
  const module = {exports:{}};
  vm.runInNewContext(transformSync(readFileSync(new URL('../department-ui/'+file,import.meta.url),'utf8'),{loader:'jsx',format:'cjs'}).code,
    {module,exports:module.exports,require:name=>name==='react'?{...React,useState:value=>[value,()=>{}],useEffect:()=>{},useMemo:f=>f()}:name.endsWith('advice-bridge.js')?bridge:name.endsWith('insights-presentation.js')?insights:name.endsWith('risk-presentation.js')?riskPresentation:name.endsWith('/pc/data.js')?{can:()=>false,open:{route(){}}}:new Proxy({}, {get:(_,name)=>name})});
  return module.exports;
}
const {AdviceActions, RecordingGuide, RankingMembers, PresentationSwitch}=component('WorkflowCompletion.jsx');
function all(tree, predicate) {
  if (!tree || typeof tree !== 'object') return [];
  return [...(predicate(tree)?[tree]:[]),...React.Children.toArray(tree.props?.children).flatMap(child=>all(child,predicate))];
}
const text=tree=>typeof tree==='string'||typeof tree==='number'?String(tree):React.Children.toArray(tree?.props?.children).map(text).join('');
const buttons=tree=>all(tree,node=>node.type==='Button');
function fixture(route='pages/visit-detail/index') {
  const suggestion={id:'suggestion',decision:'pending',version_no:3};
  const advice={id:'analysis',status:'ready',rows:[suggestion]};
  const instance={data:{analysisId:advice.id,suggestion,canDecide:true,saving:false}};
  const page={route,data:{visitAdvice:advice},selectAllComponents:()=>[instance],selectComponent:()=>instance};
  const calls=[]; const invokeOn=(owner,action,payload)=>calls.push({owner,action,payload});
  return {page,advice,suggestion,instance,calls,invokeOn};
}
test('advice instance resolution includes analysis identity and syncs the full current suggestion',()=>{
  const f=fixture(), old={data:{analysisId:'old',suggestion:f.suggestion}};
  f.page.selectAllComponents=()=>[old,f.instance];
  const [entry]=adviceEntries(f.advice);
  assert.equal(resolveComponent(f.page,entry),f.instance);
  assert.equal(entry.props.suggestion,f.suggestion);
  assert.deepEqual(adviceEntries({...f.advice,checking:true}),[]);
  assert.deepEqual(adviceEntries(f.advice,false),[]);
});
test('advice buttons invoke original component actions and reject a late stale, replaced or version-changed advice',()=>{
  const f=fixture();const click=()=>buttons(AdviceActions(f))[0].props.onClick();
  click();assert.equal(f.calls[0].action,'adopt');assert.equal(f.calls[0].owner,f.instance);
  for(const change of [{stale:true},{checking:true},{needsCheck:true},{id:'replacement'},{rows:[{...f.suggestion,version_no:4}]}]) {
    f.page.data.visitAdvice={...f.advice,...change};click();assert.equal(f.calls.length,1);
  }
  f.page.data.visitAdvice=f.advice;f.instance.data.saving=true;click();assert.equal(f.calls.length,1);
});
test('adopted advice preserves each task ID and no-task notes, without a mutation button',()=>{
  const f=fixture();f.suggestion.decision='adopted';f.suggestion.tasks=[{id:'a',assignee_name:'甲'},{id:'b',assignee_name:'乙'}];
  const tree=AdviceActions(f);const actions=buttons(tree);assert.equal(actions.length,2);
  actions[1].props.onClick();assert.deepEqual(JSON.parse(JSON.stringify(f.calls[0].payload)),{dataset:{id:'b'}});assert.equal(f.calls[0].action,'openTask');
  f.suggestion.decision='no_task';f.suggestion.decision_note='已通过会议安排';
  const handled=AdviceActions(f);assert.match(text(handled),/已通过会议安排/);assert.equal(buttons(handled).length,0);
});
test('advice decision permission, verification and stale status do not expose write actions',()=>{
  for(const change of [{stale:true},{checking:true},{needsCheck:true,checkError:'核对失败'}]) {const f=fixture();Object.assign(f.advice,change);assert.equal(buttons(AdviceActions(f)).length,0);}
  const f=fixture();f.instance.data.canDecide=false;assert.equal(buttons(AdviceActions(f)).length,0);
});
test('closing archived sheet or collapsing opportunity advice invalidates queued UI actions',()=>{
  const f=fixture('pages/visit-confirm/index');f.page.data={archived:true,showAdvice:true,advice:f.advice};
  invokeAdvice(f.page,f.advice,f.suggestion,f.invokeOn,'dismiss');assert.equal(f.calls.length,1);
  f.page.data.showAdvice=false;invokeAdvice(f.page,f.advice,f.suggestion,f.invokeOn,'dismiss');assert.equal(f.calls.length,1);
  f.page.route='pages/customer-assets/index';f.page.data={canOpportunityAdvice:true,opportunityTab:'overview',adviceExpanded:{overview:false},opportunityAdvice:{overview:f.advice}};
  invokeAdvice(f.page,f.advice,f.suggestion,f.invokeOn,'adopt');assert.equal(f.calls.length,1);
  f.page.data.adviceExpanded.overview=true;f.page.data.canOpportunityAdvice=false;invokeAdvice(f.page,f.advice,f.suggestion,f.invokeOn,'adopt');assert.equal(f.calls.length,1);
});
test('recording guidance starts collapsed, is keyboard-addressable and follows capability flags',()=>{
  const calls=[];const invoke=name=>calls.push(name);
  const collapsed=RecordingGuide({data:{},invoke});assert.equal(buttons(collapsed)[0].props['aria-expanded'],false);assert.doesNotMatch(text(collapsed),/首次拜访时|新商机时/);
  buttons(collapsed)[0].props.onClick();assert.deepEqual(calls,['toggleRecordingGuide']);
  const expanded=RecordingGuide({data:{recordingGuideVisible:true,canFirstVisit:true,canCreateOpportunity:true},invoke});assert.match(text(expanded),/首次拜访时/);assert.match(text(expanded),/本次产生新商机时/);
});
test('ranking detail uses followup_count and distinguishes zero from missing without inventing the denominator',()=>{
  const tree=RankingMembers({row:{memberSummary:'6 ÷ 3 = 2 次/人',members:[]}});
  const table=all(tree,node=>node.type==='Table')[0].props;
  assert.equal(table.columns[1].render(null,{followup_count:0}),'0 次');
  assert.equal(table.columns[1].render(null,{followup_count:6,record_count:99}),'6 次');
  assert.equal(table.columns[1].render(null,{}),'未取得');
  assert.match(text(table.columns[2].render(null,{current_member:false,customer_count:0})),/不计入当前人数/);
});
test('claim rows require server-derived eligibility and expose a labelled radio selection',()=>{
  const {CustomerClaim}=component('SmallPages.jsx');const calls=[];
  const tree=CustomerClaim({page:{},data:{customers:[],industryOptions:[],statusOptions:[]},invoke:(...args)=>calls.push(args)});
  const table=all(tree,node=>node.type==='SbTable')[0].props;
  table.onRowClick({id:'no',claimEligible:false});assert.equal(calls.length,0);
  const radio=table.columns[0].render(null,{id:'yes',name:'合成客户',claimEligible:true});assert.equal(radio.props['aria-label'],'选择 合成客户');radio.props.onChange();assert.equal(calls[0][1].dataset.id,'yes');
});
test('risk resolution form and action require both action and object permission',()=>{
  const {RiskDetail}=component('RiskReview.jsx');
  for (const [action,object,expected] of [[true,true,1],[true,false,0],[false,true,0],[true,undefined,0],[undefined,true,0]]) {
    const calls=[];
    const tree=RiskDetail({page:{},data:{canResolveRisk:action,risk:{status:'open',canResolve:object,version_no:1}},invoke:(...args)=>calls.push(args)});
    assert.equal(all(tree,node=>node.type==='SbTextarea').length,expected);
    const bars=all(tree,node=>node.type==='SbBottomBar');
    assert.equal(bars.length,expected);
    if(expected){bars[0].props.primary.onClick();assert.equal(calls[0][0],'confirmResolve');}
  }
});
test('new workflow styles use shared tokens and presentation emits the original kind contract',()=>{
  const css=readFileSync(new URL('../department-ui/workflow-completion.css',import.meta.url),'utf8');assert.deepEqual(inspectStyles(css,'workflow-completion.css'),[]);
  const calls=[];const tree=PresentationSwitch({data:{presentationOptions:[{value:'sales',label:'销售'},{value:'fde',label:'FDE'}],presentation:'sales'},invoke:(...args)=>calls.push(args)});
  all(tree,node=>node.type==='SbSegmented')[0].props.onChange('fde');assert.deepEqual(JSON.parse(JSON.stringify(calls)),[['changePresentation',{dataset:{kind:'fde'}}]]);
});
test('opportunity advice and team pickers follow capability flags rather than role names',()=>{
  const {default:OpportunityDetail}=component('OpportunityDetail.jsx');
  const data={opportunity:{},opportunityTab:'overview',opportunityAdvice:{overview:{id:'a',status:'ready',rows:[{id:'s'}]}},adviceExpanded:{overview:true}};
  assert.equal(OpportunityDetail.subcomponents({},data).length,0);
  assert.equal(all(OpportunityDetail({page:{},data,invoke(){}}),node=>node.props.className==='ds-od-ai').length,0);
  data.canOpportunityAdvice=true;assert.equal(OpportunityDetail.subcomponents({},data).length,1);
  const {default:Bi}=component('Bi.jsx');
  for(const [role,canViewTeam,count] of [['manager',false,0],['sales',true,1]]) {
    const tree=Bi({page:{},data:{role,canViewTeam,viewMode:'team',teamOptions:[{id:'all',name:'全部团队'}]},invoke(){}});
    assert.equal(all(tree,node=>node.type==='SbSegmented').length,count);
  }
});
