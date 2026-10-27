// Real built modules; synthetic API responses, no production credentials.
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {gzipSync} from 'node:zlib';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser,server,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>server?.close(r));});
async function fixture(run){
 const context=await browser.newContext(),page=await context.newPage(),requests=[],errors=[];
 page.on('request',r=>requests.push(new URL(r.url()).pathname));page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/api/**',r=>r.fulfill({status:503,json:{detail:'Synthetic unavailable API'}}));
 try{await run({page,requests});assert.deepEqual(errors,[]);}finally{await context.close();}
}
test('cold login loads only route modules, original images on demand, and no PDF reader or standalone ECharts',()=>fixture(async({page,requests})=>{
 const started=Date.now();await page.goto(base+'/?mode=live#/pages/login/index');
 await page.locator('.ds-login').waitFor();
 const scripts=requests.filter(x=>x.endsWith('.js'));
 assert.ok(scripts.some(x=>/chunks\/Login-/.test(x)));
 assert.ok(!scripts.some(x=>/chunks\/(Knowledge|ReportWorkspace|Bi|CustomerDetail|OpportunityDetail)-/.test(x)));
 assert.ok(!scripts.some(x=>/pdfjs|echarts.min/.test(x)));
 const bundle=fs.readFileSync(new URL('../dist/bundle.js',import.meta.url),'utf8');
 assert.ok(!bundle.includes('data:image/'));assert.ok(bundle.includes('assets/source-images/'));
 const bytes=scripts.map(path=>new URL('../dist'+path,import.meta.url)).filter(path=>fs.existsSync(path)).map(path=>fs.readFileSync(path));
 console.log(JSON.stringify({loginReadyMs:Date.now()-started,scriptRequests:scripts.length,rawBytes:bytes.reduce((n,b)=>n+b.length,0),gzipBytes:bytes.reduce((n,b)=>n+gzipSync(b).length,0)}));
}));
test('failed lazy page remains in new UI with explicit retry instead of rendering legacy form',()=>fixture(async({page})=>{
 let failed=true;
 await page.route('**/chunks/Login-*.js',r=>failed?r.abort():r.continue());
 await page.goto(base+'/?mode=live#/pages/login/index');
 await page.getByText('页面未能加载，请重试。',{exact:false}).waitFor();
 assert.equal(await page.locator('.ds-login').count(),0);
 failed=false;await page.getByRole('button',{name:'重新加载',exact:true}).click();await page.locator('.ds-login').waitFor();
}));
