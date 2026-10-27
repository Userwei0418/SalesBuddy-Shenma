import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';

const root = new URL('../', import.meta.url);
// Exercise the real browser module cache and scoped timers, not Node's ordinary
// require cache. Only the DOM, clock and directory transport are replaced.
const modules = Object.fromEntries(['access', 'opportunityAccess', 'opportunityFollowUpReference', 'visitOpportunityFollowUp']
  .map(name => [`utils/${name}`, fs.readFileSync(new URL(`source/miniprogram/utils/${name}.js`, root), 'utf8')]));
modules['pages/fixture/index'] = `Page(require('../../utils/visitOpportunityFollowUp').create(globalThis.fixtureApi,()=>getApp().globalData.session));`;
const runtime = fs.readFileSync(new URL('runtime.js', root), 'utf8').replace(/\}\)\(\);\s*$/, `
  globalThis.fixtureRuntime = {
    init(modules) {bundle={modules};app={globalData:{session:{workspaceId:'w',userId:'u',capabilities:{company_customer_follow_up:true}}}};},
    mount() {
      const scope=instanceDefinition('pages/fixture/index');
      const page={...scope.definition,_destroyed:false,data:{followUpPickerOpen:true,followUpPickerMode:'customers'},setData(values){Object.assign(this.data,values);}};
      scope.owner=page;return page;
    },
    unmount(page) {page.closed=page.unloaded=page._destroyed=true;clearOwnerTimers(page);}
  };
})();`);

test('automatic keyword search still returns matches after leaving and reopening the visit page', async () => {
  const timers = new Map(), requests = [], session = new Map();let serial = 0;
  const setTimer = callback => {const id=++serial;timers.set(id,callback);return id;};
  const clearTimer = id => timers.delete(id);
  const element = {addEventListener(){},style:{setProperty(){}}};
  const context = vm.createContext({console,clearTimeout:clearTimer,
    fixtureApi:{async listCompanyCustomerFollowUpOptions({q}) {
      requests.push(q);return {items:[{id:'00000001-1111-4111-8111-111111111111',name:q}],total:1,offset:0,limit:20};
    }},
    window:{addEventListener(){},setTimeout:setTimer,setInterval:setTimer,clearTimeout:clearTimer,clearInterval:clearTimer,matchMedia:()=>({matches:true,addEventListener(){}})},
    document:{getElementById:()=>element},sessionStorage:{getItem:key=>session.get(key),setItem:(key,value)=>session.set(key,value)},
  });
  vm.runInContext(runtime,context);
  const host=context.fixtureRuntime;host.init(modules);
  const flush=async()=>{for(const [id,callback] of [...timers]){timers.delete(id);callback();}for(let i=0;i<6;i++)await Promise.resolve();};
  const input=(page,value)=>page.inputFollowUpQuery({detail:{value,autoSearch:true}});
  const first=host.mount();input(first,'初次客户');await flush();
  assert.equal(first.data.followUpItems[0].name,'初次客户');
  input(first,'离开前');host.unmount(first);assert.equal(timers.size,0);
  const reopened=host.mount();input(reopened,'关键词');await flush();
  assert.deepEqual(requests,['初次客户','关键词']);
  assert.equal(reopened.data.followUpItems[0].name,'关键词');
  assert.equal(reopened.data.followUpSearchPending,false);
  assert.equal(reopened.data.followUpLoading,false);
  input(reopened,'手动搜索');await reopened.searchFollowUp();await flush();
  assert.deepEqual(requests,['初次客户','关键词','手动搜索'],'manual search cancels the scheduled duplicate');
});

test('the magnifier stays a normal manual search button while automatic search is pending or loading', () => {
  const source=transformSync(fs.readFileSync(new URL('department-ui/VisitFollowUpPicker.jsx',root),'utf8'),{loader:'jsx',format:'cjs'}).code;
  const module={exports:{}};
  vm.runInNewContext(source,{module,exports:module.exports,require:name=>name==='react'?React:new Proxy({},{get:(_,key)=>key})});
  const find=node=>React.isValidElement(node)?node.type==='SbSearch'?node:React.Children.toArray(node.props.children).map(find).find(Boolean):null;
  for(const state of [{followUpSearchPending:true},{followUpLoading:true}]) {
    const calls=[],tree=module.exports.default({data:{followUpPickerOpen:true,followUpPickerMode:'customers',...state},invoke:name=>calls.push(name)});
    const search=find(tree);
    assert.equal(Boolean(search.props.loading),false);
    assert.equal(search.props.disabled,false);
    search.props.onSearch();assert.deepEqual(calls,['searchFollowUp']);
  }
});
