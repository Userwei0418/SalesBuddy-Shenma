import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import fs from 'node:fs';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {buildSync,transformSync} from 'esbuild';
import {MEETING_MONEY,meetingAmount,changeTone,countText} from '../department-ui/work-report/meeting-display.js';
import {rowPages} from '../department-ui/work-report/presentation-pages.js';

const root=fileURLToPath(new URL('../',import.meta.url));
const code=buildSync({absWorkingDir:root,entryPoints:['department-ui/work-report/MeetingBoardView.jsx'],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
const m=new Module(root+'meeting-view-test.cjs');m.filename=root+'meeting-view-test.cjs';m.paths=Module._nodeModulePaths(root);m._compile(code,m.filename);
const {MeetingBoardBody,MeetingAmounts}=m.exports;
const value=()=>({title:'本周经营变化',scopeLabel:'合成团队',periodLabel:'2026-10-05—2026-10-11',quarterLabel:'2026 Q4',asOfLabel:'截至 2026-10-10 20:00',
  counts:[{id:'customers',label:'新认领客户',value:2,unit:'家'},{id:'followups',label:'正式跟进',value:0,unit:'条'},{id:'new_opportunities',label:'新增商机',value:1,unit:'个'},{id:'lost',label:'丢单商机',value:null,unit:'个',note:'历史覆盖不足'}],
  amounts:[{id:'acv',opening:null,change:null,closing:'8000000000000000.01',reasons:['期初历史缺失']},{id:'recognized',opening:'0.00',change:'0.00',closing:'0.00'},
    {id:'collection',opening:'1200000.00',change:'-200000.00',closing:'1000000.00'}],
  changes:[{id:'a',name:'商机 A',customerName:'合成客户 A',ownerLabel:'成员甲',kind:'adjusted',kindLabel:'预测调整',reason:'客户付款计划调整',money:{collection:{opening:'1000000.00',change:'-300000.00',closing:'700000.00'}}},
    {id:'b',name:'商机 B',customerName:'合成客户 B',ownerLabel:'成员乙',kind:'adjusted',kindLabel:'预测调整',money:{collection:{opening:'200000.00',change:'100000.00',closing:'300000.00'}}}],
  members:[{id:'p1',name:'成员甲',summary:'2 条正式跟进'}],customers:[],opportunities:[],followups:[],nextSteps:[{id:'next1',title:'商机 A',text:'周一确认付款安排',ownerLabel:'成员甲',dateLabel:'2026-10-12'}],notices:[],definitions:['客户按认领审批生效时间统计'],detailNotes:[]});
const render=v=>renderToStaticMarkup(React.createElement(MeetingBoardBody,{value:v}));

test('meeting money presentation preserves cents and signed changes without binary arithmetic',()=>{
  assert.equal(meetingAmount('8000000000000000.01'),'800,000,000,000.000001');
  assert.equal(meetingAmount('-300000.00',{signed:true}),'-30');
  assert.equal(meetingAmount('100000.00',{signed:true}),'+10');
  assert.equal(meetingAmount('0.00',{signed:true}),'0');
  assert.equal(meetingAmount('-0.00',{signed:true}),'0');
  assert.equal(meetingAmount('0.01'),'0.000001');
  for(const missing of [null,undefined,'',NaN,12,'1e9','oops'])assert.equal(meetingAmount(missing),'—');
  assert.equal(changeTone('0.00'),'neutral');assert.equal(changeTone('-0.00'),'neutral');assert.equal(changeTone('-1'),'decrease');
  assert.equal(countText(null),'—');assert.equal(countText(0),'0');assert.equal(countText(-1),'—');
});

test('real report board shows opening/change/closing and preserves supplied totals independently of visible rows',()=>{
  const v=value(),before=JSON.stringify(v),html=render(v);
  for(const text of ['新认领客户','正式跟进','新增商机','丢单商机','ACV','2026 Q4','预计确收','预计回款','期初','本期变动','期末／当前','商机 A','商机 B','付款计划调整','下一步计划','周一确认付款安排'])assert.ok(html.includes(text),text);
  assert.match(html,/>120<\/span>/);assert.match(html,/>-20<\/span>/);assert.match(html,/>100<\/span>/);
  assert.match(html,/>-30<\/span>/);assert.match(html,/>\+10<\/span>/);
  v.changes=[];const noDetails=render(v);assert.match(noDetails,/>-20<\/span>/);assert.match(noDetails,/>100<\/span>/);
  assert.match(html,/期初历史缺失/);assert.match(html,/历史覆盖不足/);
  assert.match(html,/800,000,000,000\.000001/);
  assert.equal(JSON.stringify({...v,changes:JSON.parse(before).changes}),before);
});

test('amount cards keep unknown baselines distinct from explicit zero',()=>{
  const html=renderToStaticMarkup(React.createElement(MeetingAmounts,{items:[{id:'collection',opening:null,change:null,closing:'0',reasons:['没有可靠期初基准']}],quarterLabel:'2026 Q4'}));
  assert.match(html,/没有可靠期初基准/);assert.match(html,/>0<\/span>/);assert.match(html,/>—<\/span>/);assert.doesNotMatch(html,/>\+0<\/span>/);
});

test('board is readonly, uses accessible detail tabs and escapes business text',()=>{
  const v=value();v.title='<img src=x onerror=alert(1)>';v.nextSteps[0].text='<script>alert(1)</script>';
  const html=render(v);
  assert.match(html,/&lt;img/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<img src=x|<script>alert|<textarea|contenteditable/);
  assert.equal((html.match(/role="tab"/g)||[]).length,4);assert.match(html,/role="tabpanel"/);
  assert.match(html,/已登记计划 · 尚未完成/);assert.doesNotMatch(html,/重写本节|提交共享|生成中/);
});

function wrapperHarness(props) {
  const slots=[];let cursor=0;
  const target={exports:{}};
  vm.runInNewContext(transformSync(fs.readFileSync(root+'department-ui/work-report/MeetingBoardView.jsx','utf8'),{loader:'jsx',format:'cjs'}).code,
    {module:target,exports:target.exports,require:name=>name==='react'?{...React,useState:initial=>{
      const index=cursor++;if(!(index in slots))slots[index]=initial;
      return [slots[index],update=>{slots[index]=typeof update==='function'?update(slots[index]):update;}];
    }}:name==='antd'?{Button:'button'}:name.endsWith('icons.jsx')?{NavIcon:'icon'}:name.endsWith('ReportPresentation.jsx')?{default:'presentation',__esModule:true}:
      name.endsWith('meeting-display.js')?{MEETING_MONEY}:name.endsWith('presentation-pages.js')?{rowPages}:{}});
  return ()=>{cursor=0;return target.exports.default(props);};
}
test('entering projection freezes its facts while leaving normal detail selection and mutation controls behind',()=>{
  const v=value(),props={value:v,actions:React.createElement('button',null,'保存草稿'),onOpenOpportunity(){},onOpenCustomer(){}},render=wrapperHarness(props);
  let [normal,presentation]=render().props.children;
  normal.props.onSelection({tab:'followups',metric:'acv'});
  normal.props.actions.props.children[1].props.onClick();
  [normal,presentation]=render().props.children;
  assert.equal(presentation.props.open,true);const slides=presentation.props.slides;
  assert.equal(slides[0].title,'本期汇总');assert.ok(slides.some(row=>row.title.includes('预计回款变动')));
  assert.equal(presentation.props.onOpenOpportunity,undefined);assert.equal(presentation.props.actions,undefined);
  props.value={...v,title:'后续已刷新',amounts:[]};
  [normal,presentation]=render().props.children;
  assert.equal(presentation.props.slides,slides);assert.equal(presentation.props.title,'本周经营变化');
  presentation.props.onClose();
  [normal,presentation]=render().props.children;assert.equal(presentation.props.open,false);assert.equal(normal.props.selection.tab,'followups');assert.equal(normal.props.selection.metric,'acv');assert.equal(normal.props.value,props.value);
});

test('presentation paginates all changes and uses separate member totals without exposing business navigation',()=>{
  const v=value();v.changes=Array.from({length:12},(_,i)=>({...v.changes[0],id:String(i),name:`商机-${i}`}));
  const member={...value(),scopeLabel:'成员甲',amounts:[{id:'collection',opening:'1000000',change:'-1000000',closing:'0'}],changes:[]};
  const before=JSON.stringify(v),slides=m.exports.meetingSlides(v,{members:[{id:'p1',name:'成员甲',value:member},{id:'p2',name:'成员乙',error:'成员统计未完整取得'}]});
  const changeSlides=slides.filter(row=>row.key.startsWith('team:changes:collection'));
  assert.equal(changeSlides.length,3);
  const html=changeSlides.map(row=>renderToStaticMarkup(row.content)).join('');
  for(let i=0;i<12;i++)assert.equal((html.match(new RegExp(`>商机-${i}<`,'g'))||[]).length,1);
  assert.doesNotMatch(html,/查看商机|href=/);
  const team=renderToStaticMarkup(slides[0].content),individual=renderToStaticMarkup(slides.find(row=>row.key==='member:p1').content);
  assert.match(team,/>-20<\/span>/);assert.match(individual,/>-100<\/span>/);assert.doesNotMatch(individual,/>-20<\/span>/);
  assert.match(renderToStaticMarkup(slides.find(row=>row.key==='member:p2').content),/成员统计未完整取得/);
  assert.equal(JSON.stringify(v),before);
});
