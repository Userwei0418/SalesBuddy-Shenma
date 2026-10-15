import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {buildSync} from 'esbuild';

// Real React controls and shared display values, without a browser or network.
const root=fileURLToPath(new URL('../',import.meta.url));
const compiled=buildSync({absWorkingDir:root,entryPoints:['department-ui/OpportunityMaterials.jsx'],bundle:true,write:false,platform:'node',format:'cjs',packages:'external'}).outputFiles[0].text;
const m=new Module(root+'finance-form-test.cjs');m.filename=root+'finance-form-test.cjs';m.paths=Module._nodeModulePaths(root);m._compile(compiled,m.filename);
const Materials=m.exports.default;
const data=()=>({quarterOptions:[{label:'2026 Q4',year:2026,quarter:4}],quarterIndex:0,productLineReadonly:true,
 opportunityProductLines:[{id:'p1',name:'合成产品甲'},{id:'p2',name:'合成产品乙'}],legacyProductLine:'历史 <产品线>',
 form:{materials_dirty:true,trade_relation:'direct',material_lines:[{client_key:'line-a',material_id:'m1',quantity:'1',unit_price:'8000000000000000.01',usage:'self_use',
  catalog_snapshot:{name:'合成服务',recognition_rule:'monthly',product_line_name:'合成产品甲',unit:'套'},inputVisibility:{monthly:true,oa:true,accounts:true},line_amount_text:'8000000000000000.01'}]},
 materialPreview:{pending:false,loading:false,draftRevision:9,result:{reference_kind:'sales_forecast_draft_reference',persisted:false,draft_revision:9},
  display:{sourceLabel:'当前输入试算（尚未保存）',periodLabel:'2026年 Q4',summary:'含税参考：0.00 元；不含税参考：0.00 元',lines:[],reasons:[]}}});
const render=d=>renderToStaticMarkup(React.createElement(Materials,{form:{data:d},invokeOn(){throw Error('Rendering must not request or save');}}));

test('real controls render unsaved reference, exact decimals, multiple product lines and rule inputs',()=>{
 const d=data(),before=JSON.stringify(d),html=render(d);
 for(const text of ['合成产品甲','合成产品乙','当前输入试算（尚未保存）','2026年 Q4','0.00 元','预计服务开始日期','预计 OA 日期','预计账号数','后端计算参考'])assert.ok(html.includes(text),text);
 assert.match(html,/8000000000000000\.01/);assert.match(html,/历史 &lt;产品线&gt;/);
 assert.doesNotMatch(html,/保存物料修改后再查看|填写产品线|预计交付日期/);
 assert.equal(JSON.stringify(d),before);
});

test('pending and failed references never show old totals or a fabricated zero',()=>{
 const pending=data();pending.materialPreview.pending=true;const waiting=render(pending);
 assert.match(waiting,/输入已更新/);assert.doesNotMatch(waiting,/含税参考：0\.00/);
 const failed=data();failed.materialPreview.error='目录已更新，请重新选择物料';const html=render(failed);
 assert.match(html,/目录已更新，请重新选择物料/);assert.match(html,/重试计算/);assert.doesNotMatch(html,/含税参考：0\.00/);
 assert.match(html,/8000000000000000\.01/);
});

test('saved upstream ID without a detail name remains visibly linked without changing the draft',()=>{
 const d=data();Object.assign(d.form,{trade_relation:'downstream',upstream_opportunity_id:'parent-1',upstream_opportunity_name:''});
 const before=JSON.stringify(d),html=render(d);
 assert.match(html,/已关联上游商机/);assert.match(html,/重新选择/);assert.doesNotMatch(html,/选择上游商机/);
 assert.equal(JSON.stringify(d),before);
});

test('upstream label resolves only the saved ID from readable options and escapes its name',()=>{
 const d=data();Object.assign(d.form,{trade_relation:'downstream',upstream_opportunity_id:'parent-1'});
 d.upstreamOptions=[{id:'parent-2',name:'错误父级'},{id:'parent-1',name:'总代 <甲>'}];
 const before=JSON.stringify(d),html=render(d);
 assert.match(html,/总代 &lt;甲&gt;/);assert.doesNotMatch(html,/错误父级|已关联上游商机/);
 assert.equal(JSON.stringify(d),before);
 d.upstreamOptions=[];assert.match(render(d),/已关联上游商机/);
});

test('an unlinked draft does not show a stale upstream name as a saved relationship',()=>{
 const d=data();Object.assign(d.form,{trade_relation:'downstream',upstream_opportunity_id:'',upstream_opportunity_name:'旧名称'});
 const html=render(d);assert.match(html,/选择上游商机/);assert.doesNotMatch(html,/旧名称|已关联上游商机|重新选择/);
});
