const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const tick = () => new Promise(setImmediate);
const assets = path.resolve(__dirname, '../../backend/src/sales_backend/web/assets');

async function harness() {
  const core = await import(pathToFileURL(path.join(assets, 'core.js')).href);
  const detail = await import(pathToFileURL(path.join(assets, 'customer-detail.js')).href);
  core.clearSession();
  Object.assign(core.state, {actor:{workspace_id:'w',user_id:'ops',role:'administrator'},token:'fixture-only',page:'customers'});
  const requests = [], regions = {};
  global.fetch = (url, options) => new Promise(resolve => requests.push({url, options, resolve, answered:false}));
  const d = {open:false, contents:'', regions, footer:{},
    set innerHTML(value) {
      this.contents = value;
      Object.values(regions).forEach(r => {r.isConnected = false;});
      for (const kind of ['ownership', 'claims']) {
        const body = {contents:'',button:{},set innerHTML(v){this.contents=v;},get innerHTML(){return this.contents;},
          querySelector(selector){return selector==='[data-history-more]' && this.contents.includes('data-history-more') ? this.button : null;}};
        regions[kind] = {isConnected:true,body,querySelector:()=>body};
      }
    },
    get innerHTML(){return this.contents;},
    querySelector(selector) {return selector==='[data-detail-opportunities]' ? this.footer : regions[selector.match(/data-customer-history="([^"]+)"/)?.[1]];},
    querySelectorAll(){return [];}, showModal(){this.open=true;}, close(){this.open=false;},
    contains(node){return Object.values(regions).includes(node);},
  };
  global.document = {querySelector:selector => selector==='#dialog' ? d : null};
  const pending = part => requests.find(r => !r.answered && r.url.includes(part));
  const answer = async (request, data, status=200) => {
    assert.ok(request); request.answered=true;
    request.resolve({ok:status<400,status,json:async()=>data}); await tick();
  };
  const open = id => detail.showCustomerDetail(id, value => { d.navigated = value; });
  return {core,requests,d,regions,pending,answer,open};
}
const overview = name => ({name,customer_type_code:'潜在客户',ownership:{state:'unclaimed'}});
const history = (prefix, count=20, next=20) => ({items:Array.from({length:count},(_,i)=>({id:`${prefix}-${i}`,event_type:'released',reason:prefix,operator:'运营',applicant_name:prefix,status:'approved'})),total:25,has_more:next!==null,next_offset:next});

test('operations detail paints overview before either independent history; next page is explicit', async () => {
  const h=await harness(), opened=h.open('customer-a');
  assert.match(h.requests[0].url,/customers\/customer-a\/overview$/);
  await h.answer(h.pending('/overview'),overview('客户概览')); await opened;
  assert.equal(h.d.open,true); assert.match(h.d.innerHTML,/客户概览/);
  assert.equal(h.requests.length,3);
  const own=h.pending('kind=ownership'), claims=h.pending('kind=claims');
  await h.answer(own,history('第一页'));
  assert.match(h.regions.ownership.body.innerHTML,/第一页/);
  assert.match(h.regions.claims.body.innerHTML,/正在加载/);
  await h.answer(claims,{detail:'审批历史暂不可用'},503);
  assert.match(h.regions.claims.body.innerHTML,/重新加载/);
  assert.match(h.regions.ownership.body.innerHTML,/第一页/);
  h.regions.ownership.body.button.onclick();
  assert.match(h.pending('kind=ownership').url,/offset=20/);
  await h.answer(h.pending('kind=ownership'),history('末页',5,null));
  assert.match(h.regions.ownership.body.innerHTML,/已加载 25 条/);
  assert.doesNotMatch(h.regions.ownership.body.innerHTML,/data-history-more/);
  h.regions.claims.body.button.onclick();
  assert.match(h.pending('kind=claims').url,/offset=0/);
  await h.answer(h.pending('kind=claims'),{items:[],total:0,has_more:false,next_offset:null});
  assert.match(h.regions.claims.body.innerHTML,/暂无历史记录/);
});

test('late overview from another actor or another selected customer cannot open a stale dialog', async () => {
  const h=await harness(), first=h.open('old');
  h.core.state.actor={workspace_id:'w',user_id:'other',role:'administrator'};
  await h.answer(h.pending('/old/overview'),overview('旧身份')); await first;
  assert.equal(h.d.open,false);
  const older=h.open('older'), latest=h.open('latest');
  await h.answer(h.pending('/latest/overview'),overview('当前客户')); await latest;
  await h.answer(h.pending('/older/overview'),overview('迟到客户')); await older;
  assert.match(h.d.innerHTML,/当前客户/); assert.doesNotMatch(h.d.innerHTML,/迟到客户/);
});

test('closed/replaced dialog drops history responses and leaves other dialog state intact', async () => {
  const h=await harness(), open=h.open('customer');
  await h.answer(h.pending('/overview'),overview('客户')); await open;
  const original=h.regions.ownership.body;
  h.d.close();
  await h.answer(h.pending('kind=ownership'),history('关闭后返回'));
  assert.doesNotMatch(original.innerHTML,/关闭后返回/);
  h.d.innerHTML='其他管理窗口'; h.d.open=true;
  await h.answer(h.pending('kind=claims'),history('<script>不能写入</script>'));
  assert.equal(h.d.innerHTML,'其他管理窗口');
});

test('a different management dialog opened while overview is pending is not replaced', async () => {
  const h=await harness(), open=h.open('slow');
  h.d.innerHTML='账号编辑窗口'; h.d.open=true;
  await h.answer(h.pending('/slow/overview'),overview('迟到客户')); await open;
  assert.equal(h.d.innerHTML,'账号编辑窗口');
  assert.equal(h.requests.length,1);
});
