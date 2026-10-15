// Tests the production department bundle and generated Page adapters offline.
// Every browser request is fulfilled locally; no server or user profile is used.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {join} from 'node:path';
import {buildOutput,webRoot} from '../scripts/build-output.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output=buildOutput(),bundleContext={window:{}};
vm.runInNewContext(await fs.readFile(join(output,'bundle.js'),'utf8'),bundleContext);
const js=await fs.readFile(join(output,'department-ui/app.js'),'utf8');
const css=await fs.readFile(join(output,'department-ui/app.css'),'utf8');
const runtimeCss=await fs.readFile(join(webRoot,'runtime.css'),'utf8');

test('both report entry routes render the new module and the same lightweight Page lifecycle',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
  for(const route of ['pages/work-report/index','pages/weekly-report/index']) {
   let definition;
   vm.runInNewContext(bundleContext.window.SALES_BUNDLE.modules[route],{Page:value=>{definition=value;}});
   const page={...definition,data:{...definition.data},setData(patch){Object.assign(this.data,patch);}};
   page.onLoad({period:'biweek'});
   assert.deepEqual(Object.keys(page.data),['pcRoute','options']);
   assert.equal(page.onShow,undefined,'legacy weekly data collection is not registered');
   assert.equal(page.data.options.period,'biweek');
   const context=await browser.newContext({viewport:{width:1440,height:900}}),p=await context.newPage(),errors=[];
   p.on('pageerror',error=>errors.push(error.stack || error.message));p.setDefaultTimeout(10000);
   await context.route('**/*',request=>new URL(request.request().url()).pathname==='/department-app.js'?request.fulfill({contentType:'application/javascript; charset=utf-8',body:js}):request.fulfill({contentType:'text/html; charset=utf-8',body:`<!doctype html><meta charset="utf-8"><style>${runtimeCss}\n${css}</style><div id="page-root"></div><script>
    window.calls=[];window.owner='report-actor';window.syntaxErrors=[];window.addEventListener('error',event=>syntaxErrors.push({message:event.message,file:event.filename,line:event.lineno,column:event.colno}));
    const config={sections:[],group_by:'topic',style:{focus:[]},reference_texts:[],default_period:'month',default_sources:['visit']};
    const options={can_create:true,generation_available:false,default_scheme:config,periods:['day','week','two_weeks','month','quarter'],available_scope_kinds:['self','member'],members:[{id:'allowed-member',name:'合成同事'}],teams:[],sources:[{id:'visit',label:'拜访跟进',available:true}]};
    const client={request:async request=>{calls.push(request);if(request.path==='/work-reports/options')return options;
     if(request.path.startsWith('/work-reports/schemes?'))return {items:[],total:0,limit:100,offset:0,has_more:false,next_offset:null};throw Error('unexpected request '+request.path);}};
    window.SalesRuntime={app:{globalData:{session:{userId:'actor'}}},require:name=>name==='utils/apiClient'?client:{identity:()=>owner},replacePageQuery:()=>true};
    window.page={route:${JSON.stringify(route)},_id:1,options:{scope:'member',member:'allowed-member'}};SalesRuntime.current=page;
    </script><script src="/department-app.js"></script><script>SalesDepartmentUI.render(page,{},document.getElementById('page-root'));</script>`}));
   try {
    await p.goto('https://report-entry.test/');
    await p.getByRole('button',{name:'报告方案',exact:true}).waitFor();
    await p.getByText('请先选择或创建报告方案',{exact:true}).waitFor();
    assert.match(await p.locator('.wr-scope-controls').innerText(),/合成同事/);
    const period=await p.getByRole('combobox',{name:'报告周期'}).evaluate(element=>element.closest('.ant-select').innerText);
    assert.match(period,route.includes('weekly-report')?/周报/:/月报/);
    assert.equal(await p.getByText('当前账号可查看已有报告，暂不可生成新报告',{exact:true}).count(),0);
    assert.deepEqual(await p.evaluate(()=>calls.map(item=>[item.method,item.path,item.fresh])),[
     ['GET','/work-reports/options',true],['GET','/work-reports/schemes?limit=100&offset=0',true]]);
    await p.evaluate(()=>{owner='new-actor';SalesDepartmentUI.render(page,{},document.getElementById('page-root'));});
    await p.waitForFunction(()=>calls.filter(item=>item.path==='/work-reports/options').length===2);
    assert.deepEqual(errors,[]);
   } catch(error) {console.error({errors,syntax:await p.evaluate(()=>window.syntaxErrors),body:await p.locator('body').innerText(),calls:await p.evaluate(()=>window.calls)});throw error;} finally {await context.close();}
  }
 } finally {await browser.close();}
});
