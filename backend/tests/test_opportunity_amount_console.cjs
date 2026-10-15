const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const assets = path.resolve(__dirname, '../src/sales_backend/web/assets');
const moduleAt = name => import(pathToFileURL(path.join(assets, name + '.js')).href);
const policy = threshold => ({definition:{schema_version:1,warning_threshold_wan:threshold}});
const row = {id:'op',customer_id:'c',customer_name:'合成客户',name:'合成商机',owner_user_ref_id:'self',
  amount:'10000',probability:10,status:'open',sales_channel:'direct',can_edit:true,quarterly_forecasts:[]};

async function harness({threshold=1000,confirmed=true,previous={},policyResponse}={}) {
  const core = await moduleAt('core'), pages = await moduleAt('pages');
  core.clearSession();Object.assign(core.state,{permissions:{'opportunity.create':true,'opportunity.update':true,
    'customer.claim_directory':true},actor:{workspace_id:'w',user_id:'self',role:'sales'},token:'fixture',
    filters:{},offset:0,refresh:async()=>{},page:'opportunities'});
  const calls=[], prompts=[], nodes={};
  const node = key => nodes[key] ||= {value:'',innerHTML:'',textContent:'',style:{},setAttribute(){},scrollIntoView(){},addEventListener(type,fn){this[type]=fn;}};
  node('[name=sales_channel]').value='direct';
  node('[name=amount_wan]').value=previous.id?String(Number(previous.amount)/10000):'';
  const form={values:[],isConnected:true,addEventListener(){},querySelector:s=>node(s)};
  let activeForm=form;
  const dialog={innerHTML:'',open:false,addEventListener(){},removeAttribute(){},setAttribute(){},querySelectorAll:()=>[],
    querySelector:s=>s==='#dialog-form'?activeForm:node(s),close(){this.open=false;},showModal(){this.open=true;}};
  global.document={querySelector:s=>s==='#dialog'?dialog:s==='#dialog-form'?activeForm:node(s),dispatchEvent(){}};
  global.FormData=class{constructor(f){this.values=f.values;}get(n){return this.values.find(([k])=>k===n)?.[1]??null;}has(n){return this.values.some(([k])=>k===n);}getAll(n){return this.values.filter(([k])=>k===n).map(([,v])=>v);}};
  global.window={confirm:message=>{prompts.push(message);return confirmed;}};
  global.fetch=async(url,options={})=>{
    calls.push({url,options});let result;
    if(url.endsWith('/amount-policy'))result=policyResponse?await policyResponse():policy(threshold);
    else if(options.method==='POST')result={id:'new',changed:true};
    else if(url.includes('/create-options'))result={default_owner_id:'self',owners:[{id:'self',name:'销售',default_team_id:'t',teams:[{id:'t',name:'合成团队'}]}]};
    else result={items:[{id:'c',name:'合成客户'}]};
    return{ok:true,status:200,json:async()=>result};
  };
  await pages.opportunityForm(previous);
  return {calls,prompts,nodes,dialog,replaceForm(){activeForm={};},
    async submit(wan){
      form.values=Object.entries({customer_id:'c',name:'合成商机',stage:'10',amount_wan:wan,
        expected_close_date:'2026-12-01',sales_channel:'direct',owner_user_ref_id:'self',owner_team_id:'t',forecast_quarter:''});
      await form.onsubmit({preventDefault(){},currentTarget:form});
    }, writes(){return calls.filter(c=>c.options.method==='POST').map(c=>JSON.parse(c.options.body));}};
}

test('whole yuan conversion is exact and preview exposes the factor of 10000',async()=>{
  const m=await moduleAt('opportunity-amount');
  assert.equal(m.yuanFromWan('100000'),'1000000000');
  assert.equal(m.yuanFromWan('1.2345'),'12345');
  assert.equal(m.yuanFromWan('0.0001'),'1');
  assert.equal(m.yuanFromWan('999999999999.9999'),'9999999999999999');
  assert.equal(m.wanFromYuan('9999999999999999'),'999999999999.9999');
  assert.equal(m.amountDisplay('1000000000'),'1,000,000,000 元（10 亿元）');
  assert.equal(m.amountDisplay('1200.01'),'1,200.01 元');
  assert.equal(m.amountDisplay(null),'金额待填写');
  for(const value of ['','NaN','Infinity','-1','1e3','1.0000001'])assert.throws(()=>m.yuanFromWan(value));
  const h=await harness();
  assert.match(h.dialog.innerHTML,/例如 10 = 10 万元/);
  assert.match(h.dialog.innerHTML,/step="0.000001"/);
  const input=h.nodes['[name=amount_wan]'];input.value='100000';input.input();
  assert.match(h.nodes['[data-amount-preview]'].textContent,/1,000,000,000 元（10 亿元）/);
});

test('yuan amounts with cents round-trip exactly and unchanged edits stay quiet',async()=>{
  const m=await moduleAt('opportunity-amount');
  for(const yuan of ['0.01','12345.67','10000000.01','9999999999999999.99']) {
    assert.equal(m.yuanFromWan(m.wanFromYuan(yuan)),yuan);
    const h=await harness({previous:{...row,amount:yuan},confirmed:false});
    await h.submit(m.wanFromYuan(yuan));
    assert.equal(h.writes()[0].amount,yuan);
    assert.equal(h.prompts.length,0);
    assert.equal(h.calls.filter(c=>c.url.endsWith('/amount-policy')).length,0);
  }
});

test('below threshold saves normally; equality requires exact amount acknowledgement',async()=>{
  let h=await harness();await h.submit('999.9999');assert.equal(h.prompts.length,0);
  assert.equal(h.writes()[0].amount,'9999999');assert(!('amount_confirmed_value' in h.writes()[0]));
  h=await harness();await h.submit('1000');assert.equal(h.prompts.length,1);
  assert.match(h.prompts[0],/10,000,000 元/);assert.equal(h.writes()[0].amount_confirmed_value,'10000000');
});

test('cancelling large amount leaves form intact and sends no mutation',async()=>{
  const h=await harness({confirmed:false});await h.submit('100000');
  assert.equal(h.writes().length,0);assert.equal(h.dialog.open,true);
  assert.match(h.nodes['.form-error'].innerHTML,/尚未确认大额金额/);
});

test('company threshold is read fresh on retry and cancellation cannot acknowledge a new amount',async()=>{
  let threshold=2000;
  const h=await harness({confirmed:false,policyResponse:()=>policy(threshold)});
  await h.submit('3000');assert.equal(h.writes().length,0);
  threshold=1000;await h.submit('1000');assert.equal(h.prompts.length,2);
  assert.match(h.prompts[1],/1,000 万元/);assert.equal(h.writes().length,0);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/amount-policy')).length,2);
});

test('unchanged historical large amount needs no confirmation or policy read',async()=>{
  const h=await harness({previous:{...row,amount:'1000000000'},confirmed:false});await h.submit('100000');
  assert.equal(h.writes().length,1);assert.equal(h.prompts.length,0);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/amount-policy')).length,0);
});

test('changed amount states before and after; malformed or unavailable policy cannot save',async()=>{
  let h=await harness({previous:{...row}});await h.submit('1000');
  assert.match(h.prompts[0],/原金额：10,000 元/);assert.equal(h.writes()[0].amount_confirmed_value,'10000000');
  for(const response of [()=>({}),()=>policy(0),()=>policy(1.5),()=>{throw Error('offline');}]){
    h=await harness({policyResponse:response});await h.submit('1000');
    assert.equal(h.writes().length,0);assert.equal(h.dialog.open,true);assert.equal(h.prompts.length,0);
  }
});

test('late policy result cannot confirm or save a replaced form',async()=>{
  let resolve;
  const h=await harness({policyResponse:()=>new Promise(r=>{resolve=r;})});
  const submit=h.submit('1000');for(let n=0;!resolve&&n<100;n++)await new Promise(r=>setImmediate(r));assert.ok(resolve,h.nodes['.form-error']?.innerHTML || 'amount policy request not reached');
  h.replaceForm();resolve(policy(1000));await submit;
  assert.equal(h.writes().length,0);assert.equal(h.prompts.length,0);
});
