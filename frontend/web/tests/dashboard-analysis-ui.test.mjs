import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {buildSync} from 'esbuild';

const root=fileURLToPath(new URL('../',import.meta.url));
function load(entry) {
  const code=buildSync({absWorkingDir:root,entryPoints:[entry],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
  const module=new Module(root+'dashboard-analysis-test.cjs');module.filename=root+'dashboard-analysis-test.cjs';module.paths=Module._nodeModulePaths(root);module._compile(code,module.filename);return module.exports;
}
const ui=load('department-ui/DashboardPipelineActivity.jsx'),charts=load('department-ui/DashboardAnalysisCharts.jsx'),bi=load('department-ui/Bi.jsx');
const metric={eligible_count:1,excluded_count:0,unknown_eligibility_count:0,value:'0',known_subtotal:'0',known_amount_count:1,missing_amount_count:0,coverage:'complete',missing_reasons:[]};
const stages=[{code:'30',label:'需求明确',count:0,metrics:{forecast_collection:metric,forecast_recognized:{...metric,eligible_count:0,excluded_count:1}}},
  {code:'70',label:'商务推进',count:2,metrics:{forecast_collection:{...metric,value:null,known_subtotal:'10000',coverage:'partial',missing_amount_count:1},forecast_recognized:metric}}];
const value={eligible_count:2,excluded_count:1,unknown_eligibility_count:0,stages,missing_reasons:[]};
const renderPipeline=result=>renderToStaticMarkup(React.createElement(ui.PipelinePanel,{result,scopeLabel:'授权团队 · 2026 Q4'}));

test('pipeline uses original chart card controls with keyboard-equivalent stage filters and no fabricated counts',()=>{
  const html=renderPipeline({value});
  assert.match(html,/季度 PPL 阶段分布/);assert.match(html,/全部阶段/);assert.match(html,/需求明确 · 0/);assert.match(html,/商务推进 · 2/);
  assert.match(html,/只筛选本卡/);assert.match(html,/查看图表数据/);
  const option=charts.funnelOption(stages),series=option.series[0];assert.deepEqual(series.data.map(row=>row.value),[0,2]);
  const api={getWidth:()=>400,getHeight:()=>300};
  const shape=(source,index)=>source.renderItem({dataIndex:index},api).children.find(row=>row.type==='group');
  const empty=shape(series,0),full=shape(series,1);
  const small=shape(charts.funnelOption(stages.map((row,index)=>({...row,count:index?1:100}))).series[0],1);
  const bounds=mark=>{
    const coordinates=mark.children[0].shape.pathData.match(/-?\d+(?:\.\d+)?(?:e[-+]?\d+)?/gi).map(Number);
    const xs=coordinates.filter((_,index)=>index%2===0),ys=coordinates.filter((_,index)=>index%2===1);
    return {width:Math.max(...xs)-Math.min(...xs),height:Math.max(...ys)-Math.min(...ys)};
  };
  assert.equal(bounds(small).height,bounds(full).height);assert.equal(small.y,full.y);
  assert.ok(bounds(small).width<bounds(full).width);assert.equal(small.scaleY,undefined);
  const half=shape(charts.funnelOption(stages.map((row,index)=>({...row,count:index?50:100}))).series[0],1);
  assert.equal(bounds(half).width,bounds(full).width/2);assert.equal(bounds(half).height,bounds(full).height);
  assert.match(full.children[0].shape.pathData,/Q/);assert.equal(empty.children[0].style.fill,'transparent');
  assert.equal(empty.children[1].clipPath.shape.pathData,empty.children[0].shape.pathData);
  const zeros=charts.funnelOption(stages.map(row=>({...row,count:0}))).series[0];
  for(let index=0;index<stages.length;index++){const mark=shape(zeros,index);assert.equal(bounds(mark).width,28);assert.equal(bounds(mark).height,bounds(full).height);assert.ok(mark.children[1].children.length>0);}
  assert.ok(bounds(small).width>=bounds(shape(zeros,1)).width);assert.match(option.aria.label.description,/高度固定，仅宽度/);
  assert.deepEqual(series.data.map(row=>row.code),['30','70']);
  assert.doesNotMatch(renderPipeline({loading:true,value}),/商务推进 · 2/);
  assert.doesNotMatch(renderPipeline({error:'数据读取失败',value}),/商务推进 · 2/);
});

test('pipeline renders before data arrives and when refresh clears previous data',()=>{
  for(const result of [{loading:true},{loading:true,value:undefined},{loading:true,value:null},{error:'图表读取失败'}]) {
    let html;assert.doesNotThrow(()=>{html=renderPipeline(result);});
    assert.doesNotMatch(html,/商务推进 · 2/);
  }
});

test('bar and donut data preserve null, zero and unknown without inventing an empty-ring total',()=>{
  const option=charts.stageBarsOption([{label:'阶段',amounts:{forecast_collection:{value:null,text:'未填写'},forecast_recognized:{value:0,text:'0.00'}}}]);
  assert.equal(option.series[0].data[0],null);assert.equal(option.series[1].data[0],0);
  const donut=charts.activityDonutOption([{label:'近期',count:1},{label:'超期',count:2},{label:'待核对',count:1}]);assert.deepEqual(donut.series[0].data.map(row=>row.value),[1,2,1]);
  assert.equal(donut.series[0].stillShowZeroSum,false);
});

test('activity summary renders server aggregates independently of the first detail page',()=>{
  const activity={active_count:40,recent_count:10,overdue_count:29,never_count:4,unknown_count:1,missing_reasons:[],window:{start:'2026-10-19T00:00:00+08:00'}};
  const html=renderToStaticMarkup(React.createElement(ui.ActivityPanel,{result:{value:activity},selection:{personal:true,member_id:null,team_groups:[],year:2026,quarter:4},scopeLabel:'本人 · 2026 Q4',service:{},onRefresh(){}}));
  assert.doesNotMatch(html,/25\.0%/);assert.match(html,/跟进比例待核对/);assert.match(html,/已确认 10 \/ 可见 40 个/);assert.match(html,/共 29 个/);assert.match(html,/4 个尚无正式跟进/);assert.match(html,/1 个跟进情况待核对/);
  const page=renderToStaticMarkup(React.createElement(ui.ActivityRows,{result:{value:{items:[],known_total:15,total:15,data_as_of:'2026-10-25T12:00:00+08:00'}},position:0,setPosition(){},onRefresh(){}}));
  assert.match(page,/每页 7 个/);assert.match(page,/title="3"/);assert.doesNotMatch(page,/ant-pagination-options-size-changer/);
});

test('legacy business analysis starts collapsed and only mounts charts after expansion',()=>{
  const page=renderToStaticMarkup(React.createElement(bi.default,{page:{},data:{viewMode:'personal',memberLabel:'本人'},invoke(){}}));
  assert.match(page,/季度监控/);assert.match(page,/季度商机与跟进/);
  assert.match(page,/aria-expanded="false" aria-controls="dashboard-business-analysis"/);
  assert.doesNotMatch(page,/aria-label="经营概览"|各阶段商机 ACV 有多少/);
  let mounts=0;
  const Child=()=>{mounts++;return React.createElement('div',null,'已登记回款与历史图表');};
  const fold=expanded=>renderToStaticMarkup(React.createElement(bi.BiMoreAnalysis,{expanded,onToggle(){}},React.createElement(Child)));
  assert.doesNotMatch(fold(false),/已登记回款与历史图表/);assert.equal(mounts,0);
  const opened=fold(true);assert.match(opened,/aria-expanded="true"/);assert.match(opened,/已登记回款与历史图表/);assert.equal(mounts,1);
});
