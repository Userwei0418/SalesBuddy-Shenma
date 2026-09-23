const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
function harness(){
 let page;const modals=[],routes=[],ticks=[],toasts=[];let logoutCount=0;
 const app={globalData:{session:{}},logout(){logoutCount++;this.globalData.session=null;return new Promise(()=>{});}};
 const wx={showModal:o=>modals.push(o),reLaunch:o=>routes.push(o),nextTick:fn=>ticks.push(fn),showToast:o=>toasts.push(o),showLoading(){},hideLoading(){}};
 const filename=path.resolve(__dirname,'../miniprogram/pages/profile/index.js');
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:p=>page=p,require:n=>n.endsWith('apiClient')?{}:require(path.resolve(path.dirname(filename),n)),getApp:()=>app,wx,setTimeout,clearTimeout});
 page.data=JSON.parse(JSON.stringify(page.data));page.setData=o=>Object.assign(page.data,o);
 return{page,modals,routes,ticks,toasts,app,get logoutCount(){return logoutCount;},flush(){while(ticks.length)ticks.shift()();}};
}
test('连续点退出只弹一次确认，取消后可再次点击',()=>{
 const h=harness();h.page.logout();h.page.logout();assert.equal(h.modals.length,1);
 h.modals[0].success({confirm:false});assert.equal(h.routes.length,0);assert.equal(h.logoutCount,0);
 h.page.logout();assert.equal(h.modals.length,2);
});
test('确认后下一帧只跳转一次，不等待退出网络请求，失败有反馈可重试',()=>{
 const h=harness();h.page.logout();h.modals[0].success({confirm:true});
 assert.equal(h.logoutCount,1);assert.equal(h.routes.length,0);h.page.logout();h.flush();assert.equal(h.routes.length,1);
 assert.equal(h.routes[0].url,'/pages/login/index');h.routes[0].fail({errMsg:'route failed'});
 assert.equal(h.page.data.logoutBusy,false);assert.ok(h.toasts.length>0);
 h.page.logout();h.flush();assert.equal(h.routes.length,2);assert.equal(h.logoutCount,1);assert.equal(h.modals.length,1);
});
test('确认弹窗失败后解除锁定并反馈，不把按钮永久禁用',()=>{
 const h=harness();h.page.logout();assert.equal(typeof h.modals[0].fail,'function');h.modals[0].fail({errMsg:'modal failed'});
 assert.equal(h.page.data.logoutBusy,false);assert.ok(h.toasts.length);h.page.logout();assert.equal(h.modals.length,2);
});
