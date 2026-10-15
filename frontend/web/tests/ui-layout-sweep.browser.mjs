/** Layout regressions in actual PC components, synthetic data and no service requests. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from '../node_modules/esbuild/lib/main.js';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';import {App} from 'antd';
import {PcProvider} from './department-ui/pc/PcProvider.jsx';import CustomerCreate from './department-ui/CustomerCreate.jsx';import OpportunityWorkspace from './department-ui/OpportunityWorkspace.jsx';
import Knowledge from './department-ui/Knowledge.jsx';
import './runtime.css';import './desktop.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';import './department-ui/pc/workspace-layout.css';import './department-ui/pc/overview-filters.css';
window.SALES_MODE='live';window.SalesRuntime={app:{globalData:{session:{workspaceId:'layout',userId:'owner',permissionVersion:'v1'}},can:()=>true},require:()=>({identity:()=> 'layout-owner'}),businessOptions:()=>({customer:{}})};
window.fixtureCalls=[];
const fields=[['customer_name','客户名称'],['industry','所属行业'],['customer_type','客户类型'],['level_code','客户优先级'],['lead_source','客户来源'],['target_team','客户归属团队'],['partner_name','合作伙伴'],['contact_name','首要联系人'],['contact_title','联系人职位'],['contact_role','联系人角色']].map(([key,label])=>({key,label,value:'',required:true}));
const createData={fields,canRequestCreate:true,completedRequiredCount:2,requiredCount:8,missingCount:6,progressPercent:25,creationRequests:['pending','rejected','approved'].map((status,i)=>({id:String(i),status,requested_at:'2026-10-08T15:07:24Z',payload:{name:'合成布局验证客户-'+status+'-集团跨区域业务事业部'},decision_reason:i?'这是用于排版验收的审批说明，较长时仍需保证审批状态和查询认领操作可见。':''}))};
const config={schema_version:2,columns:[{key:'name',width:240,pinned:'left'},{key:'customer_name',width:180},{key:'amount',width:180}],q:'',filters:[],sorts:[],group_by:null,scope:{kind:'authorized'},period:{kind:'current_quarter'},density:'comfortable',wrap:false};
window.oppFixture={options:{definition_version:'opportunity_workspace_v2',fields:[['name','商机名称','text'],['customer_name','客户名称','text'],['amount','ACV','money']].map(([key,label,type])=>({key,label,type,width:180,nullable:true,editable:false,read_only:true,edit_mode:'none',filter_ops:['eq'],sortable:true,groupable:false})),default_config:config,publish_scopes:['private'],teams:[],scope_options:{kinds:[{value:'authorized',label:'全部可查看'}],teams:[],owners:[]}},views:{items:[{id:'long',name:'合成方案名称-大客户年度业务进展与跨团队商机经营视图',visibility:'private',can_edit:true,version_no:1,config}]},preference:{effective_view_id:'long',default_view_id:'long',version_no:1},query:{items:[{id:'a',values:{name:'合成长名称商机-客户需求与预算推进',customer_name:'合成客户',amount:'12345678'},capabilities:{editable_fields:[]}}],total:1,has_more:false,next_offset:null,summary:{count:1,amount:'12345678',amount_unknown_count:0},groups:[]}};
window.catalogFixture={version:'fixture',updated_at:'2026-10-09',categories:[{key:'solution',label:'解决方案'}],items:[1,2,3].map(i=>({id:String(i),title:i===1?'VeryLongUnbrokenProductName'.repeat(5):'合成方案'+i,summary:i===1?'用于复核长标题和不同长度卡片的布局。'.repeat(8):'简短介绍',category:'solution',status:'current',version:'v1.0',product_line:'合成产品线',updated_at:'2026-10-09'}))};
const appRoot=createRoot(document.getElementById('root'));window.show=(mode)=>{const page={route:mode==='customer'?'pages/customer-create/index':mode==='opportunity'?'pages/workbench/index':'pages/knowledge/index',options:{tab:'catalog'},data:createData,directoryTeams:[],setData:()=>{},refresh:()=>{}};SalesRuntime.current=page;document.getElementById('root').dataset.departmentPage=page.route;appRoot.render(<PcProvider><App>{mode==='customer'?<CustomerCreate key={mode} page={page} data={createData} invoke={(...args)=>fixtureCalls.push(args)}/>:mode==='opportunity'?<OpportunityWorkspace key={mode} page={page} data={{options:{}}}/>:<Knowledge key={mode} page={page}/>}</App></PcProvider>);};show('customer');
`},plugins:[{name:'isolated-data',setup(b){
 b.onResolve({filter:/^\.\/pc\/(opportunity-workspace-api|knowledge-data)\.js$/},args=>({path:args.path,namespace:'fixture'}));
 b.onLoad({filter:/.*/,namespace:'fixture'},args=>({loader:'js',resolveDir:root,contents:args.path.includes('opportunity-workspace')?`export const opportunityWorkspaceApi={options:async()=>oppFixture.options,views:async()=>oppFixture.views,preference:async()=>oppFixture.preference,query:async()=>oppFixture.query};`:`export * from '${root}department-ui/pc/knowledge-data.js';export const loadCatalog=async()=>catalogFixture;`}));
}}],bundle:true,write:false,loader:{'.svg':'dataurl'},outdir:'.runtime/ui-sweep-test',define:{'process.env.NODE_ENV':'"production"'}});
const js=built.outputFiles.find(f=>f.path.endsWith('.js')).text,css=built.outputFiles.find(f=>f.path.endsWith('.css')).text;
const shots=root+'.runtime/ui-sweep-shots';await fs.mkdir(shots,{recursive:true});
for(const width of [1920,1440,1024,901,768,390])test(`UI sweep at ${width}px`,async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext({viewport:{width,height:900}}),p=await context.newPage(),errors=[],unexpected=[];p.setDefaultTimeout(7000);p.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',r=>{const u=new URL(r.request().url());if(u.pathname==='/')return r.fulfill({contentType:'text/html',body:`<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body class="department-design"><aside class="web-sidebar"></aside><main class="web-main"><div class="web-topbar"></div><div id="page-root" class="department-host"><div id="root" class="department-ui"></div></div></main><script>${js}</script></body></html>`});unexpected.push(u.pathname);return r.abort();});
 const checkBounds=async(selector,boundary)=>p.locator(selector).evaluateAll((els,b)=>els.map(e=>{const x=e.getBoundingClientRect(),limit=e.closest(b).getBoundingClientRect();return{x:x.x,right:x.right,leftLimit:limit.left,rightLimit:limit.right,width:x.width,text:e.textContent};}),boundary);
 try{
  await p.goto('https://ui-sweep.test/');await p.getByText('待运营审批',{exact:false}).waitFor();
  const customer=await checkBounds('.ds-cc-request-result > *','.ds-cc-requests');
  await p.screenshot({path:shots+'/fixture-customer-'+width+'.png',fullPage:true});
  assert.ok(customer.every(r=>r.x>=r.leftLimit&&r.right<=r.rightLimit+1),'approval statuses and actions stay inside the request panel: '+JSON.stringify(customer));
  assert.ok(await p.locator('.ds-cc-request-row').evaluateAll(es=>es.every(e=>e.getBoundingClientRect().height<180)),'request rows remain compact when flex direction changes');
  const inputs=await p.locator('.ds-cc-field input').evaluateAll(es=>es.filter(e=>!e.classList.contains('ant-select-input')).map(e=>({id:e.id,width:e.getBoundingClientRect().width})));
  assert.ok(inputs.every(x=>x.width>=200),'input fields must remain usable: '+JSON.stringify(inputs));
  await p.getByRole('button',{name:'查询认领状态',exact:true}).scrollIntoViewIfNeeded();assert.equal(await p.getByRole('button',{name:'查询认领状态',exact:true}).isVisible(),true);
  const colors=await p.locator('.ds-cc-request-reason').evaluateAll(es=>es.map(e=>getComputedStyle(e).color));assert.notEqual(colors[0],colors[1],'approved note must not use rejection styling');
  await p.evaluate(()=>show('opportunity'));await p.getByRole('combobox',{name:'选择看板方案'}).waitFor();await p.getByText(/已加载 1 \/ 1 条/).waitFor();
  const scheme=await checkBounds('.opp-scheme-bar .ant-select,.opp-scheme-bar button','.opp-scheme-bar');
  await p.screenshot({path:shots+'/fixture-opportunity-'+width+'.png',fullPage:true});
  assert.ok(scheme.every(r=>r.x>=r.leftLimit&&r.right<=r.rightLimit+1),'scheme controls stay inside the panel');
  if(width>600)assert.ok(await p.locator('.pc-filterbar-row').evaluate(e=>[...e.children].every(c=>Math.abs(c.getBoundingClientRect().top-e.children[0].getBoundingClientRect().top)<2)),'filters remain a single row');
  await p.evaluate(()=>show('knowledge'));await p.locator('.pc-kb-card').first().waitFor();
  const knowledge=await p.locator('.pc-kb-card').evaluateAll(es=>es.map(e=>({width:e.clientWidth,scroll:e.scrollWidth,height:e.getBoundingClientRect().height})));
  await p.screenshot({path:shots+'/fixture-knowledge-'+width+'.png',fullPage:true});
  assert.ok(knowledge.every(r=>r.scroll<=r.width+1),'long card text must wrap without overflow: '+JSON.stringify(knowledge));
  assert.ok(knowledge[0].height>knowledge[1].height+30,'short cards retain their own height beside long cards');
  assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);assert.deepEqual(await p.evaluate(()=>fixtureCalls),[]);
  await fs.writeFile(shots+'/fixture-'+width+'.json',JSON.stringify({customer,inputs,scheme,knowledge},null,2));
 }finally{await context.close();await browser.close();}
});
