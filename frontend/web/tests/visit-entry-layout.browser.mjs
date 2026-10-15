/** 真实录入组件的长列表回归；合成客户，所有请求封锁，无业务写入。 */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from '../node_modules/esbuild/lib/main.js';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
// 与运行时一样加载共享基础样式；其中 button overflow:hidden 会改变 flex 的最小尺寸。
const sharedCss=execFileSync('python3',['-c',"import sys;sys.path.insert(0,'scripts');from build import css,SOURCE;print(css(SOURCE/'app.wxss'));print(css(SOURCE/'pages/visit-entry/index.wxss'))"],{cwd:root,encoding:'utf8'});
const built = await build({absWorkingDir: root, stdin: {resolveDir: root, loader: 'jsx', contents: `
import React from 'react';import {createRoot} from 'react-dom/client';
import {PcProvider} from './department-ui/pc/PcProvider.jsx';import VisitEntry from './department-ui/VisitEntry.jsx';
import './runtime.css';import './desktop.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';
window.SALES_MODE='live';window.SalesRuntime={app:{can:()=>true},require:()=>({visitImportAction:()=>({permission:null})})};
window.fixtureCalls=[];const customers=Array.from({length:30},(_,i)=>({id:String(i),name:i===0?'合成长名称客户集团跨区域解决方案事业部有限公司':'合成客户'+(i+1),team_name:'南区'}));
let data={customerResults:customers,customerSearchDone:true,canFirstVisit:true,canCreateOpportunity:true,canTranscribe:true,customerConfirmed:false,transcript:'',statusText:'先记录事实，稍后确认客户与商机'};
const appRoot=createRoot(document.getElementById('root'));
function invoke(name,event){fixtureCalls.push([name,event]);if(name==='inputCustomerQuery')data={...data,customerQuery:event.detail.value};if(name==='retryCustomerSearch')data={...data,customerResults:customers.filter(c=>c.name.includes(data.customerQuery||''))};if(name==='chooseCustomer'){const c=customers.find(c=>c.id===event.dataset.id);data={...data,customerConfirmed:true,customerId:c.id,customerName:c.name,customerInitial:c.name[0]};}if(name==='changeCustomer')data={...data,customerConfirmed:false};render();}
function render(){appRoot.render(<PcProvider><VisitEntry page={{route:'pages/visit-entry/index'}} data={data} invoke={invoke} invokeOn={()=>{}} select={()=>null}/></PcProvider>);}render();
`}, bundle:true, write:false, loader:{'.svg':'dataurl'}, outdir:'.runtime/visit-entry-layout-test', define:{'process.env.NODE_ENV':'"production"'}});
const js=built.outputFiles.find(f=>f.path.endsWith('.js')).text,css=sharedCss+'\n'+built.outputFiles.find(f=>f.path.endsWith('.css')).text;
const shots=root+'.runtime/visit-entry-layout-shots';await fs.mkdir(shots,{recursive:true});
for(const [width,height] of [[1920,1080],[1470,779],[1024,768],[901,600],[768,900],[390,844]])test(`visit entry ${width}x${height}`,async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext({viewport:{width,height}}),p=await context.newPage(),errors=[],unexpected=[];p.setDefaultTimeout(6000);p.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',r=>{if(new URL(r.request().url()).pathname==='/')return r.fulfill({contentType:'text/html',body:`<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body class="department-design"><aside class="web-sidebar"></aside><main class="web-main"><div class="web-topbar"></div><div id="page-root" class="department-host"><div class="ant-app"><div id="root" class="department-ui" data-department-page="pages/visit-entry/index"></div></div></div></main><script>${js}</script></body></html>`});unexpected.push(r.request().url());return r.abort();});
 try{
  await p.goto('https://visit-layout.test/');await p.locator('.sb-row').last().waitFor();
  const geometry=await p.evaluate(()=>{const list=document.querySelector('.ds-ve-results');return {clientHeight:list.clientHeight,scrollHeight:list.scrollHeight,rows:[...list.querySelectorAll('.sb-row')].map(e=>{const r=e.getBoundingClientRect(),n=e.querySelector('.sb-row-name').getBoundingClientRect(),s=e.querySelector('.sb-row-summary').getBoundingClientRect();return{height:r.height,nameHeight:n.height,nameTop:n.top-r.top,summaryBottom:s.bottom-r.top};}),overflow:document.documentElement.scrollWidth-innerWidth};});
  await fs.writeFile(shots+'/fixture-'+width+'.json',JSON.stringify(geometry,null,2));await p.screenshot({path:shots+'/fixture-'+width+'.png',fullPage:true});
  const capture=await p.locator('.ds-ve-capture').evaluate(e=>{const r=e.getBoundingClientRect();return [...e.children].map(c=>{const x=c.getBoundingClientRect();return {left:x.left,right:x.right,width:x.width,parentLeft:r.left,parentRight:r.right};});});
  assert.ok(capture.every(r=>r.left>=r.parentLeft&&r.right<=r.parentRight+1&&r.width>=80),'录入方式、说明与动作完整在录音栏内：'+JSON.stringify(capture));
  assert.equal(geometry.rows.length,30);assert.ok(geometry.rows.every(r=>r.height>=54&&r.nameHeight>=18&&r.nameTop>=0&&r.summaryBottom<=r.height),'客户名称和部门必须在完整行内：'+JSON.stringify(geometry));
  assert.ok(geometry.scrollHeight>geometry.clientHeight,'多客户在列表内滚动');assert.ok(geometry.overflow<=1,'页面无横向溢出');
  if(width<=1100){await p.locator('.ds-ve-capture').scrollIntoViewIfNeeded();await p.screenshot({path:shots+'/fixture-capture-'+width+'.png'});assert.ok(await p.getByRole('button',{name:'开始录音',exact:true}).isVisible());}
  await p.locator('.sb-row').last().scrollIntoViewIfNeeded();assert.ok(await p.locator('.ds-ve-results').evaluate(e=>e.scrollTop>0));
  await p.locator('.sb-row').last().click();await p.locator('.ds-ve-selected-main b').getByText('合成客户30',{exact:true}).waitFor();
  await p.getByRole('button',{name:'更换',exact:true}).click();await p.getByPlaceholder('输入客户名称关键词').fill('合成客户2');await p.getByPlaceholder('输入客户名称关键词').press('Enter');await p.waitForFunction(()=>document.querySelectorAll('.ds-ve-results .sb-row').length===11);
  await p.getByPlaceholder('输入客户名称关键词').fill('不存在');await p.getByPlaceholder('输入客户名称关键词').press('Enter');await p.getByText('没有匹配到客户',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);assert.ok(await p.evaluate(()=>fixtureCalls.every(([name])=>['chooseCustomer','changeCustomer','inputCustomerQuery','retryCustomerSearch'].includes(name))));
 }finally{await browser.close();}
});
