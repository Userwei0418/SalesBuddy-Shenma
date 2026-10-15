import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from '../node_modules/esbuild/lib/main.js';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
test('scheme resource flow creates a private scheme, retains conflicts and hides denied content',async()=>{
 const root=fileURLToPath(new URL('../',import.meta.url));
 const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
 import React from'react';import{createRoot}from'react-dom/client';import{App}from'antd';import{PcProvider}from'./department-ui/pc/PcProvider.jsx';import ReportSchemes from'./department-ui/work-report/ReportSchemes.jsx';
 import './runtime.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/work-report/work-report.css';
 window.SalesRuntime={app:{globalData:{session:{}}},require:()=>({identity:()=> 'owner'})};window.calls=[];window.rows=[];window.fail=null;
 const config={sections:[{id:'progress',title:'重点进展',enabled:true,instructions:''}],group_by:'topic',style:{audience:'直属负责人',tone:'concise',length:'medium',voice:'first_person',language:'zh-CN',focus:[]},reference_texts:[],default_period:'week',default_sources:['visit']};
 const service={schemes:async()=>({items:rows,has_more:false}),scheme:async id=>rows.find(r=>r.id===id),createScheme:async payload=>{calls.push({method:'create',payload});let row={...payload,id:'created-'+calls.length,version_no:1,can_edit:true};rows.push(row);return row},updateScheme:async(id,payload)=>{calls.push({method:'update',payload});if(fail)throw Object.assign(Error(fail===409?'版本冲突，请重新读取':'权限已变化'),{status:fail});const row={...payload,id,version_no:payload.version_no+1,can_edit:true};rows=[row];return row}};
 const weekly={...config,facts_contract:'work_report.sales_week_facts.v1',sections:[{id:'summary',title:'本周工作总结',enabled:true,instructions:''}]};
 createRoot(document.getElementById('root')).render(<PcProvider><App><main className="department-ui"><ReportSchemes owner="owner" page={{}} service={service} options={{default_scheme:weekly,general_scheme:config,can_create:true,sources:[{id:'visit',label:'拜访跟进',available:true},{id:'actual',label:'正式回款',available:true}]}}/></main></App></PcProvider>);
 `},bundle:true,write:false,outdir:'.runtime/scheme-check',define:{'process.env.NODE_ENV':'"production"'}});
 const js=built.outputFiles.find(f=>f.path.endsWith('.js')).text,css=built.outputFiles.find(f=>f.path.endsWith('.css')).text;
 const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext({viewport:{width:1200,height:1000}}),p=await context.newPage();const errors=[];p.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',r=>r.fulfill({contentType:'text/html',body:`<!doctype html><head><style>${css}</style></head><div id="page-root"><div id="root"></div></div><script>${js}</script>`}));
 try{
  await p.goto('https://report-scheme.test/');await p.getByRole('button',{name:'新建个人方案'}).click();await p.getByLabel('方案名称').fill('合成销售周报');
  await p.getByRole('button',{name:'添加参考范文',exact:true}).click();await p.getByLabel('参考范文 1',{exact:true}).fill('我的私人风格参考');
  await p.getByRole('button',{name:'保存方案',exact:true}).click();await p.waitForFunction(()=>calls.length===1 && rows.length===1);await p.getByRole('button',{name:'合成销售周报 个人方案'}).waitFor();
  const created=await p.evaluate(()=>calls[0].payload);assert.equal(created.visibility,'private');assert.equal(created.team_id,null);assert.equal(created.config.reference_texts[0].version_no,1);assert.equal(created.config.reference_texts[0].text,'我的私人风格参考');assert.equal('originalText' in created.config.reference_texts[0],false);
  await p.getByLabel('方案名称').fill('修改后保留');await p.evaluate(()=>{fail=409});await p.getByRole('button',{name:'保存方案',exact:true}).click();await p.getByText('版本冲突，请重新读取',{exact:true}).waitFor();
  assert.equal(await p.getByLabel('方案名称').evaluate(el=>el.value),'修改后保留');assert.equal(await p.evaluate(()=>calls[1].payload.version_no),1);assert.equal(await p.evaluate(()=>calls.length),2);
  await p.evaluate(()=>{fail=403});await p.getByRole('button',{name:'保存方案',exact:true}).click();await p.getByText('权限已变化',{exact:true}).waitFor();assert.equal(await p.getByLabel('参考范文 1',{exact:true}).count(),0);assert.equal(await p.getByRole('button',{name:'保存方案',exact:true}).count(),0);assert.deepEqual(errors,[]);
  await p.getByRole('button',{name:'新建通用方案',exact:true}).click();await p.getByLabel('方案名称').fill('合成通用季报');
  assert.equal(await p.getByRole('checkbox',{name:'正式回款',exact:true}).count(),0);
  await p.getByRole('combobox',{name:'默认报告周期'}).click();await p.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:/^季度报$/}).click();
  await p.getByRole('button',{name:'保存方案',exact:true}).click();await p.waitForFunction(()=>calls.length===4);
  const general=await p.evaluate(()=>calls[3].payload);assert.equal(general.config.default_period,'quarter');assert.equal('facts_contract' in general.config,false);assert.equal(general.config.sections[0].id,'progress');
  const original=await p.evaluate(()=>rows[0]);assert.equal(original.config.facts_contract,'work_report.sales_week_facts.v1');assert.equal(original.config.default_period,'week');
  assert.deepEqual(errors,[]);
 }finally{await context.close();await browser.close();}
});
