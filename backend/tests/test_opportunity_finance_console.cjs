const test = require('node:test');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
const moduleAt = name => import(pathToFileURL(path.resolve(__dirname,'../src/sales_backend/web/assets',name+'.js')).href);
const id='11111111-1111-4111-8111-111111111111';
const result={opportunity_id:id,name:'Synthetic',can_confirm:true,quality:'待核对',conflict_period_count:1,
  review_path:`/admin?company=test&finance=${id}#opportunities`,coverage_note:'not zero',
  totals:{recognized:{amount:null,confirmed_subtotal:'20'},collection:{amount:null,confirmed_subtotal:null}},
  periods:[{year:2026,quarter:3,kind:'recognized',amount:null,entry_amount:'100',status:'needs_confirmation',
    source_fingerprint:'a'.repeat(64),entries:[],history:[{id,source_field:'<script>',source_record_id:id,
      raw_amount:'1',source_unit:'wan_cny',amount_yuan:'10000',tax_basis:'unknown'}],decision:null}]};

async function harness(url=result.review_path){
  const core=await moduleAt('core'), finance=await moduleAt('opportunity-finance');
  core.clearSession();Object.assign(core.state,{actor:{workspace_id:'w',user_id:'u',role:'operations'},token:'synthetic',
    permissions:{'actual.read':true,'actual.create':true},page:'opportunities',refresh:async()=>{}});
  const calls=[], nodes={};let html='',form;
  const node=k=>nodes[k]||=( {textContent:'',innerHTML:'',setAttribute(){},scrollIntoView(){},addEventListener(){}} );
  const periodButton={dataset:{financePeriod:'0'}};
  const dialog={open:false,get innerHTML(){return html;},set innerHTML(v){html=v;form={values:[],addEventListener(){},querySelector:s=>node(s)};},
    removeAttribute(){},setAttribute(){},querySelector:s=>node(s),querySelectorAll:s=>s==='[data-finance-period]'?[periodButton]:[],
    close(){this.open=false;},showModal(){this.open=true;}};
  global.document={querySelector:s=>s==='#dialog'?dialog:s==='#dialog-form'?form:node(s),dispatchEvent(){}};
  global.location=new URL(url,'https://test.invalid');
  global.FormData=class{constructor(f){this.values=f.values;}get(k){return this.values.find(v=>v[0]===k)?.[1]??null;}};
  global.fetch=async(url,options={})=>{calls.push({url,options});return {ok:true,status:200,json:async()=>result};};
  return {core,finance,dialog,calls,periodButton,async submit(){
    const current=form;current.values=[['selected','history:'+id],['reason','verified original evidence']];
    await current.onsubmit({preventDefault(){},currentTarget:current});
  }};
}

test('unknown totals remain unknown and evidence text is escaped',async()=>{
  const f=await moduleAt('opportunity-finance');const html=f.financeBody(result);
  assert.equal(f.yuan('9999999999999999.99'),'9,999,999,999,999,999.99 元');
  assert.equal(f.yuan('0.005'),'0.01 元');
  assert.match(html,/未登记 \/ 待核对/);assert.match(html,/可计入小计/);
  assert.match(html,/&lt;script&gt;/);assert(!html.includes('<script>'));
});

test('copyable finance query opens authorized detail after login',async()=>{
  const h=await harness();await h.finance.openFinanceDeepLink();
  assert.equal(h.calls.length,1);assert(h.calls[0].url.endsWith(`/opportunities/${id}/financial-overview`));
  assert(h.dialog.open);assert.match(h.dialog.innerHTML,/财务核对/);
});

test('invalid deep link and denied read cannot start a fetch',async()=>{
  let h=await harness('/admin?finance=bad#opportunities');await h.finance.openFinanceDeepLink();assert.equal(h.calls.length,0);
  h=await harness();h.core.state.permissions['actual.read']=false;
  await h.finance.openFinanceDeepLink();assert.equal(h.calls.length,0);
});

test('source submit sends only explicit evidence choice with version fingerprint and key',async()=>{
  const h=await harness();await h.finance.openFinanceDeepLink();h.periodButton.onclick();await h.submit();
  const write=h.calls.find(c=>c.options.method==='POST');assert(write);
  const body=JSON.parse(write.options.body);assert.equal(body.selected_history_id,id);
  assert.equal(body.source,'historical');assert.equal(body.expected_version,0);
  assert.equal(body.expected_fingerprint,'a'.repeat(64));assert(!('amount' in body));assert(!('workspace_id' in body));
  assert(write.options.headers['Idempotency-Key']);
});
