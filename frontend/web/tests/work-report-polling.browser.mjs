// Real React effects with deferred synthetic reads; no business API or browser account.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from '../node_modules/esbuild/lib/main.js';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
test('report status reads stop on errors, identity changes and unmount without overlapping',async()=>{
 const root=fileURLToPath(new URL('../',import.meta.url));
 const bundle=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
 import React,{useState}from'react';import{createRoot}from'react-dom/client';import{usePollingResource}from'./department-ui/work-report/usePollingResource.js';
 window.pollOwner='a';window.reads=[];window.SalesRuntime={app:{globalData:{session:{}}},require:()=>({identity:()=>window.pollOwner})};
 function Fixture(){const[options,S]=useState({id:'one',owner:'a'});window.setPoll=S;const resource=usePollingResource({...options,delay:20,load:id=>new Promise((resolve,reject)=>window.reads.push({id,resolve,reject})),pending:value=>value.status==='running'});window.resource=resource;return <div>{resource.value?.text||resource.error?.message||'loading'}</div>}
 window.root=createRoot(document.getElementById('root'));root.render(<Fixture/>);
 `},bundle:true,write:false,define:{'process.env.NODE_ENV':'"production"'}});
 const browser=await chromium.launch({channel:'chrome',headless:true});const context=await browser.newContext();const p=await context.newPage();const errors=[];p.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><div id="root"></div><script>${bundle.outputFiles[0].text}</script>`}));
 try{
  await p.goto('https://report-poll.test/');await p.waitForFunction(()=>reads.length===1);await p.waitForTimeout(70);assert.equal(await p.evaluate(()=>reads.length),1);
  await p.evaluate(()=>reads[0].resolve({status:'running',text:'处理中'}));await p.waitForFunction(()=>reads.length===2);
  await p.evaluate(()=>reads[1].reject(Error('权限已变化')));await p.waitForFunction(()=>resource.error?.message==='权限已变化');assert.equal(await p.evaluate(()=>resource.value),null);await p.waitForTimeout(70);assert.equal(await p.evaluate(()=>reads.length),2);
  await p.evaluate(()=>setPoll({id:'one',owner:'a',refresh:1}));await p.waitForFunction(()=>reads.length===3);
  await p.evaluate(()=>{window.pollOwner='b';setPoll({id:'two',owner:'b'})});await p.waitForFunction(()=>reads.length===4);assert.equal(await p.evaluate(()=>resource.value),null);
  await p.evaluate(()=>reads[2].resolve({status:'succeeded',text:'旧账号正文'}));await p.waitForTimeout(50);assert.equal(await p.evaluate(()=>resource.value),null);assert.equal(await p.getByText('旧账号正文').count(),0);
  await p.evaluate(()=>reads[3].resolve({status:'succeeded',text:'当前账号'}));await p.waitForFunction(()=>resource.value?.text==='当前账号');await p.waitForTimeout(70);assert.equal(await p.evaluate(()=>reads.length),4);
  await p.evaluate(()=>setPoll({id:'two',owner:'b',refresh:1}));await p.waitForFunction(()=>reads.length===5);
  await p.evaluate(()=>{root.unmount();reads[4].resolve({status:'running',text:'已离开的正文'})});await p.waitForTimeout(70);assert.equal(await p.evaluate(()=>reads.length),5);assert.deepEqual(errors,[]);
 }finally{await context.close();await browser.close();}
});
