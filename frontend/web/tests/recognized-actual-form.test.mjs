import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';
const source=transformSync(fs.readFileSync(new URL('../department-ui/OpportunityRecognizedActual.jsx',import.meta.url),'utf8'),{loader:'jsx',format:'cjs'}).code;
const module={exports:{}},Input=Object.assign(()=>null,{TextArea:()=>null});
vm.runInNewContext(source,{module,exports:module.exports,require:name=>name==='react'?React:name==='./ImeInput.jsx'?{Input}:new Proxy({},{get:(_,key)=>key})});
const Component=module.exports.default;
const all=(node,predicate)=>!React.isValidElement(node)?[]:[...(predicate(node)?[node]:[]),...React.Children.toArray(node.props.children).flatMap(child=>all(child,predicate))];
const text=node=>typeof node==='string'?node:React.isValidElement(node)?React.Children.toArray(node.props.children).map(text).join(''):'';
function render(overrides={}){
 const calls=[],form={properties:{existing:{id:'opportunity'}},data:{recognizedActualVisible:true,recognizedActualLoaded:true,recognizedActualEditable:true,
 recognizedActualQuarterOptions:[{label:'2026 Q4'}],recognizedActualCurrentText:'10 万元 · 已登记',recognizedActualTotalText:'15 万元 · 已填合计',recognizedActualRows:[
 {material_line_id:'one',material_name:'服务物料',material_code:'SKU',eligible:true,is_active:true,amount:'15.000001',currentText:'10 万元',evidence:'凭证',note:'',dirty:true}],...overrides}};
 return {form,calls,tree:Component({form,invokeOn:(...args)=>calls.push(args)})};
}
test('Web物料录入用精确字符串、只读合计和原商机保存，没有审批按钮',()=>{
 const view=render(),input=all(view.tree,node=>node.props['aria-label']==='服务物料 本季度累计不含税确收（万元）')[0];
 assert.equal(input.props.value,'15.000001');input.props.onChange({target:{value:'15.000002'}});
 assert.equal(view.calls[0][1],'recognizedActualInput');assert.equal(view.calls[0][2].detail.value,'15.000002');
 assert.match(text(view.tree),/保存即生效/);assert.match(text(view.tree),/物料填写合计15 万元/);assert.doesNotMatch(text(view.tree),/提交审批|运营确认/);
 assert.equal(all(view.tree,node=>node.props.type==='submit').length,0);
});
test('Web拒绝未授权和不适用物料输入，加载锁定且跟进内嵌不展示',()=>{
 for(const flags of [{recognizedActualEditable:false},{recognizedActualRows:[{material_line_id:'one',material_name:'服务物料',eligible:false}]}]){
  assert.equal(all(render(flags).tree,node=>node.type===Input).length,0);
 }
 const loading=render({recognizedActualLoading:true});const input=all(loading.tree,node=>node.type===Input)[0];
 assert.equal(input.props.disabled,true);input.props.onChange({target:{value:'9'}});assert.equal(loading.calls.length,0);
 const view=render();view.form.properties.visitContext=true;assert.equal(Component({form:view.form}),null);
});
