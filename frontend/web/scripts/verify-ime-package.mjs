// Run against an extracted delivery without installing the project's dependencies.
import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {createSalesWebServer} from '../server.mjs';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const out=resolve(process.env.IME_PACKAGE_EVIDENCE || '.runtime/ime-package-verify');await mkdir(out,{recursive:true});
const release=JSON.parse(await readFile(resolve(root,'IME_FIX_RELEASE.json'),'utf8'));
for(const [file,hash] of Object.entries(release.hashes))assert.equal(createHash('sha256').update(await readFile(resolve(root,file))).digest('hex'),hash,file+' release hash');
const server=createSalesWebServer({previewOnly:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base=`http://127.0.0.1:${server.address().port}`,browser=await chromium.launch({channel:'chrome',headless:true});
const report={at:new Date().toISOString(),root,revision:release.revision,browser:browser.version(),releaseHashes:'passed',noProjectDependenciesRequired:true,cases:[]};
try {
 for(const [name,route,selector] of [
  ['visit-note','visit-entry','textarea.sb-textarea'],['visit-search','visit-entry','input[type=search]'],
  ['task-description','management-task-create','#department-task-description'],['claim-search','customer-claim','.ds-sp-search input'],
  ['customer-create','customer-create','#customer-create-customer_name'],['customer-search','customers','.ds-customer-search input'],
 ]){
  const ctx=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'}),blocked=[],errors=[];
  await ctx.addInitScript(()=>sessionStorage.setItem('sales-web:preview-role','sales'));
  await ctx.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==base||u.pathname.startsWith('/api/')){blocked.push(u.href);return r.abort();}return r.continue();});
  const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
  try {
   await p.goto(base+'/?mode=preview#/pages/'+route+'/index');const input=p.locator(selector);await input.waitFor();await input.focus();
   const cdp=await ctx.newCDPSession(p);
   for(const text of ['n','ni','nihao']){
    await cdp.send('Input.imeSetComposition',{text,selectionStart:text.length,selectionEnd:text.length});
    await p.evaluate(()=>SalesRuntime.current.setData({imePackageRefresh:true}));
    await p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert.equal(await input.inputValue(),text);
   }
   await cdp.send('Input.insertText',{text:'你好'});await p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
   assert.equal(await input.inputValue(),'你好');assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);
   await p.screenshot({path:resolve(out,name+'.png')});report.cases.push({name,route,expected:'你好',actual:await input.inputValue(),status:'passed'});
  }finally{await ctx.close();}
 }
 report.status='passed';
}finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));await writeFile(resolve(out,'package-verification.json'),JSON.stringify(report,null,2));}
console.log(JSON.stringify(report,null,2));
