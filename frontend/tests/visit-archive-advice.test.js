const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const tick=()=>new Promise(r=>setImmediate(r));
function setup({linked=false,storageFails=false,adviceFails=false,fde=false}={}){
 let page;let adviceCalls=0,archives=0;const urls=[];
 const file=path.resolve(__dirname,'../miniprogram/pages/visit-confirm/index.js');
 const api={createVisit:async()=>{archives++;return{id:'v1',opportunity_id:linked?'o1':null,fields:{}};},queryBusinessAdvice:async()=>{adviceCalls++;if(adviceFails)throw Error('网络异常');return{id:'a1',suggestions:[]};}};
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:p=>page=p,require:n=>n.endsWith('/apiClient')?api:require(path.resolve(path.dirname(file),n)),wx:{getStorageSync:()=>null,removeStorageSync:()=>{if(storageFails)throw Error('storage');},setNavigationBarTitle(){},navigateTo:o=>urls.push(o.url),showToast(){}}});
 page.data={...page.data,isFde:fde,customerId:'c1',canSubmit:true,reviewPayload:{fields:{}}};page.setData=o=>Object.assign(page.data,o);page.refresh=()=>{};page.fail=e=>{throw e;};
 return{page,urls,counts:()=>({adviceCalls,archives}),recover:()=>adviceFails=false};
}
for(const linked of [false,true])test(`销售归档关联商机=${linked}均推荐，清理异常不阻断详情`,async()=>{
 const h=setup({linked,storageFails:true});h.page.submitArchive();await tick();assert.equal(h.page.data.archived,true);assert.equal(h.counts().adviceCalls,1);assert.ok(h.page.data.archiveWarning);h.page.openVisit();assert.equal(h.urls[0],'/pages/visit-detail/index?customer_id=c1&visit_id=v1');
});
test('推荐失败可单独重试，不重复归档',async()=>{const h=setup({adviceFails:true});h.page.submitArchive();await tick();assert.match(h.page.data.adviceError,/记录已保存/);h.recover();await h.page.loadAdvice();assert.deepEqual(h.counts(),{adviceCalls:2,archives:1});assert.equal(h.page.data.adviceError,'');});
test('FDE保持不自动推荐，仍可打开本次详情',async()=>{const h=setup({fde:true,linked:true});h.page.submitArchive();await tick();assert.equal(h.counts().adviceCalls,0);h.page.openVisit();assert.equal(h.urls.length,1);});
