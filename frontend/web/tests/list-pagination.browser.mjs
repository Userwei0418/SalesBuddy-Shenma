/** Isolated browser fixtures: no backend, existing browser, or business writes. */
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
let browser,html;
before(async()=>{
 const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
 import React,{useRef,useState}from'react';import{createRoot}from'react-dom/client';import{App}from'antd';
 import{PcProvider}from'./department-ui/pc/PcProvider.jsx';import Tasks from './department-ui/Tasks.jsx';import CustomerHistory from './department-ui/CustomerHistory.jsx';import Customers from './department-ui/Customers.jsx';
 import './runtime.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';import './department-ui/pc/workspace-layout.css';
 window.account={workspaceId:'fixture-workspace',userId:'fixture-user'};window.loads=[];window.requests=[];window.failNext=false;
 const all=Array.from({length:75},(_,i)=>({id:'task-'+i,webTitle:'合成任务'+String(i+1).padStart(2,'0'),customer:'合成客户',owner:'测试成员',statusLabel:'进行中',webTime:{label:'本周到期',detail:'2026-10-16'},priority:'普通'}));
 const history=Array.from({length:75},(_,i)=>({id:'request-'+i,kind:'creation',customer_name:'合成申请'+(i+1),status:'pending',can_read_customer:false,applicant_name:'测试成员'}));
 const customers=history.map((row,i)=>({...row,name:'合成客户'+(i+1),owner_name:'测试成员',can_open:true,activity_readable:true,opportunity_readable:true,map_readable:true,is_active:true,max_open_probability:30}));
 window.fixtureApi={getBusinessOptions:async()=>({}),request:async({path})=>{requests.push(path);const q=new URL(path,'https://lists.test').searchParams,offset=Number(q.get('offset')),limit=Number(q.get('limit'));if(path.startsWith('/customers/portfolio'))return{items:customers.slice(offset,offset+limit),total:75,offset,limit,map_points:[],unscored_count:75,map_unavailable_count:0,availability:{activity:true,opportunity:true,map:true,pool:true},summary:{claimed:75,active:75,pipeline_ge_10:75,pipeline_ge_30:75,pipeline_ge_50:0,pipeline_ge_70:0,pipeline_ge_90:0,won:0,pool_total:75,activity_unavailable_count:0,opportunity_unavailable_count:0}};return{items:history.slice(offset,offset+limit),total:75,summary:{pending:75,approved:0,rejected:0,cancelled:0}};}};
 function TaskFixture(){const[data,setData]=useState({activeTab:'pending',taskPeriod:'all',sortIndex:0,taskView:'self',webTaskRows:all.slice(0,20),filteredTotal:75,hasMore:true,nextOffset:20,loading:false,loadingMore:false,moreError:'',webTaskKpis:{pending:75,completed:0,overdue:0,teamMembers:1},webTaskTabs:[{key:'pending',label:'待处理',count:75}],sortOptions:[{label:'最早到期',key:'due_asc'}]});
 const ref=useRef({});const page=ref.current;
 const invoke=name=>{if(name!=='loadMore')return;loads.push(data.nextOffset);setData(p=>({...p,loadingMore:true,moreError:''}));setTimeout(()=>{if(window.failNext){window.failNext=false;setData(p=>({...p,loadingMore:false,moreError:'合成分页失败'}));return;}setData(p=>{const count=Math.min(75,p.nextOffset+20);return{...p,webTaskRows:all.slice(0,count),nextOffset:count,hasMore:count<75,loadingMore:false};});},10);};
 return <Tasks page={page} data={data} invoke={invoke} select={()=>null}/>;}
 const root=createRoot(document.getElementById('root'));let serial=0;window.show=mode=>root.render(<PcProvider><App><main className="department-ui">{mode==='history'?<CustomerHistory key={++serial}/>:mode==='customers'?<Customers key={++serial}/>:<TaskFixture key={++serial}/>}</main></App></PcProvider>);show('tasks');
 `},plugins:[{name:'fixture-data',setup(b){b.onResolve({filter:/\/data\.js$/},args=>args.importer.includes('/department-ui/')?({path:args.path,namespace:'fixture'}):null);b.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:`export const identity=()=>account.workspaceId+':'+account.userId;export const session=()=>account;export const loadDirectory=async()=>null;export const api=()=>fixtureApi;export const can=()=>true;export const open={route(){},customer(){}};export const amountWan=v=>Number(v)/10000;export const dayKey=v=>v?.slice(0,10)||'';export const todayKey=()=> '2026-10-12';export const periodRange=()=>({start:'2026-10-12',end:'2026-10-18',today:'2026-10-12'});export const scopeLabel=()=> '本人';export const readScopeFor=()=>({scopes:['self']});`}));}}],bundle:true,write:false,outdir:'.runtime/list-pagination-fixture',define:{'process.env.NODE_ENV':'"production"'}});
 const js=built.outputFiles.find(f=>f.path.endsWith('.js')).text,css=built.outputFiles.find(f=>f.path.endsWith('.css')).text;
 html=`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>${css}\n#page-root{height:100dvh}body{margin:0}#mobile-nav,#mobile-account-button{display:none}@media(max-width:900px){#mobile-nav{display:block;position:fixed;bottom:0;left:0;right:0;height:66px;z-index:80;background:white}#mobile-account-button{display:block;position:fixed;bottom:82px;right:76px;width:62px;height:36px;z-index:91;background:white}}</style></head><body class="department-design"><div id="page-root" class="department-host"><div id="root"></div></div><nav id="mobile-nav">底部导航</nav><button id="mobile-account-button">账号</button><script>${js}</script></body></html>`;
 browser=await chromium.launch({channel:'chrome',headless:true});
});
after(async()=>browser?.close());
const chooseSize=async(page,size)=>{await page.locator('.ant-pagination-options .ant-select').click();await page.locator('.ant-select-dropdown:visible').getByText(size+' / page',{exact:true}).or(page.locator('.ant-select-dropdown:visible').getByText(size+' 条/页',{exact:true})).click();};
for(const width of [1440,1024,600])test(`lists use whole content scrolling and selected page sizes at ${width}px`,async()=>{
 const context=await browser.newContext({viewport:{width,height:800}}),p=await context.newPage(),errors=[];
 p.on('pageerror',e=>errors.push(e.message));p.setDefaultTimeout(7000);
 await context.route('**/*',route=>route.fulfill({contentType:'text/html',body:html}));
 try{
  await p.goto('https://lists.test');await p.getByRole('button',{name:'合成任务20',exact:true}).waitFor();
  assert.equal(await p.locator('.ant-table-row').count(),20);
  assert.match(await p.locator('.ant-pagination-total-text').innerText(),/75/);
  const scroll=await p.evaluate(()=>{const shell=document.getElementById('page-root'),list=document.querySelector('.pc-results-scroll');return{shell: shell.scrollHeight-shell.clientHeight,list:list.scrollHeight-list.clientHeight,overflow:getComputedStyle(list).overflowY};});
  assert.ok(scroll.shell>700,JSON.stringify(scroll));assert.equal(scroll.list,0);assert.equal(scroll.overflow,'visible');
  assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await chooseSize(p,50);await p.getByRole('button',{name:'合成任务50',exact:true}).waitFor();
  assert.equal(await p.locator('.ant-table-row').count(),50);assert.deepEqual(await p.evaluate(()=>loads),[20,40]);
  await p.locator('.ant-pagination-item-2').click();await p.getByRole('button',{name:'合成任务75',exact:true}).waitFor();assert.equal(await p.locator('.ant-table-row').count(),25);
  await chooseSize(p,10);await p.getByRole('button',{name:'合成任务01',exact:true}).waitFor();assert.equal(await p.locator('.ant-table-row').count(),10);assert.equal(await p.locator('.ant-pagination-item-active').innerText(),'1');
  await p.evaluate(()=>show('tasks'));await p.getByRole('button',{name:'合成任务10',exact:true}).waitFor();assert.equal(await p.locator('.ant-table-row').count(),10);
  await p.evaluate(()=>{account={...account,userId:'second-user'};show('tasks');});await p.getByRole('button',{name:'合成任务20',exact:true}).waitFor();assert.equal(await p.locator('.ant-table-row').count(),20);
  await p.evaluate(()=>window.failNext=true);await chooseSize(p,100);await p.getByText('合成分页失败',{exact:true}).waitFor();const failedLoads=await p.evaluate(()=>loads.length);await p.waitForTimeout(60);assert.equal(await p.evaluate(()=>loads.length),failedLoads);await p.getByRole('button',{name:'重试加载本页',exact:true}).click();await p.getByRole('button',{name:'合成任务75',exact:true}).waitFor();assert.equal(await p.locator('.ant-table-row').count(),75);assert.match(await p.locator('.ant-pagination-total-text').innerText(),/75/);
  await p.evaluate(()=>show('history'));await p.getByText('合成申请20',{exact:true}).waitFor();await chooseSize(p,100);await p.getByText('合成申请75',{exact:true}).waitFor();
  assert.ok(await p.evaluate(()=>requests.at(-1).includes('limit=100')&&requests.at(-1).includes('offset=0')));assert.equal(await p.locator('.ant-table-row').count(),75);
  if(process.env.LIST_SHOTS){await fs.mkdir(process.env.LIST_SHOTS,{recursive:true});await p.screenshot({path:process.env.LIST_SHOTS+'/history-'+width+'.png',fullPage:true});}
  if(width!==1024){await p.evaluate(()=>show('customers'));await p.getByRole('button',{name:'合成客户20',exact:true}).waitFor();assert.equal(await p.locator('.ant-table-row').count(),20);await chooseSize(p,50);await p.getByRole('button',{name:'合成客户50',exact:true}).waitFor();assert.equal(await p.locator('.ant-table-row').count(),50);assert.ok(await p.evaluate(()=>requests.at(-1).includes('limit=50')));const metrics=await p.evaluate(()=>{const node=document.querySelector('.ds-customer-table-viewport'),shell=document.getElementById('page-root');return{overflow:getComputedStyle(node).overflowY,inner:node.scrollHeight-node.clientHeight,outer:shell.scrollHeight-shell.clientHeight};});assert.equal(metrics.inner,0);assert.equal(metrics.overflow,'visible');assert.ok(metrics.outer>1000,JSON.stringify(metrics));await p.locator('.ant-pagination-item-2').click();await p.getByRole('button',{name:'合成客户75',exact:true}).waitFor();assert.equal(await p.locator('.ant-table-row').count(),25);assert.ok(await p.evaluate(()=>requests.at(-1).includes('offset=50')));assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));}
  assert.deepEqual(errors,[]);
 }finally{await context.close();}
});
