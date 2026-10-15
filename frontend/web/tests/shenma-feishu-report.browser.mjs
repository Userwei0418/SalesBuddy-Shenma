import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from '../node_modules/esbuild/lib/main.js';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React from'react';import{createRoot}from'react-dom/client';import{PcProvider}from'./department-ui/pc/PcProvider.jsx';import FeishuPublish from'./department-ui/work-report/FeishuPublish.jsx';
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';
window.actorIdentity='owner';window.SalesRuntime={app:{globalData:{session:{}}},require:()=>({identity:()=>actorIdentity})};window.calls=[];window.reads=0;window.denied=[];window.failPost=false;window.rejectPost=0;window.reloads=0;window.failRead=false;window.holdRead=false;window.resolveRead=null;
window.receipt={status:'not_published',can_publish:true,blocked_reason:null,event_id:null,revision_id:null,record_id:null,message_id:null,error_code:null};
window.doc={id:'11111111-1111-4111-8111-111111111111',owner_id:'actor',can_share:true,can_export:false,version_no:5,latest_revision_id:'22222222-2222-4222-8222-222222222222',title:'合成普通工作报告',revision:{id:'22222222-2222-4222-8222-222222222222',revision_no:3,content:{title:'合成普通工作报告'}}};window.dirty=false;
const service={feishu:async()=>{reads++;if(failRead)throw Error('状态读取暂时失败');if(holdRead)return new Promise(r=>resolveRead=r);return {...receipt};},publishFeishu:async(id,payload)=>{calls.push({id,payload});if(rejectPost)throw Object.assign(Error('synthetic rejection'),{statusCode:rejectPost});receipt={...receipt,status:'pending',can_publish:false,blocked_reason:'already_published',event_id:'33333333-3333-4333-8333-333333333333',revision_id:doc.latest_revision_id};if(failPost)throw Object.assign(Error('synthetic timeout'),{code:'NETWORK_ERROR'});return{document_id:id,revision_id:doc.latest_revision_id,event_id:receipt.event_id,status:'queued'};}};
const root=createRoot(document.getElementById('root'));window.render=()=>root.render(<PcProvider><FeishuPublish document={doc} actorId="actor" owner="owner" dirty={dirty} locked={false} service={service} onAccessLost={error=>denied.push(error.message)} onReload={()=>reloads++}/></PcProvider>);render();
`},bundle:true,write:false,outdir:'out',loader:{'.svg':'dataurl','.woff2':'dataurl'},define:{'process.env.NODE_ENV':'"production"'}});
const js=built.outputFiles.find(f=>f.path.endsWith('.js')).text,css=built.outputFiles.find(f=>f.path.endsWith('.css')).text;
async function fixture(width,run){
 const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext({viewport:{width,height:1000}}),p=await context.newPage(),errors=[];
 p.on('pageerror',e=>errors.push(e.message));p.setDefaultTimeout(10000);
 await context.route('**/*',r=>r.fulfill({contentType:'text/html',body:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+css+'</style><div id="root" style="padding:16px"></div><script>'+js+'</script>'}));
 try{await p.goto('https://synthetic-shenma-feishu.test/');await p.getByRole('button',{name:'同步飞书并推送群',exact:true}).waitFor();await p.waitForFunction(()=>reads>0);await run(p);assert.deepEqual(errors,[]);}
 catch(error){await fs.mkdir(root+'.runtime/shenma-feishu-report',{recursive:true});await fs.writeFile(root+'.runtime/shenma-feishu-report/failure-'+width+'.json',JSON.stringify({errors,body:await p.locator('body').innerText(),html:await p.locator('body').innerHTML()},null,2));await p.screenshot({path:root+'.runtime/shenma-feishu-report/failure-'+width+'.png',fullPage:true});throw error;}
 finally{await context.close();await browser.close();}
}
for(const width of [1440,390])test(`Shenma report explicit saved-revision publish and separate table/group receipts at ${width}`,()=>fixture(width,async p=>{
 const button=p.getByRole('button',{name:'同步飞书并推送群',exact:true});await button.click();await p.getByRole('dialog').getByText('报告：合成普通工作报告',{exact:true}).waitFor();await p.getByRole('button',{name:/^取\s*消$/}).click();assert.equal(await p.evaluate(()=>calls.length),0);
 await button.click();await p.getByRole('button',{name:'确认同步并推送',exact:true}).click();await p.getByText('多维表格：等待同步',{exact:true}).waitFor();assert.equal(await button.isDisabled(),true);
 assert.deepEqual(await p.evaluate(()=>calls),[{id:'11111111-1111-4111-8111-111111111111',payload:{expected_version:5,revision_id:'22222222-2222-4222-8222-222222222222',confirm:true}}]);
 await p.evaluate(()=>receipt={...receipt,status:'failed',record_id:'synthetic-record',error_code:'SYNTHETIC_DELIVERY_FAILED'});await p.getByRole('button',{name:'刷新飞书状态',exact:true}).click();await p.getByText('多维表格：已入表',{exact:true}).waitFor();await p.getByText('群通知：未完成',{exact:true}).waitFor();assert.equal(await p.evaluate(()=>calls.length),1);
 await p.evaluate(()=>receipt={...receipt,status:'sent',message_id:'synthetic-message',error_code:null});await p.getByRole('button',{name:'刷新飞书状态',exact:true}).click();await p.getByText('群通知：已推送',{exact:true}).waitFor();
 const panel=await p.getByRole('region',{name:'飞书同步'}).boundingBox();assert.ok(panel.x>=0 && panel.x+panel.width<=width+1,'panel fits viewport');
 await fs.mkdir(root+'.runtime/shenma-feishu-report',{recursive:true});await p.screenshot({path:root+'.runtime/shenma-feishu-report/report-'+width+'.png',fullPage:true});
}));
test('unsaved changes, non-owner and source eligibility prevent publish with no POST',()=>fixture(390,async p=>{
 const button=p.getByRole('button',{name:'同步飞书并推送群',exact:true});await p.evaluate(()=>{dirty=true;render();});await p.getByText('请先保存当前修改，再同步飞书。',{exact:true}).waitFor();assert.equal(await button.isDisabled(),true);
 for(const reason of ['source_excluded','aggregate_provenance_not_supported']){
  await p.evaluate(reason=>{dirty=false;receipt={...receipt,can_publish:false,blocked_reason:reason};render();},reason);await p.getByRole('button',{name:'刷新飞书状态',exact:true}).click();await p.waitForFunction(()=>!document.querySelector('button.ant-btn-loading'));assert.equal(await button.isDisabled(),true);
 }
 await p.evaluate(()=>{doc={...doc,owner_id:'other'};render();});await button.waitFor({state:'detached'});assert.equal(await p.evaluate(()=>calls.length),0);
}));
test('lost publish response checks GET and never duplicates an accepted notification',()=>fixture(1440,async p=>{
 await p.evaluate(()=>failPost=true);await p.getByRole('button',{name:'同步飞书并推送群',exact:true}).click();await p.getByRole('button',{name:'确认同步并推送',exact:true}).click();await p.getByText('提交结果需核对，请刷新飞书状态；未核对前不会重复提交。',{exact:true}).waitFor();
 assert.equal(await p.evaluate(()=>calls.length),1);assert.ok(await p.evaluate(()=>reads)>=2);await p.getByRole('button',{name:'刷新飞书状态',exact:true}).click();await p.getByText('多维表格：等待同步',{exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'同步飞书并推送群',exact:true}).isDisabled(),true);assert.equal(await p.evaluate(()=>calls.length),1);
}));
test('late receipt from a preceding identity is discarded',()=>fixture(1440,async p=>{
 await p.evaluate(()=>holdRead=true);await p.getByRole('button',{name:'刷新飞书状态',exact:true}).click();await p.waitForFunction(()=>Boolean(resolveRead));
 await p.evaluate(()=>{actorIdentity='other';resolveRead({...receipt,status:'sent',record_id:'old-private-record',message_id:'old-private-message'});render();});await p.getByRole('region',{name:'飞书同步'}).waitFor({state:'detached'});assert.equal(await p.getByText('群通知：已推送',{exact:true}).count(),0);assert.equal(await p.evaluate(()=>calls.length),0);
}));

test('version conflict preserves state and requires document reload before another publish',()=>fixture(390,async p=>{
 await p.evaluate(()=>rejectPost=409);await p.getByRole('button',{name:'同步飞书并推送群',exact:true}).click();await p.getByRole('button',{name:'确认同步并推送',exact:true}).click();await p.getByText('报告版本或发布状态已变化，请重新读取报告后核对。',{exact:true}).waitFor();
 await p.getByRole('button',{name:'刷新飞书状态',exact:true}).click();assert.equal(await p.getByRole('button',{name:'同步飞书并推送群',exact:true}).isDisabled(),true);await p.getByRole('button',{name:'重新读取报告',exact:true}).click();assert.equal(await p.evaluate(()=>reloads),1);assert.equal(await p.evaluate(()=>calls.length),1);
}));
test('unknown and malformed receipts never offer a new publish',()=>fixture(1440,async p=>{
 await p.evaluate(()=>receipt={...receipt,status:'unknown',can_publish:false,event_id:'33333333-3333-4333-8333-333333333333',record_id:'synthetic-record'});await p.getByRole('button',{name:'刷新飞书状态',exact:true}).click();await p.getByText('群推送结果未知',{exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'同步飞书并推送群',exact:true}).isDisabled(),true);
 await p.evaluate(()=>receipt={status:'failed',can_publish:true});await p.getByRole('button',{name:'刷新飞书状态',exact:true}).click();await p.getByText('飞书同步状态不完整，请重新读取',{exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'同步飞书并推送群',exact:true}).isDisabled(),true);assert.equal(await p.evaluate(()=>calls.length),0);
}));
test('failed status read after uncertain publish remains unknown and recovers without another POST',()=>fixture(390,async p=>{
 await p.evaluate(()=>{failPost=true;failRead=true;});await p.getByRole('button',{name:'同步飞书并推送群',exact:true}).click();await p.getByRole('button',{name:'确认同步并推送',exact:true}).click();await p.getByText('发布结果待核对',{exact:true}).waitFor();await p.getByText('多维表格：待核对',{exact:true}).waitFor();await p.getByText('群通知：待核对',{exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'同步飞书并推送群',exact:true}).isDisabled(),true);
 await p.evaluate(()=>failRead=false);await p.getByRole('button',{name:'刷新飞书状态',exact:true}).click();await p.getByText('多维表格：等待同步',{exact:true}).waitFor();assert.equal(await p.evaluate(()=>calls.length),1);
}));
