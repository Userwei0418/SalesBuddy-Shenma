/** Actual components + shared WXSS; synthetic fixtures, network blocked, no business writes. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from '../node_modules/esbuild/lib/main.js';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
const shared=execFileSync('python3',['-c',"import sys;sys.path.insert(0,'scripts');from build import css,SOURCE;print(css(SOURCE/'app.wxss'))"],{cwd:root,encoding:'utf8'});
const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';import {PcProvider} from './department-ui/pc/PcProvider.jsx';
import OpportunityCreate from './department-ui/OpportunityCreate.jsx';import CustomerDetail from './department-ui/CustomerDetail.jsx';import OpportunityDetail from './department-ui/OpportunityDetail.jsx';import VisitConfirm from './department-ui/VisitConfirm.jsx';
import './runtime.css';import './desktop.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';
window.SALES_MODE='live';window.SalesRuntime={app:{can:()=>true},require:()=>({})};window.fixtureCalls=[];
const name='合成跨区域智能业务解决方案客户集团有限公司';const opp={id:'opp',name:'跨区域智能业务解决方案一期项目',customerName:name,amount:'120万',stageText:'方案沟通－50%',probability:50,expectedDate:'2026-12-17',ownerLabel:'合成销售',teamLabel:'南区'};
const form={data:{form:{name:'',amount:'',partner_mode:'direct',stageIndex:-1},stages:[{code:'30',text:'商机确认－30%'},{code:'50',text:'方案沟通－50%'}]}};
const customer={id:'c',name,initial:'合',team:'南区',owner:'合成销售',annualValue:'120万',opportunity:opp,cooperationYears:'1年',opportunities:[opp],contacts:Array.from({length:30},(_,i)=>({id:String(i),name:'合成联系人'+i,title:'业务负责人',role:'决策者'})),nextAction:'本周讨论采购范围，下一步确认方案。'};
const mode=new URL(location.href).searchParams.get('case')||'create';
const options=Array.from({length:30},(_,i)=>({id:String(i),name:'合成长名称商机'+i+'：跨区域智能业务解决方案多业务部门协同建设项目第二阶段',unverified:i%2===0}));
const tabs=[{key:'overview',label:'经营概览'},{key:'opportunity',label:'商机进展'}];
const cases={create:[OpportunityCreate,'pages/opportunity-create/index',{customerId:'c',customerName:name}],customer:[CustomerDetail,'pages/customer-detail/index',{customer,activeTab:'overview',tabs,detailSummary:{loaded:true}}],opportunity:[OpportunityDetail,'pages/customer-assets/index',{opportunityId:'opp',opportunity:opp,opportunityTab:'overview',opportunityTabs:[{key:'overview',label:'商机概览'}],detailSummary:{loaded:true},forecastRows:[]}],picker:[VisitConfirm,'pages/visit-confirm/index',{customerName:name,customerConfirmed:true,visitDate:'2026-10-09',values:{},opportunityPickerOpen:true,opportunityOptions:options,opportunityItems:options}],done:[VisitConfirm,'pages/visit-confirm/index',{archived:true,customerName:name,archivedCount:8,values:{next_action:'下周确认业务范围'},advice:{suggestions:[]}}]};
const [Component,route,data]=cases[mode];document.getElementById('root').dataset.departmentPage=route;
createRoot(document.getElementById('root')).render(<PcProvider><Component page={{route}} data={data} invoke={(...a)=>fixtureCalls.push(a)} invokeOn={(...a)=>fixtureCalls.push(a)} select={s=>s==='#opportunityForm'?form:null}/></PcProvider>);
`},bundle:true,write:false,loader:{'.svg':'dataurl'},outdir:'.runtime/detail-layout-test',define:{'process.env.NODE_ENV':'"production"'}});
const js=built.outputFiles.find(f=>f.path.endsWith('.js')).text,css=shared+'\n'+built.outputFiles.find(f=>f.path.endsWith('.css')).text;
const shots=root+'.runtime/similar-ui-shots';await fs.mkdir(shots,{recursive:true});
for(const width of [1470,1024,901,390])for(const mode of ['create','customer','opportunity','picker','done'])test(mode+' at '+width,async()=>{
 const b=await chromium.launch({channel:'chrome',headless:true}),c=await b.newContext({viewport:{width,height:779}}),p=await c.newPage(),errors=[],requests=[];
 await c.route('**/*',r=>{if(new URL(r.request().url()).pathname==='/')return r.fulfill({contentType:'text/html',body:'<!doctype html><html><head><meta charset="utf-8"><style>'+css+'</style></head><body class="department-design"><aside class="web-sidebar"></aside><main class="web-main"><div class="web-topbar"></div><div id="page-root" class="department-host"><div class="ant-app"><div id="root" class="department-ui"></div></div></div></main><script>'+js+'</script></body></html>'});requests.push(r.request().url());return r.abort();});p.on('pageerror',e=>errors.push(e.message));
 try{await p.goto('https://detail-layout.test/?case='+mode);await p.locator(mode==='picker'?'.ds-vc-opp-row':mode==='create'?'.ds-oc-grid':mode==='customer'?'.ds-cd-strip':mode==='opportunity'?'.ds-od-grid':'.ds-vc-done-actions').first().waitFor();
 await p.evaluate(()=>Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{}))));
 const geometry=await p.evaluate(mode=>{const bounds=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,w:r.width,h:r.height,sw:e.scrollWidth,cw:e.clientWidth};};const boxes=s=>[...document.querySelectorAll(s)].map(e=>({cls:e.className,...bounds(e)}));return {overflow:document.documentElement.scrollWidth-innerWidth,fields:boxes('.ds-oc-grid .sb-lselect,.ds-oc-grid .ant-input,.ds-oc-grid .ant-picker'),metrics:boxes('.ds-cd-strip,.ds-cd-strip .sb-mstrip-items,.ds-od-body .sb-mstrip-items'),panels:boxes('.ds-oc-body,.ds-cd-body,.ds-od-body'),rows:[...document.querySelectorAll('.ds-vc-opp-row')].map(e=>({row:bounds(e),children:[...e.children].map(bounds)})),done:[...document.querySelectorAll('.ds-vc-done-actions button')].map(bounds)};},mode);
 await fs.writeFile(shots+'/fixture-'+mode+'-'+width+'.json',JSON.stringify(geometry,null,2));await p.screenshot({path:shots+'/fixture-'+mode+'-'+width+'.png',fullPage:true});
 assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);assert.ok(geometry.overflow<=1,'页面不能横向溢出');
 if(mode==='create')assert.ok(geometry.fields.every(e=>e.right<=width&&e.w>=150),'表单控件完整且保留可读宽度：'+JSON.stringify(geometry.fields));
 if(['customer','opportunity'].includes(mode))assert.ok(geometry.metrics.length>0&&geometry.metrics.every(e=>e.sw<=e.cw+1&&e.right<=geometry.panels[0].right+1),'指标条不能裁切：'+JSON.stringify(geometry.metrics));
 if(mode==='picker'){assert.equal(geometry.rows.length,30);assert.ok(geometry.rows.every(({row,children})=>children.every(ch=>ch.y>=row.y&&ch.bottom<=row.bottom&&ch.right<=row.right)),'长商机名称与状态完整在行内');await p.locator('.ds-vc-opp-row').last().scrollIntoViewIfNeeded();assert.ok(await p.locator('.ds-vc-opp-list').evaluate(e=>e.scrollTop>0));}
 if(mode==='done')assert.ok(geometry.done.every(e=>e.right<=width),'完成页按钮不能越界');
 }finally{await b.close();}
});
