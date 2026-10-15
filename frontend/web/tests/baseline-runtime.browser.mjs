/** Browser compatibility for the current Mini Program baseline; isolated synthetic data, no API or microphone. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let server, browser, base;
before(async () => {
  server = createSalesWebServer({target:''});
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel:'chrome',headless:true});
});
after(async () => { await browser?.close(); server?.closeAllConnections(); await new Promise(resolve=>server?.close(resolve)); });
async function fixture(run) {
  const context = await browser.newContext({viewport:{width:1440,height:1000}}), unexpected = [], errors = [];
  await context.route('**/*', route=>{
    const url=new URL(route.request().url());
    if(url.origin!==base||url.pathname.startsWith('/api/')||url.pathname==='/local-login') {unexpected.push(url.pathname);return route.abort();}
    return route.continue();
  });
  const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
  try {
    await page.goto(base+'/?mode=preview');
    await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/index/index');
    await run(page);
    assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);
  } finally {await context.close();}
}
test('shared opportunity templates keep parent row navigation and isolated edit actions',()=>fixture(async page=>{
  await page.evaluate(()=>{
    const tree=SALES_BUNDLE.pages['pages/workbench/index'].templates['opportunity-list-card'];
    const route='pages/web-runtime-test/index';
    SALES_BUNDLE.pages[route]={config:{},css:'',templates:{card:tree},tree:[{t:'view',a:{'data-id':'{{item.id}}',bindtap:'open'},c:[{t:'template',a:{is:'card',data:'{{item: item}}'},c:[]}]}]};
    SALES_BUNDLE.modules[route]=`Page({data:{item:{id:'op-synthetic',customer_id:'customer-synthetic',name:'合成商机',customer_name:'合成客户',canEdit:true,signal:{tone:'green',label:'正常',detail:'合成状态'},recognizedLabel:'¥0',collectionLabel:'未填写'},opened:'',edited:''},open(e){this.setData({opened:e.currentTarget.dataset.id});},editOpportunity(e){this.setData({edited:e.currentTarget.dataset.opportunityId});}});`;
    SalesRuntime.route('/'+route);
  });
  await page.getByText('合成商机',{exact:true}).click();
  assert.equal(await page.evaluate(()=>SalesRuntime.current.data.opened),'op-synthetic');
  await page.evaluate(()=>SalesRuntime.current.setData({opened:''}));
  await page.getByRole('button',{name:'编辑商机',exact:true}).click();
  assert.deepEqual(await page.evaluate(()=>[SalesRuntime.current.data.edited,SalesRuntime.current.data.opened]),['op-synthetic','']);
  assert.equal(await page.getByText('¥0',{exact:true}).count(),1);
  assert.equal(await page.getByText('未填写',{exact:true}).count(),1);
}));
test('local named template renders once per use and receives explicit template data',()=>fixture(async page=>{
  await page.evaluate(()=>{
    const route='pages/web-template-test/index';
    const named=[{t:'text',a:{class:'template-result'},c:['{{label}}']}];
    SALES_BUNDLE.pages[route]={config:{},css:'',templates:{label:named},tree:[{t:'template',a:{name:'label'},c:named},{t:'template',a:{is:'label',data:'{{label: title}}'},c:[]}]};
    SALES_BUNDLE.modules[route]=`Page({data:{title:'只展示一次'}});`;
    SalesRuntime.route('/'+route);
  });
  assert.deepEqual(await page.locator('.template-result').allTextContents(),['只展示一次']);
}));
test('member Web popup escapes clipping; native root portal still retains its modal keyboard contract',()=>fixture(async page=>{
  await page.evaluate(()=>{
    const route='pages/web-portal-test/index';
    SALES_BUNDLE.pages[route]={config:{usingComponents:{'member-scope-filter':'/components/member-scope-filter/index'}},css:'',tree:[{t:'view',a:{style:'height:40px;overflow:hidden;transform:translateX(1px)'},c:[{t:'member-scope-filter',a:{id:'memberFilter',members:'{{members}}',selected:'{{selected}}',bindchange:'changed'},c:[]}]}]};
    SALES_BUNDLE.modules[route]=`Page({data:{members:[{id:'m1',name:'合成成员甲'},{id:'m2',name:'合成成员乙'}],selected:[],changes:0,lastIds:[]},changed(e){this.setData({changes:this.data.changes+1,lastIds:e.detail.ids});}});`;
    SalesRuntime.route('/'+route);
  });
  const trigger=page.locator('member-scope-filter button').first(),popup=page.locator('#web-select-dialog');
  await trigger.click();await page.waitForFunction(()=>document.querySelector('#web-select-input')?.tomselect?.isOpen);
  const overlay=await popup.evaluate(el=>{const rect=el.getBoundingClientRect();return {topLayer:el.matches(':popover-open'),modal:el.matches(':modal'),inBody:el.parentElement===document.body,height:rect.height,hit:el.contains(document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height-12))};});
  assert.equal(overlay.topLayer,true);assert.equal(overlay.modal,false);assert.equal(overlay.inBody,true);assert.ok(overlay.height>40);assert.equal(overlay.hit,true,'popup content is not clipped by the 40px transformed ancestor');
  await popup.locator('.option[data-value="m1"]').click();await popup.locator('.ts-control input').focus();
  await page.keyboard.press('Escape');await popup.waitFor({state:'detached'});
  assert.equal(await page.evaluate(()=>SalesRuntime.current.data.changes),0);
  assert.equal(await trigger.evaluate(el=>document.activeElement===el),true,'Escape restores the opening control');
  await trigger.click();await page.waitForFunction(()=>document.querySelector('#web-select-input')?.tomselect?.isOpen);
  await popup.locator('.option[data-value="m2"]').click();await popup.locator('#web-select-apply').click();
  assert.deepEqual(await page.evaluate(()=>[SalesRuntime.current.data.changes,SalesRuntime.current.data.lastIds]),[1,['m2']]);

  // Exercise the underlying host portal directly as a separate runtime contract.
  // The real Web click above intentionally uses the mature non-modal picker.
  await page.evaluate(()=>SalesRuntime.current.selectComponent('#memberFilter').toggle());
  const dialog=page.locator('dialog[data-wx-portal]');await dialog.waitFor();
  assert.equal(await dialog.evaluate(el=>el.matches(':modal')),true);
  await dialog.getByText('合成成员甲',{exact:true}).click();
  for(let i=0;i<8;i++){await page.keyboard.press('Tab');assert.equal(await dialog.evaluate(el=>el.contains(document.activeElement)),true);}
  await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});
  assert.equal(await page.evaluate(()=>SalesRuntime.current.data.changes),1,'native cancellation also discards the draft');
  await page.evaluate(()=>SalesRuntime.current.selectComponent('#memberFilter').toggle());
  await dialog.getByText('合成成员乙',{exact:true}).click();await dialog.locator('.member-apply').click();
  assert.equal(await page.evaluate(()=>SalesRuntime.current.data.changes),2);
}));
test('mature dashboard picker and native ranking sheet keep independent dimensions and dismissal behavior',()=>fixture(async page=>{
  await page.evaluate(()=>{
    const route='pages/web-sheet-test/index';
    SALES_BUNDLE.pages[route]={config:{usingComponents:{'dashboard-picker':'/components/dashboard-picker/index','dashboard-ranking':'/components/dashboard-ranking/index'}},css:'',tree:[{t:'dashboard-picker',a:{title:'筛选范围',options:'{{options}}',selected:'{{selected}}',bindchange:'changed'},c:[]},{t:'dashboard-ranking',a:{title:'合成排行',rows:'{{rows}}'},c:[]}]};
    SALES_BUNDLE.modules[route]=`Page({data:{options:[{id:'one',name:'合成成员甲'},{id:'two',name:'合成成员乙'}],selected:['one'],rows:Array.from({length:20},(_,i)=>({id:'rank-'+i,rank:i+1,name:'合成成员'+i,displayValue:String(i),width:'50%'}))},changed(e){this.setData({selected:e.detail.ids});}});`;
    SalesRuntime.route('/'+route);
  });
  const popup=page.locator('#web-select-dialog');
  await page.locator('dashboard-picker button').click();await page.waitForFunction(()=>document.querySelector('#web-select-input')?.tomselect?.isOpen);
  assert.equal(await popup.locator('.ts-dropdown-content').evaluate(el=>getComputedStyle(el).maxHeight),'240px','ranking styles must not overwrite the compact picker height');
  assert.equal(await page.locator('dashboard-picker .sheet-scroll').count(),0,'Web entry uses the mature picker rather than duplicating the source sheet');
  assert.equal(await popup.evaluate(el=>el.matches(':modal')),false);
  await popup.locator('.option[data-value="two"]').click();await popup.waitFor({state:'detached'});
  assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.selected),['two']);
  await page.locator('dashboard-ranking .rank-details').click();
  const sheet=page.locator('dashboard-ranking .sheet-layer');await sheet.waitFor();
  assert.equal(await sheet.locator('.sheet-scroll').evaluate(el=>getComputedStyle(el).height),'520px','ranking retains its own 52vh scroll area');
  assert.equal(await sheet.locator('.rank-row').count(),20);
  await sheet.locator('.sheet-handle-area').evaluate(el=>{
    const point=(y)=>new Touch({identifier:1,target:el,clientX:100,clientY:y});
    el.dispatchEvent(new TouchEvent('touchstart',{bubbles:true,touches:[point(100)]}));
    el.dispatchEvent(new TouchEvent('touchend',{bubbles:true,touches:[],changedTouches:[point(175)]}));
  });
  await sheet.waitFor({state:'detached'});
  await page.locator('dashboard-ranking .rank-details').click();await sheet.locator('.sheet-footer button').click();await sheet.waitFor({state:'detached'});
  await page.locator('dashboard-picker button').click();await popup.waitFor();
  assert.equal(await popup.locator('.ts-dropdown-content').evaluate(el=>getComputedStyle(el).maxHeight),'240px');
  await page.keyboard.press('Escape');await popup.waitFor({state:'detached'});
  assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.selected),['two']);
}));
test('opener event channel refreshes the original page and becomes inert after logout',()=>fixture(async page=>{
  await page.evaluate(()=>{
    const parent='pages/web-parent-test/index',child='pages/web-child-test/index';
    for(const route of [parent,child])SALES_BUNDLE.pages[route]={config:{},css:'',tree:[]};
    SALES_BUNDLE.modules[parent]=`Page({data:{saved:0},open(){wx.navigateTo({url:'/${child}',events:{demoSaved:()=>this.setData({saved:this.data.saved+1})},success:r=>{this.channel=r.eventChannel}});}});`;
    SALES_BUNDLE.modules[child]=`Page({save(){this.getOpenerEventChannel().emit('demoSaved');wx.navigateBack();}});`;
    SalesRuntime.route('/'+parent);window.__syntheticParent=SalesRuntime.current;SalesRuntime.current.open();
    SalesRuntime.current.save();
  });
  assert.equal(await page.evaluate(()=>SalesRuntime.current.data.saved),1);
  await page.evaluate(()=>{SalesRuntime.signOut();window.__syntheticParent.channel.emit('demoSaved');});
  assert.equal(await page.evaluate(()=>window.__syntheticParent.data.saved),1);
}));
