import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';
import {paragraphPages,textPages,presentationDirection} from '../department-ui/work-report/presentation-pages.js';

test('long Chinese text, exact amounts and unicode survive paging without truncation or invented prose',()=>{
  const text='商机 A：100万元→70万元，-30万元。\n'+('继续核对👩🏽‍💻数据\n'.repeat(250))+'结尾保留全部。';
  const parts=textPages(text);
  assert.ok(parts.length>1);assert.equal(parts.join(''),text);
  for(const part of parts)assert.ok(Array.from(part).length<=1000);
  const items=[{text,kind:'plan',source_refs:['synthetic']},{text:'第二项内容',kind:'manual'}];
  const before=JSON.stringify(items),pages=paragraphPages(items);
  assert.equal(pages.flat().map(row=>row.text).join(''),text+'第二项内容');
  assert.ok(pages.flat().some(row=>row.continued));assert.equal(pages.at(-1).at(-1).kind,'manual');
  assert.equal(JSON.stringify(items),before);assert.deepEqual(paragraphPages([]),[[]]);
});

test('presentation keys respect editable fields, controls, IME, native details and other handled shortcuts',()=>{
  for(const key of ['ArrowRight','PageDown',' '])assert.equal(presentationDirection({key}),1);
  for(const key of ['ArrowLeft','PageUp'])assert.equal(presentationDirection({key}),-1);
  for(const flag of ['defaultPrevented','isComposing','altKey','ctrlKey','metaKey'])assert.equal(presentationDirection({key:'ArrowRight',[flag]:true}),0);
  for(const control of ['input','textarea','select','button','a','summary','[contenteditable]','[role="combobox"]'])
    assert.equal(presentationDirection({key:' ',target:{closest:selector=>selector.split(',').includes(control)?{}:null}}),0,control);
  assert.equal(presentationDirection({key:'ArrowRight',target:{closest:selector=>selector.split(',').includes('button')?{}:null}}),1);
  assert.equal(presentationDirection({key:'Escape'}),0); // Modal owns escape and focus restoration.
});

test('consecutive pages retain the focus host and transfer only focus inside an outgoing slide',()=>{
  let index=0,focusCalls=0;
  const inside={},selector={},document={activeElement:null};
  const body={ownerDocument:document,closest:()=>null,contains:element=>element===body || element===inside,
    focus(){focusCalls++;document.activeElement=body;}};
  const ref={current:body},module={exports:{}};
  vm.runInNewContext(transformSync(fs.readFileSync(new URL('../department-ui/work-report/ReportPresentation.jsx',import.meta.url),'utf8'),{loader:'jsx',format:'cjs'}).code,
    {module,exports:module.exports,require:name=>name==='react'?{...React,useState:()=>[index,next=>{index=next;}],useRef:()=>ref,useEffect(){}}:
      name==='antd'?{Button:'button',Modal:'modal',Select:'select'}:name.endsWith('presentation-pages.js')?{presentationDirection}:{}});
  const slides=[0,1,2].map(i=>({key:`slide-${i}`,title:`第 ${i+1} 页`,content:React.createElement('p',null,`内容 ${i}`)}));
  const render=()=>{const modal=module.exports.default({open:true,slides});const wrapper=modal.props.children;return {modal,wrapper,main:wrapper.props.children[1],footer:wrapper.props.children[2]};};
  const arrow=(view,key)=>view.wrapper.props.onKeyDown({key,target:body,preventDefault(){}});
  let view=render();const host={type:view.main.type,key:view.main.key,ref:view.main.ref};
  view.modal.props.afterOpenChange(true);assert.equal(document.activeElement,body);
  for(let page=1;page<=2;page++){
    arrow(view,'ArrowRight');view=render();
    assert.equal(index,page);assert.deepEqual({type:view.main.type,key:view.main.key,ref:view.main.ref},host);
    assert.equal(view.main.props.children.key,`slide-${page}`);assert.equal(document.activeElement,body);
  }
  assert.equal(focusCalls,1);
  document.activeElement=inside;arrow(view,'ArrowLeft');view=render();assert.equal(index,1);assert.equal(document.activeElement,body);assert.equal(focusCalls,2);
  document.activeElement=selector;view.footer.props.children[0].props.onChange(0);view=render();
  assert.equal(index,0);assert.equal(document.activeElement,selector);assert.equal(focusCalls,2);
});
