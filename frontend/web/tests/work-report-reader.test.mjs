import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import Module,{createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {buildSync,transformSync} from 'esbuild';

// Render the real presentation components without starting a browser, API or
// model request. The generated text is synthetic and never contains user data.
const root=fileURLToPath(new URL('../',import.meta.url)),require=createRequire(import.meta.url);
const compiled=buildSync({absWorkingDir:root,entryPoints:['department-ui/work-report/DocumentReader.jsx'],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
const readerModule=new Module(root+'reader-test.cjs');readerModule.filename=root+'reader-test.cjs';readerModule.paths=Module._nodeModulePaths(root);readerModule._compile(compiled,readerModule.filename);
const Reader=readerModule.exports.default;
const item=(text,kind='fact',refs={})=>({kind,text,source_refs:[],metric_refs:[],...refs});
const content=()=>({title:'合成销售周报',sections:[
  {id:'work_summary',title:'本周工作总结',items:[item('本周跟进 {{metric:visits}}。','fact',{metric_refs:['visits']})]},
  {id:'week_collection',title:'本周回款',items:[item('本周回款 {{metric:collection}}。')]},
  {id:'next_week_plan',title:'下周工作计划',items:[item('下周确认方案。\n尚未执行。','plan',{source_refs:['visit:synthetic']})]},
  {id:'next_week_collection',title:'下周预计回款',items:[item('预计回款待确认。','user_supplement')]},
  {id:'ppl_first_30',title:'本周新增 ≥30% PPL',items:[]},
  {id:'ppl_open_50',title:'≥50% PPL 进展与下一步',items:[item('暂无本周新进展。','manual')]},
],missing_data:['未登记预计回款，不按零值处理。']});
const metrics=[{metric_id:'visits',label:'正式跟进',display_value:'2 条',complete:true,period_basis:'occurred_in_period',definition:'按跟进日期'},
  {metric_id:'collection',label:'本周回款',display_value:'12,640,000.00 CNY',complete:true,period_basis:'occurred_in_period',definition:'按正式流水'},
  {metric_id:'forecast',label:'预计回款',display_value:'待补充',complete:false,period_basis:'planned_in_period',definition:'计划未核实'}];
const render=props=>renderToStaticMarkup(React.createElement(Reader,{value:content(),metrics,status:'private',meta:'第 1 版 · 生成草稿',periodLabel:'2026-10-05—2026-10-11',scopeLabel:'本人',schemeLabel:'销售六节周报',...props}));

test('reading is a complete six-section report with exact snapshot units and no editing inputs',()=>{
  const value=content(),before=JSON.stringify(value),html=render({value,onEdit(){},onSources(){}});
  assert.match(html,/报告阅读视图/);assert.match(html,/<h1>合成销售周报<\/h1>/);
  assert.equal((html.match(/class="wr-reader-section-head"/g)||[]).length,6);
  assert.match(html,/12,640,000\.00 CNY/);assert.doesNotMatch(html,/12640000.*万元/);
  assert.match(html,/本周跟进 2 条/);assert.match(html,/计划，尚未发生/);assert.match(html,/统计尚未完整取得|计划未核实/);
  assert.match(html,/未登记预计回款，不按零值处理/);assert.match(html,/本节尚无正文/);
  assert.match(html,/用户补充/);assert.match(html,/人工编辑/);assert.match(html,/编辑报告/);assert.match(html,/报告章节目录/);
  assert.doesNotMatch(html,/<textarea|<input|contenteditable|重写本节|添加段落|移除/);
  assert.equal(JSON.stringify(value),before);
});

test('authorized shared reading has no owner-only controls and does not expose source identifiers',()=>{
  const html=render({status:'shared'});
  assert.match(html,/已共享/);assert.doesNotMatch(html,/编辑报告|继续编辑|查看来源|查看材料来源|管理共享|复制与下载|visit:synthetic/);
});

test('saved overview facts appear in reading and projection without refreshing or changing the frozen material',()=>{
  const facts=JSON.parse(fs.readFileSync(new URL('./fixtures/work-report-meeting/ready-snapshot.response.json',import.meta.url),'utf8')).meeting_facts;
  facts.contract_version='work_report.overview_facts.v1';facts.scope.kind='team';
  const before=JSON.stringify(facts),html=render({overviewFacts:facts,status:'shared'});
  const slides=readerModule.exports.reportSlides({value:content(),metrics,overviewFacts:facts,status:'shared'});
  const projected=slides.map(slide=>renderToStaticMarkup(slide.content)).join('');
  for(const result of [html,projected]){
    assert.match(result,/ACV/);assert.match(result,/预计回款/);assert.doesNotMatch(result,/事实响应不完整|统计暂不可用/);
    assert.doesNotMatch(result,/编辑报告|确认并共享|生成报告|<textarea/);
  }
  assert.ok(slides.some(slide=>slide.title==='本期汇总'));assert.equal(JSON.stringify(facts),before);
});

test('projected weekly reading retains all six sections and exposes only exit controls',()=>{
  const html=render({presenting:true,onExit(){}});
  assert.equal((html.match(/class="wr-reader-section-head"/g)||[]).length,6);
  assert.match(html,/退出会议模式 · Esc/);assert.match(html,/12,640,000\.00 CNY/);
  assert.doesNotMatch(html,/编辑报告|继续编辑|确认并共享|生成报告草稿|<textarea/);
});

test('unknown metrics and unsaved preview remain explicit and cannot be shared or exported',()=>{
  const html=render({metrics:[],dirty:true,onEdit(){},onSave(){},onShare(){},onExport(){}});
  assert.match(html,/统计暂不可用/);assert.match(html,/未保存预览/);assert.match(html,/保存前不会更新已共享的报告/);
  for(const label of ['复制与下载','确认并共享','保存草稿'])assert.match(html,new RegExp('<button[^>]*disabled[^>]*>[\\s\\S]*?<span>'+label+'<\\/span><\\/button>'));
  assert.match(html,/继续编辑/);
});

test('custom section order, long text and HTML-like content are preserved as plain text',()=>{
  const text='<img src=x onerror=alert(1)>\n'+ '长内容'.repeat(1200);
  const html=render({value:{title:'自定义月报',sections:[{id:'custom_b',title:'先看计划',items:[item(text,'plan')]},{id:'custom_a',title:'再看总结',items:[]}],missing_data:[]}});
  assert.ok(html.indexOf('先看计划')<html.indexOf('再看总结'));assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);assert.doesNotMatch(html,/<img src=x/);
  assert.match(html,new RegExp('长内容'.repeat(1200)));
});

test('saved weekly presentation has all six chapters, readonly controls and preserves every long paragraph',()=>{
  const value=content(),long='逐项复核🙂。'.repeat(450);value.sections[0].items.push(item(long,'manual'));
  const before=JSON.stringify(value),slides=readerModule.exports.reportSlides({value,metrics,status:'shared',periodLabel:'2026-10-05—2026-10-11',scopeLabel:'本人',onEdit(){},onShare(){},onSources(){}});
  assert.equal(slides[0].key,'cover');
  for(const section of value.sections)assert.ok(slides.some(row=>row.group===section.title),section.title);
  const html=slides.map(row=>renderToStaticMarkup(row.content)).join('');
  assert.equal((html.match(/逐项复核🙂。/g)||[]).length,450);
  assert.match(html,/12,640,000\.00 CNY/);assert.match(html,/续页/);assert.match(html,/本节尚无正文/);
  assert.doesNotMatch(html,/编辑报告|确认并共享|查看材料来源|visit:synthetic|<textarea|contenteditable/);
  assert.equal(JSON.stringify(value),before);
});

function viewHarness(initial={}) {
  let editing,props={value:content(),metrics,readOnly:false,...initial};
  const m={exports:{}};
  vm.runInNewContext(transformSync(fs.readFileSync(root+'department-ui/work-report/DocumentView.jsx','utf8'),{loader:'jsx',format:'cjs'}).code,
    {module:m,exports:m.exports,require:name=>name==='react'?{...React,useState:value=>[editing??value,next=>{editing=next}]}:name.endsWith('DocumentReader.jsx')?{default:'reader',__esModule:true}:{default:'editor',__esModule:true}});
  return {render:()=>m.exports.default(props),update:patch=>{props={...props,...patch}}};
}
test('read/edit/unsaved preview switches share the same draft and never invoke save, share or generation',()=>{
  const calls=[],h=viewHarness({onSave:()=>calls.push('save'),onShare:()=>calls.push('share'),onRewrite:()=>calls.push('rewrite')});
  let view=h.render();assert.equal(view.type,'reader');view.props.onEdit();view=h.render();assert.equal(view.type,'editor');
  const edited=content();edited.title='尚未保存的新标题';h.update({value:edited,dirty:true});
  h.render().props.onPreview();view=h.render();assert.equal(view.type,'reader');assert.equal(view.props.value,edited);assert.equal(view.props.dirty,true);
  view.props.onEdit();assert.equal(h.render().props.value,edited);assert.deepEqual(calls,[]);
  h.update({busy:true});assert.equal(h.render().props.busy,true);
  h.update({readOnly:true});view=h.render();assert.equal(view.type,'reader');assert.equal(view.props.onEdit,undefined);
});


test('empty chapters explain saved state and preserve unclassified material gaps without inventing section evidence',()=>{
  const value=content();value.sections[2].items=[];value.sections[3].items=[];
  const before=JSON.stringify(value),html=render({value});
  assert.match(html,/报告已保存，本节尚未形成可展示的内容/);
  assert.match(html,/计划日期、客户或商机、负责人/);assert.match(html,/预计回款日期、金额、币种/);
  assert.match(html,/首次达到 30% 需要阶段变更历史佐证/);
  assert.match(html,/<details class="wr-reader-material-notes"><summary>/);
  assert.match(html,/以下是整份报告的已记录材料缺口/);
  assert.doesNotMatch(html,/暂无已保存内容|<details[^>]*open/);
  assert.equal(JSON.stringify(value),before);
  const preview=render({value,dirty:true});assert.match(preview,/当前预览尚未填写本节内容/);
  assert.doesNotMatch(preview,/报告已保存，本节尚未形成/);
});

test('reading formats frozen CNY money and controlled source labels without rewriting prose or snapshots',()=>{
 const money={metric_id:'amount',label:'商机登记金额',kind:'amount',currency:'CNY',unit:'原币',value:'1233760100.00',display_value:'1233760100.00',complete:true};
 const source='快照时点的当前状态：qualified；open。不能据此认定本期阶段推进。';
 const value={title:'冻结阅读示例',missing_data:[],sections:[{id:'summary',title:'汇总',items:[
  item('商机登记金额 {{metric:amount}}。','fact',{metric_refs:['amount']}),
  item('取数时当前状态 · 商机登记金额：{{metric:amount}} 原币。','summary',{metric_refs:['amount']}),
  item('取数时当前状态 · 商机登记金额：{{metric:amount}}（原币）。','fact',{metric_refs:['amount']}),
  item('本期已发生 · 回款：{{metric:yuan}} 元。','summary',{metric_refs:['yuan']}),
  item(source,'fact',{source_refs:['opportunity:synthetic']}),
  item('客户手工备注：qualified / open。','manual'),
  item(source,'manual',{source_refs:['opportunity:synthetic']}),
 ]}]};
 const frozen=[money,{...money,metric_id:'yuan',label:'回款',unit:'元'}];
 const before=JSON.stringify({value,money}),html=render({value,metrics:frozen});
 assert.match(html,/123,376\.01/);assert.match(html,/万元/);assert.match(html,/查看原始金额/);
 assert.match(html,/商机确认（30%）；推进中/);assert.match(html,/客户手工备注：qualified \/ open/);
 assert.doesNotMatch(html,/万元[\s（]*(?:原币|元)/);
 assert.match(html,/快照时点的当前状态：qualified；open/,'manual frozen text stays literal');
 assert.equal(JSON.stringify({value,money}),before);
 const projected=readerModule.exports.reportSlides({value,metrics:frozen}).map(slide=>renderToStaticMarkup(slide.content)).join('');
 assert.match(projected,/123,376\.01/);assert.match(projected,/商机确认（30%）；推进中/);
});

test('AI fallback is prominent and safely explained, while saved meeting boards are not AI failures',()=>{
 const fallback={revision:{origin:'basic_summary',run_id:'synthetic-run'},generation_issue:{code:'AI_SOURCE_REFERENCES_MISSING',message:'internal trace must not render',retryable:true}};
 let html=render({generationDocument:fallback,onRetryGeneration(){}});
 assert.match(html,/AI未完成，仅基础汇总/);assert.match(html,/缺少可核对的来源引用/);assert.match(html,/重新生成 AI 报告/);
 assert.doesNotMatch(html,/internal trace/);
 html=render({generationDocument:{...fallback,generation_issue:{code:'FUTURE_INTERNAL_TRACE',message:'secret diagnostic',retryable:false}},onRetryGeneration(){}});
 assert.match(html,/已保留可核对的基础材料汇总/);assert.doesNotMatch(html,/secret diagnostic|重新生成 AI 报告/);
 assert.doesNotMatch(render({generationDocument:{revision:{origin:'basic_summary',run_id:null}}}),/AI未完成/);
});
