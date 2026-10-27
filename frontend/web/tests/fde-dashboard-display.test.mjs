import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import React from 'react';
import {transformSync} from 'esbuild';
const require=createRequire(import.meta.url);
const finance=require('../../miniprogram/utils/financialDisplay.js');
const module={exports:{}};
vm.runInNewContext(transformSync(fs.readFileSync(new URL('../department-ui/FdePages.jsx',import.meta.url),'utf8'),{loader:'jsx',format:'cjs'}).code,{
  module,exports:module.exports,require:name=>name==='react'?React:new Proxy({},{get:(_,key)=>key==='__esModule'?true:String(key)}),
});
const nodes=tree=>!tree||typeof tree!=='object'?[]:[tree,...React.Children.toArray(tree.props?.children).flatMap(nodes)];
function render(summary={}) {
 const calls=[];
 const d={ready:true,scope:'self',canViewTeam:true,memberIndex:0,memberOptions:[{id:'',name:'本人'},{id:'peer',name:'同事'}],summary};
 const tree=module.exports.FdeDashboard({dash:{data:d,properties:{}},invokeOn:(_,name,payload)=>calls.push({name,payload})});
 return {tree,calls,items:nodes(tree).filter(row=>row.type==='SbMetricStrip').flatMap(row=>row.props.items)};
}
test('FDE Web确收保留零/小额/部分采用金额、单次单位及质量提示',()=>{
 for(const [amount,subtotal,expected] of [['0',null,'0元'],['0.49',null,'0.49元'],['10000',null,'1万'],[null,'10000','1万'],[null,null,'未登记']]) {
   const recognizedFinancial=finance.aggregate({recognized_amount:amount,recognized_confirmed_subtotal:subtotal,recognized_complete:amount!==null},'recognized');
   const {items}=render({recognized_amount:amount,recognizedText:recognizedFinancial.amountText,recognizedFinancial,financialHints:recognizedFinancial.qualityHints});
   const card=items.find(item=>item.key==='recognized');
   assert.equal(card.value??card.missingText,expected);assert.equal(card.label,'实际确收（不含税）');
   if(subtotal)assert.match(card.note,/已登记小计/);
   assert.doesNotMatch(card.value||'',/万 万|元 万/);
 }
});
test('FDE Web成员下拉使用已加载成员入口，不依赖小程序弹层状态',()=>{
 const {tree,calls}=render();const dropdown=nodes(tree).find(row=>row.type==='SbLabeledSelect'&&row.props.label==='成员');
 assert.equal(dropdown.props.value,'');dropdown.props.onChange('peer');
 assert.equal(calls[0].name,'selectDashboardMember');assert.equal(calls[0].payload.detail.id,'peer');
});
