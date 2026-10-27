const test=require('node:test'),assert=require('node:assert/strict');
const {createPageWriteContext}=require('../miniprogram/utils/pageWriteContext');
test('写入上下文锁定账号、公司与对象；旧finally不能解锁新操作',()=>{
  let session={userId:'one',workspaceId:'company',permissionVersion:'p1'};
  const writes=createPageWriteContext(()=>session),page={data:{id:'a',busy:true},setData(patch){Object.assign(this.data,patch);}};
  const old=writes.begin(page,'save',()=>page.data.id);
  assert.equal(writes.begin(page,'save',()=>page.data.id),null);assert.equal(old.start(),true);assert.equal(old.start(),false);
  session={...session,userId:'two'};const fresh=writes.begin(page,'save',()=>page.data.id);
  assert.equal(old.current(),false);old.finish('busy');assert.equal(page.data.busy,true);assert.equal(fresh.current(),true);
  page.data.id='b';assert.equal(fresh.current(),false);
});
test('切后台保持同账号在途写锁，禁止不可见页面的跳转，销毁后不能应用回执',()=>{
  const writes=createPageWriteContext(()=>({userId:'one',workspaceId:'company'})),page={data:{busy:true},setData(p){Object.assign(this.data,p);}};
  const write=writes.begin(page,'save',()=>1);page.writeHidden=true;
  assert.equal(write.current(),true);assert.equal(write.visible(),false);assert.equal(writes.begin(page,'save',()=>1),null);
  page.writeHidden=false;assert.equal(write.visible(),true);write.finish('busy');assert.equal(page.data.busy,false);
  assert.equal(write.current(),false);assert.equal(write.settledVisible(),true);page.unloaded=true;assert.equal(write.settledVisible(),false);
});
