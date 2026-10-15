/** Real-time report layout; actual React components and styles, synthetic read results, no API. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from '../node_modules/esbuild/lib/main.js';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const built = await build({absWorkingDir: root, stdin: {resolveDir: root, loader: 'jsx', contents: `
import React from 'react';import {createRoot} from 'react-dom/client';import {App} from 'antd';
import {PcProvider} from './department-ui/pc/PcProvider.jsx';import WorkReport from './department-ui/WorkReport.jsx';import {renderFrame} from './department-ui/Frame.jsx';
import {normalizeVisit} from './department-ui/pc/data.js';
import './runtime.css';import './desktop.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';import './department-ui/pc/workspace.css';import './department-ui/pc/workspace-layout.css';import './department-ui/pc/overview-filters.css';
window.SALES_MODE='live';window.SalesRuntime={app:{globalData:{session:{workspaceId:'layout',userId:'a',userName:'合成销售甲',permissionVersion:'v1',teamIds:['south'],role:'sales'}},can:()=>false},require:()=>({identity:()=> 'layout-owner',scopeFor:()=>({allowed:true,scopes:['teams']})})};
const members=['甲','乙','丙'].map((n,i)=>({id:['a','b','c'][i],name:'合成销售'+n,role:'sales',team_ids:['south'],teamLabel:'合成南区'}));
const visits=[1,2,3].map(n=>normalizeVisit({id:'visit'+n,customer_id:'customer'+n,customer_name:'合成客户'+n,opportunity_id:'opportunity'+n,opportunity_name:'合成商机'+n,recorder_id:'a',recorder_name:'合成销售甲',recorder_role:'sales',visit_date:'2026-10-08',status:'confirmed',follow_up_record:'已完成需求沟通，核对客户经营范围和跟进展示。',next_action:'下周安排方案复核，与客户确认下一步计划。'}));
window.fixture={directory:{members,teams:[{id:'south',name:'合成南区'}],people:new Map(members.map(m=>[m.id,m])),defaultTeamId:'south'},visits,opportunities:visits.map((v,i)=>({id:'opportunity'+i,name:'合成商机'+i,customerId:v.customerId,customerName:v.customerName,stage:'方案沟通',amount:(i+1)*100000,close:'2026-11-01',lastVisit:v})),risks:Array.from({length:17},(_,i)=>({id:'risk'+i,title:'合成风险 '+i+'：关键节点需协调',customerName:'合成客户',opportunityName:'合成项目'}))};
window.frame={workspaceName:'合成公司',workspaceScope:'按功能授权',accountName:'合成销售甲',accountTeam:'合成南区',title:'周报 · 本周',activeKey:'reports.week',status:'ready',groups:[{key:'workspace',text:'工作台',path:'home'},...['客户','商机','拜访与跟进','任务'].map((n,i)=>({key:'group'+i,text:n,children:[{key:'leaf'+i,text:n+'列表',path:'list'+i}]})),{key:'reports',text:'工作报告',icon:'report',children:['day','week','biweek','month','quarter'].map((k,i)=>({key:'reports.'+k,text:['日报 · 今日','周报 · 本周','双周报 · 上周＋本周','月报 · 本月','季度报 · 本季度'][i],path:k}))},{key:'insights',text:'经营分析',children:[{key:'growth',text:'成长与评估',path:'growth'}]}],onSelect:(path,key)=>{frame.activeKey=key;renderFrame({...frame});}};
window.repaint=()=>renderFrame({...frame,groups:frame.groups.map(g=>({...g,children:g.children?.map(c=>({...c}))}))});repaint();
window.reportRoot=createRoot(document.getElementById('root'));window.renderReport=(period='week')=>reportRoot.render(<PcProvider><App><WorkReport page={{route:'pages/work-report/index',options:{period,scope:'team',team_id:'south'}}}/></App></PcProvider>);renderReport();
`}, plugins: [{name: 'synthetic-read-results', setup(b) {
  b.onResolve({filter:/^\.\/pc\/(data|workspace-data)\.js$/}, args => args.importer.endsWith('/WorkReport.jsx') ? {path:args.path,namespace:'synthetic'} : null);
  b.onLoad({filter:/.*/,namespace:'synthetic'}, args => ({loader:'js',resolveDir:root,contents:args.path.endsWith('/workspace-data.js') ? `export const loadWorkspaceTasks=async()=>({total:11,counts:{overdue:11},overdue:[{id:'task',title:'合成逾期任务'}]});export const loadWorkspaceRisks=async()=>({items:fixture.risks,total:17});` : `export * from '${root}department-ui/pc/data.js';export const loadDirectory=async()=>fixture.directory;export const collectFollowups=async()=>({items:fixture.visits});export const loadActiveOpportunities=async()=>({items:fixture.opportunities});export const loadCompletedTasks=async()=>({items:[]});export const loadNewOpportunities=async()=>({items:[]});export const defaultScope=()=>({kind:'team',teamId:'south'});`}));
}}],bundle:true,write:false,loader:{'.svg':'dataurl'},outdir:'.runtime/report-layout-check',define:{'process.env.NODE_ENV':'"production"'}});
const js=built.outputFiles.find(f=>f.path.endsWith('.js')).text,css=built.outputFiles.find(f=>f.path.endsWith('.css')).text;
const shots=root+'.runtime/work-report-layout-shots';await fs.mkdir(shots,{recursive:true});
for (const width of [1920,1440,1024,768]) test(`real-time report and navigation layout at ${width}px`,async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext({viewport:{width,height:1000}}),p=await context.newPage(),errors=[],unexpected=[];
 p.setDefaultTimeout(8000);p.on('pageerror',e=>errors.push(e.message));p.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 await context.route('**/*',async r=>{const url=new URL(r.request().url());if(url.pathname==='/')return r.fulfill({contentType:'text/html',body:`<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body class="department-design"><aside class="web-sidebar" id="frame-sidenav"></aside><main class="web-main"><div class="web-topbar" id="frame-topbar"></div><div id="page-root" class="department-host"><div id="root" class="department-ui"></div></div></main><script>${js}</script></body></html>`});if(url.pathname.startsWith('/assets/brand/'))return r.fulfill({contentType:'image/svg+xml',body:await fs.readFile(root+url.pathname)});unexpected.push(url.pathname);return r.abort();});
 try{
  await p.goto('https://work-report-layout.test/');await p.getByText('重点商机进展',{exact:true}).waitFor();
  await p.locator('.pc-wr-member').first().waitFor();
  const geometry=await p.evaluate(()=>{const main=document.querySelector('.pc-wr-main'),panels=main.children,rect=x=>({top:x.getBoundingClientRect().top,bottom:x.getBoundingClientRect().bottom,height:x.getBoundingClientRect().height});return{panels:[...panels].map(rect),idle:[...document.querySelectorAll('.pc-wr-member.is-idle')].map(rect),side:rect(document.querySelector('.pc-wr-side')),pageOverflow:document.documentElement.scrollWidth-innerWidth,mainOverflow:main.scrollWidth-main.clientWidth};});
  assert.ok(geometry.panels[1].top-geometry.panels[0].bottom<=24,'pipeline immediately follows member progress');
  assert.equal(geometry.idle.length,2);assert.ok(geometry.idle.every(r=>r.height<100),'idle member cards are compact');assert.ok(geometry.pageOverflow<=1);assert.ok(geometry.mainOverflow<=1);
  if(width>1180)assert.ok(geometry.panels[1].top<geometry.side.bottom-200,'tall risk list does not push the pipeline down');
  const group=()=>p.locator('.pc-nav-menu .ant-menu-submenu-title').filter({hasText:/工作报告/});
  assert.equal(await group().count(),1);
  await p.evaluate(()=>{for(let i=0;i<30;i++){frame.activeKey=['reports.day','reports.week','reports.month'][i%3];repaint();}});
  assert.equal(await group().count(),1);assert.equal(await p.locator('#frame-sidenav .pc-nav').count(),1);
  if(width>900){await p.locator('.pc-nav-menu').evaluate(e=>e.scrollTop=e.scrollHeight);assert.equal(await group().count(),1);await p.locator('.pc-nav-menu').evaluate(e=>e.scrollTop=0);}
  await p.screenshot({path:shots+`/report-${width}.png`,fullPage:true});
  await p.locator('.pc-report-results').evaluate(e=>e.scrollTop=e.scrollHeight);assert.equal(await group().count(),1);await p.screenshot({path:shots+`/report-${width}-bottom.png`,fullPage:true});
  if(!await p.getByRole('button',{name:'会议模式',exact:true}).isVisible())await p.getByRole('button',{name:/更多条件/}).click();await p.getByRole('button',{name:'会议模式',exact:true}).click();await p.getByRole('dialog',{name:'会议模式'}).waitFor();await p.getByRole('button',{name:'商机',exact:true}).click();assert.equal(await p.getByRole('dialog',{name:'会议模式'}).getByText('合成商机0',{exact:true}).count(),1);await p.getByRole('button',{name:'退出会议模式'}).click();
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);await fs.writeFile(shots+`/geometry-${width}.json`,JSON.stringify(geometry,null,2));
 }catch(e){await p.screenshot({path:shots+`/failure-${width}.png`,fullPage:true});console.error(await p.locator('body').innerText(),errors);throw e;}finally{await context.close();await browser.close();}
});
