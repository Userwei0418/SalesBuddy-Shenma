import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from '../node_modules/esbuild/lib/main.js';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React from'react';import{createRoot}from'react-dom/client';import{flushSync}from'react-dom';import{App}from'antd';import{PcProvider}from'./department-ui/pc/PcProvider.jsx';import{DocumentExport}from'./department-ui/work-report/ReportDocumentDialogs.jsx';
import './runtime.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';
window.owner='actor';window.SalesRuntime={app:{globalData:{session:{}}},require:()=>({identity:()=>owner})};
window.calls=[];window.copies=[];window.lost=[];window.pending=[];window.failure=0;window.hold=false;window.revisionId='revision-one';window.closed=false;
const response=(id,rid)=>({document_id:id,revision_id:rid,text:'新读取正文 '+calls.length,filename:'合成导出.md',mime_type:'text/markdown; charset=utf-8'});
const service={export:async(id,rid,format)=>{calls.push({id,rid,format});if(failure)throw Object.assign(Error('导出权限已变化'),{status:failure});if(hold)return new Promise(resolve=>pending.push(()=>resolve(response(id,rid))));return response(id,rid);}};
Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>copies.push(text)}});
window.root=createRoot(document.getElementById('root'));window.render=()=>flushSync(()=>root.render(<PcProvider><App>{!closed && <DocumentExport owner={owner} document={{id:'document',revision:{id:revisionId,revision_no:1,content:{title:'合成导出'}}}} service={service} onClose={()=>{closed=true;render();}} onAccessLost={error=>lost.push(error.status)}/>}</App></PcProvider>));render();
`},bundle:true,write:false,outdir:'.runtime/export-check',define:{'process.env.NODE_ENV':'"production"'}});
const js=built.outputFiles.find(f=>f.path.endsWith('.js')).text,css=built.outputFiles.find(f=>f.path.endsWith('.css')).text;
async function fixture(run){
 const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext(),p=await context.newPage(),errors=[],downloads=[];
 p.on('pageerror',error=>errors.push(error.message));p.on('download',download=>downloads.push(download));p.setDefaultTimeout(10000);
 await context.route('**/*',request=>request.fulfill({contentType:'text/html; charset=utf-8',body:'<!doctype html><meta charset="utf-8"><style>'+css+'</style><div id="root"></div><script>'+js+'</script>'}));
 try{await p.goto('https://report-export.test/');await p.waitForFunction(()=>document.querySelectorAll('button').length && [...document.querySelectorAll('button')].some(button=>button.textContent==='复制正文' && !button.disabled));await run(p,downloads);assert.deepEqual(errors,[]);}finally{await context.close();await browser.close();}
}
for(const action of ['复制正文','下载 Markdown'])test('revoked permission after opening prevents '+action,()=>fixture(async(p,downloads)=>{
 assert.equal(await p.evaluate(()=>calls.length),1);await p.evaluate(()=>failure=403);await p.getByRole('button',{name:action,exact:true}).click();
 await p.getByText('导出权限已变化',{exact:true}).waitFor();assert.equal(await p.evaluate(()=>calls.length),2);assert.deepEqual(await p.evaluate(()=>copies),[]);assert.deepEqual(await p.evaluate(()=>lost),[403]);assert.equal(downloads.length,0);
 assert.equal(await p.locator('.wr-export-summary').count(),0);assert.ok(await p.getByRole('button',{name:'复制正文',exact:true}).isDisabled());assert.ok(await p.getByRole('button',{name:'下载 Markdown',exact:true}).isDisabled());
}));
test('each explicit export uses newly authorized bytes for the selected revision',()=>fixture(async(p)=>{
 await p.getByRole('button',{name:'复制正文',exact:true}).click();await p.waitForFunction(()=>copies.length===1);assert.deepEqual(await p.evaluate(()=>copies),['新读取正文 2']);
 const downloadEvent=p.waitForEvent('download');await p.getByRole('button',{name:'下载 Markdown',exact:true}).click();const downloaded=await downloadEvent;assert.equal(await fs.readFile(await downloaded.path(),'utf8'),'新读取正文 3');
 assert.deepEqual(await p.evaluate(()=>calls),Array.from({length:3},()=>({id:'document',rid:'revision-one',format:'markdown'})));
}));
for(const change of ['identity','revision','close'])test('pending export discards late bytes after '+change,()=>fixture(async(p,downloads)=>{
 await p.evaluate(()=>hold=true);await p.getByRole('button',{name:'复制正文',exact:true}).click();await p.waitForFunction(()=>pending.length===1);
 assert.equal(await p.getByRole('button',{name:'下载 Markdown',exact:true}).isDisabled(),true);assert.equal(await p.evaluate(()=>calls.length),2);
 if(change==='close')await p.getByRole('button',{name:'关闭',exact:true}).last().click();
 else await p.evaluate(change=>{if(change==='identity'){owner='new-actor';hold=false;render();}else{revisionId='revision-two';hold=false;render();}},change);
 await p.evaluate(()=>pending[0]());await p.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 assert.deepEqual(await p.evaluate(()=>copies),[]);assert.equal(downloads.length,0);
 if(change==='revision')assert.equal(await p.evaluate(()=>calls.at(-1).rid),'revision-two');
}));
