import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Real runtime loader/timer/detach code and shared form/helper; only DOM mounting,
// elapsed time and the API transport are replaced. No browser or business writes.
const root = new URL('../', import.meta.url);
const modules = Object.fromEntries(fs.readdirSync(new URL('source/miniprogram/utils/', root))
  .filter(name => name.endsWith('.js')).map(name => [`utils/${name.slice(0, -3)}`, fs.readFileSync(new URL(`source/miniprogram/utils/${name}`, root), 'utf8')]));
modules['components/opportunity-form/index'] = fs.readFileSync(new URL('source/miniprogram/components/opportunity-form/index.js', root), 'utf8');
modules['utils/apiClient'] = 'globalThis.apiLoads++; module.exports=globalThis.fixtureApi;';
const runtime = fs.readFileSync(new URL('runtime.js', root), 'utf8').replace(/\}\)\(\);\s*$/, `
  globalThis.fixtureRuntime = {
    init(modules) { bundle={modules}; app={globalData:{session:{userId:'user-1',workspaceId:'workspace-1',role:'sales'}}}; current={route:'test-page'}; },
    mount(id) {
      const scope=instanceDefinition('components/opportunity-form/index');
      const component={...scope.definition.methods,_definition:scope.definition,_components:new Map(),_page:current,
        _destroyed:false,sourceKey:id,properties:{customerId:id,existing:null,disabled:false},
        data:{...clone(scope.definition.data),quarterOptions:[{year:2026,quarter:4}],quarterIndex:0,
          form:{trade_relation:'direct',material_lines:[{client_key:'line-'+id,quantity:'1',unit_price:'100'}]}},
        setData(values) { for(const [key,value] of Object.entries(values)){ const parts=key.split('.');let target=this.data;for(const part of parts.slice(0,-1))target=target[part];target[parts.at(-1)]=value;} }
      };
      scope.owner=component;return component;
    },
    detach, require:requireModule, get app(){return app;}
  };
})();`);

function harness() {
  const timers = new Map(), requests = [], session = new Map();let serial = 0;
  const setTimer = callback => {const id=++serial;timers.set(id,callback);return id;};
  const clearTimer = id => timers.delete(id);
  const element = {addEventListener(){},style:{setProperty(){}}};
  const context = vm.createContext({
    apiLoads:0, console, URL, URLSearchParams, clearTimeout:clearTimer,
    fixtureApi:{async previewMaterialDraft(request) {
      requests.push(request);
      return {status:'complete',persisted:false,reference_kind:'sales_forecast_draft_reference',draft_revision:request.draft_revision,
        period:{year:request.year,quarter:request.quarter},opportunity_id:null,opportunity_version:null,
        recognized_inclusive:'100.00',recognized_exclusive:'94.34',
        lines:request.material_lines.map(line=>({client_key:line.client_key,catalog_version_no:line.catalog_version_no,status:'complete',amount_inclusive:'100.00',amount_exclusive:'94.34'}))};
    }},
    window:{SALES_MODE:'live',addEventListener(){},dispatchEvent(){},setTimeout:setTimer,setInterval:setTimer,clearTimeout:clearTimer,clearInterval:clearTimer,
      matchMedia:()=>({matches:true,addEventListener(){}})},
    document:{getElementById:()=>element},
    sessionStorage:{getItem:key=>session.get(key),setItem:(key,value)=>session.set(key,value)},
  });
  vm.runInContext(runtime,context,{filename:'runtime.js'});
  const host=context.fixtureRuntime;host.init(modules);
  return {host,requests,timers,context,async flush() {
    for(const [id,callback] of [...timers]){timers.delete(id);callback();}
    for(let index=0;index<8;index++)await Promise.resolve();
  }};
}

test('closing and reopening the actual shared form rebinds automatic preview to the new component', async()=>{
  const h=harness();const first=h.host.mount('customer-1');
  first.scheduleMaterialPreview();await h.flush();
  assert.equal(h.requests.length,1);
  assert.equal(first.data.materialPreview.pending,false);
  first.scheduleMaterialPreview();h.host.detach(first);
  assert.equal(h.timers.size,0,'detach clears the old form timer');
  const reopened=h.host.mount('customer-1');reopened.scheduleMaterialPreview();
  assert.equal(reopened.data.materialPreview.pending,true);
  await h.flush();
  assert.equal(h.requests.length,2,'reopened form must send its automatic preview instead of remaining pending');
  assert.equal(reopened.data.materialPreview.pending,false);
  assert.equal(reopened.data.materialPreview.loading,false);
  assert.equal(reopened.data.materialPreview.error,'');
  assert.ok(reopened.data.materialPreview.result);
  reopened.data.form.material_lines[0].quantity='2';reopened.scheduleMaterialPreview();await h.flush();
  assert.equal(h.requests.at(-1).material_lines[0].quantity,'2');
});

test('detaching one form cannot cancel the automatic preview owned by another live form', async()=>{
  const h=harness();const first=h.host.mount('first'),second=h.host.mount('second');
  first.scheduleMaterialPreview();second.scheduleMaterialPreview();h.host.detach(first);
  await h.flush();
  assert.deepEqual(h.requests.map(request=>request.customer_id),['second']);
  assert.equal(second.data.materialPreview.pending,false);
});

test('per-form preview timers preserve the API singleton and application instance', async()=>{
  const h=harness(),app=h.host.app,api=h.host.require('utils/apiClient');
  const first=h.host.mount('first');h.host.detach(first);
  const second=h.host.mount('second');second.scheduleMaterialPreview();await h.flush();
  assert.equal(h.context.apiLoads,1,'scoping the form helper must not recreate API/auth state');
  assert.equal(h.host.require('utils/apiClient'),api);
  assert.equal(h.host.app,app);
});
