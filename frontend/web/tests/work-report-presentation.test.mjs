import {test} from 'node:test';
import assert from 'node:assert/strict';
import {schemeEditor,schemeConfig,materialView,metricViews,sourceViews,allSchemes,reportRow,supplementView} from '../department-ui/work-report/presentation.js';
import {salesWeekView,SALES_WEEK_FACTS} from '../department-ui/work-report/sales-week.js';

const config=()=>({sections:[{id:'progress',title:'重点进展',enabled:true,instructions:'先写结果'}],group_by:'customer',style:{audience:'直属负责人',tone:'concise',length:'medium',voice:'first_person',language:'zh-CN',focus:['说明问题']},reference_texts:[{id:'00000000-0000-4000-8000-000000000001',version_no:3,text:'仅作风格参考'}],default_period:'two_weeks',default_sources:['visit','opportunity']});
test('scheme round trip preserves private references, section instructions and calendar period',()=>{
 const original=config(),editor=schemeEditor({name:'团队周报',config:original});
 assert.equal(editor.period,'biweek');assert.deepEqual(schemeConfig(editor),original);
 editor.references[0].text='调整风格参考';editor.sections[0].instructions='把问题单列';editor.style.focus='说明问题\n列出下周计划';
 const saved=schemeConfig(editor);assert.equal(saved.reference_texts[0].version_no,4);assert.equal(saved.sections[0].instructions,'把问题单列');assert.deepEqual(saved.style.focus,['说明问题','列出下周计划']);
 assert.equal('originalText' in saved.reference_texts[0],false);assert.equal(original.reference_texts[0].text,'仅作风格参考');
 assert.deepEqual(reportRow({id:'r',title:'报告',period:{label:'本周'},scope:{label:'本人'},scheme:{name:'团队周报',scheme_id:'s',version_no:1,config_hash:'hash'},status:'private',updated_at:'2026-10-09T08:00:00Z'}).schemeLabel,'团队周报');
});
test('scheme limits prevent silently dropping reference material and writing requirements',()=>{
 let editor=schemeEditor({name:'方案',config:config()});editor.references=Array.from({length:6},(_,i)=>({id:String(i),version_no:1,text:'范文'}));assert.throws(()=>schemeConfig(editor),/最多5篇/);
 editor.references=Array.from({length:4},(_,i)=>({id:String(i),version_no:1,text:'文'.repeat(16000)}));assert.throws(()=>schemeConfig(editor),/合计最多60000/);
 editor.references=[];editor.style.focus='要求'.repeat(121);assert.throws(()=>schemeConfig(editor),/每行最多240/);
});
test('partial and unknown sources remain explicit; current-state facts are not historical cutoff facts',()=>{
 const snapshot={status:'ready',period:{label:'本周'},scope:{label:'本人'},data_as_of:'2026-10-09T08:00:00Z',selection:{supplements:[]},coverage:[{source_type:'visit',selected:true,status:'partial',included:2,total:null,missing_reasons:['后续记录暂未取得']},{source_type:'task',selected:false,status:'not_selected',included:0,total:null,missing_reasons:[]}]};
 const view=materialView(snapshot,config(),true);assert.equal(view.coverageState,'partial');assert.equal(view.sources.length,1);assert.equal(view.sources[0].countLabel,'已纳入 2 条 · 总量未知');assert.equal(view.sources[0].complete,false);
 assert.match(metricViews([{metric_id:'risk',label:'当前风险',display_value:'2 项',complete:true,definition:'当前未解决',period_basis:'current_at_snapshot'}])[0].note,/取数时点/);
 assert.match(sourceViews([{source_id:'risk:r',source_type:'risk',title:'风险',text:'待处理',contributors:[],business_at:null,observed_at:'2026-10-09T08:00:00Z',state_kind:'current'}])[0].date,/取数时点/);
});
test('scheme picker loads beyond the first page without hiding failures or identity cancellation',async()=>{
 const calls=[],items=Array.from({length:123},(_,i)=>({id:'scheme-'+i}));
 assert.deepEqual(await allSchemes({schemes:async({offset,limit})=>{calls.push(offset);return {items:items.slice(offset,offset+limit),has_more:offset+limit<items.length,next_offset:offset+limit}}}),items);assert.deepEqual(calls,[0,100]);
 await assert.rejects(allSchemes({schemes:async()=>({items:[{id:'same'}],has_more:true,next_offset:100})}),/列表已变化/);
 const controller=new AbortController();await assert.rejects(allSchemes({schemes:async()=>{controller.abort();return{items:[],has_more:false}}},controller.signal),{name:'AbortError'});
 await assert.rejects(allSchemes({schemes:async()=>{throw Error('权限已变化')}}),/权限已变化/);
});

test('plans, actual facts and current state retain distinct dates and labels',()=>{
 const source={source_id:'opportunity:o',source_type:'opportunity',title:'合成商机',text:'计划回款',contributors:[],observed_at:'2026-10-09T08:00:00Z',business_at:'2026-10-13T08:00:00Z'};
 const planned=sourceViews([{...source,state_kind:'planned'}])[0];
 assert.match(planned.kindLabel,/计划，尚未发生/);assert.match(planned.date,/计划日期.*2026\/10\/13/);
 const undated=sourceViews([{...source,state_kind:'planned',business_at:null}])[0];
 assert.match(undated.date,/计划日期未登记/);assert.doesNotMatch(undated.date,/计划日期 2026\/10\/09/);
 const current=sourceViews([{...source,state_kind:'current'}])[0];
 assert.match(current.date,/取数时点.*2026\/10\/09/);assert.doesNotMatch(current.date,/2026\/10\/13/);
 assert.match(sourceViews([{...source,state_kind:'occurred'}])[0].kindLabel,/已发生/);
});
test('forecast and actual metrics are labelled from server basis without altering amounts or summing them',()=>{
 const base={metric_id:'collection',label:'回款',display_value:'0.00 CNY',complete:true,definition:'正式流水'};
 const actual=metricViews([{...base,period_basis:'occurred_in_period'}])[0];
 const planned=metricViews([{...base,metric_id:'forecast',period_basis:'planned_in_period',display_value:'待补充',complete:false}])[0];
 assert.match(actual.note,/已发生/);assert.equal(actual.display,'0.00 CNY');
 assert.match(planned.note,/计划，尚未发生/);assert.equal(planned.complete,false);assert.equal(planned.display,'待补充');
});

test('six-section coverage consumes the server population, preserves unknowns, and never derives a first threshold from current probability',()=>{
 const sections=[{id:'ppl_first_30',title:'本周新增 ≥30%',enabled:true},{id:'ppl_open_50',title:'全部在推 ≥50%',enabled:true}];
 const facts={facts_contract:SALES_WEEK_FACTS,data_as_of:'2026-10-09T08:00:00Z',next_period:{start:'2026-12-28T00:00:00+08:00',end_exclusive:'2027-01-04T00:00:00+08:00'},
  sections:[{section_id:'ppl_first_30',availability:'unknown',included:0,population_total:null,population_complete:false,content_complete:false,missing_reasons:['首次达到 30% 的历史不完整']},
    {section_id:'ppl_open_50',availability:'available',included:2,population_total:2,population_complete:true,content_complete:true,missing_reasons:[]}],
  opportunity_facts:[{probability:70,progress_state:'no_recorded_progress_in_week'}],finance:{precision_warning:'not_allocated_to_days',unallocated_period_count:2}};
 const before=JSON.stringify(facts),view=salesWeekView(facts,sections);
 assert.equal(view.sections[0].state,'暂无法核定');assert.equal(view.sections[0].coverage,'已纳入 0 / 未知 项');
 assert.match(view.sections[0].notes.join(''),/历史不完整/);assert.equal(view.sections[1].coverage,'已纳入 2 / 2 项');
 assert.match(view.asOf,/不代表所选历史周末/);assert.match(view.nextPeriod,/2026年12月28日.*2027年1月4日/);
 assert.match(view.precisionNote,/2 个期间.*无法分配至本周/);assert.equal(JSON.stringify(facts),before);
 assert.equal(salesWeekView(null,sections),null);assert.equal(salesWeekView({...facts,facts_contract:'future'},sections).supported,false);
});

test('actual material retains day or quarter precision while planned tasks are never labelled completed',()=>{
 const source={source_id:'actual:safe-ref',source_type:'actual',title:'合成回款',text:'正式记录',contributors:[],observed_at:'2026-10-09T08:00:00Z',business_at:null,state_kind:'occurred'};
 const quarter=sourceViews([{...source,actual_lineage:{period_precision:'quarter',year:2026,quarter:4}}])[0];
 assert.match(quarter.kindLabel,/正式回款/);assert.match(quarter.date,/第 4 季度.*不能计入周回款/);assert.doesNotMatch(quarter.date,/00:00/);
 const day=sourceViews([{...source,actual_lineage:{period_precision:'day',occurred_on:'2026-10-08'}}])[0];
 assert.equal(day.date,'回款日期 2026-10-08（按日登记）');
 assert.match(sourceViews([{...source,source_type:'task',state_kind:'planned'}])[0].kindLabel,/任务计划/);
});

test('unrecognized weekly facts prevent generation without replacing the saved custom outline',()=>{
 const snapshot={status:'ready',period:{label:'本周'},scope:{label:'本人'},data_as_of:'2026-10-09T08:00:00Z',selection:{supplements:[]},coverage:[],sales_week_facts:{facts_contract:'future'}};
 const view=materialView(snapshot,config(),true);assert.equal(view.canGenerate,false);assert.deepEqual(view.sections,config().sections);
 const legacy=materialView({...snapshot,sales_week_facts:null},config(),true);assert.equal(legacy.canGenerate,true);assert.equal(legacy.weekly,null);
});
test('supplement provenance retains its target without upgrading expected collection to a system fact',()=>{
 const item={id:'supplement',text:'预计回款，需确认',section_id:'next_week_collection',declared_kind:'plan'};
 const view=supplementView(item);assert.equal(view.title,'用户补充 · 下周预计回款');assert.equal(view.kindLabel,'用户补充计划，未核实');assert.equal(view.canOpen,false);
 assert.equal(supplementView({...item,section_id:undefined,declared_kind:'context'}).title,'用户补充材料');
});
