const test = require('node:test');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
const moduleAt = name => import(pathToFileURL(path.resolve(__dirname,'../src/sales_backend/web/assets',name+'.js')).href);
const requestId='11111111-1111-4111-8111-111111111111';
const custom={code:'custom_revenue_count',name:'合同积分',unit:'积分',decimal_places:2,active:true,goal_selectable:true,version_no:3,definition:'按已确认合同累计积分'};
const baseRow={id:requestId,applicant_name:'测试',account_code:'TEST',period_start:'2026-10-01',period_end:'2026-12-31',status:'pending',reason:'原申请',
  items:[{kind:custom.code,previous_amount:1,previous_amount_text:'1.20',proposed_amount:10000000000000000,proposed_amount_text:'9999999999999999.99',base_version:7}]};
const legacy=[['collection','回款','万元',2],['recognized','确收','万元',2],['acv','协助项目金额 / ACV','万元',2],
  ['opportunity_count','商机数量','个',0],['visit_count','拜访记录数','次',0],['demo_count','Demo 场景数','次',0]];
const turn=()=>new Promise(resolve=>setImmediate(resolve));

async function harness({rows=[structuredClone(baseRow)],metrics=[custom],format='batch'}={}) {
  const core=await moduleAt('core'), targets=await moduleAt('targets');
  core.clearSession();
  Object.assign(core.state,{actor:{workspace_id:'original-company',user_id:'user',role:'administrator'},token:'synthetic',
    company:{id:'selected-company'},permissions:{'target.approve':true,'target.manage':true,'target.history':true},
    page:'target_requests',filters:{status:'pending',format},offset:0,refresh:async()=>{}});
  const calls=[],nodes={};let html='',form;
  const node=key=>nodes[key]||=( {value:'',textContent:'',innerHTML:'',isConnected:true,required:false,
    style:{},setAttribute(){},removeAttribute(){},scrollIntoView(){},addEventListener(){},querySelector:s=>node(s)} );
  const dialog={open:false,get innerHTML(){return html;},set innerHTML(value){
    html=value;form={values:[],isConnected:true,addEventListener(){},querySelector:s=>node(s)};
    for(const [key] of Object.entries(nodes))delete nodes[key];
    for(const m of value.matchAll(/<select name="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)){
      const opts=[...m[2].matchAll(/<option value="([^"]*)"([^>]*)>/g)];
      node(`[name=${m[1]}]`).value=(opts.find(o=>o[2].includes('selected'))||opts[0])?.[1]||'';
    }
    for(const m of value.matchAll(/<input[^>]* name="([^"]+)"[^>]* value="([^"]*)"/g))node(`[name=${m[1]}]`).value=m[2];
  },removeAttribute(){},setAttribute(){},querySelector:s=>s==='form'?form:node(s),querySelectorAll:()=>[],
  close(){this.open=false;},showModal(){this.open=true;}};
  const content={firstElementChild:{},querySelector:()=>null};
  global.document={querySelector:s=>s==='#dialog'?dialog:s==='#dialog-form'?form:s==='#content'?content:node(s),dispatchEvent(){}};
  global.FormData=class {constructor(f){this.values=f.values;}get(k){return this.values.find(v=>v[0]===k)?.[1]??null;}};
  const h={core,targets,dialog,calls,nodes,rows,metrics,metricError:null,metricWait:null,targetWait:null,targetError:null,mutationError:null,
    editable:true,subjects:{people:[{id:'person',name:'测试',account_code:'TEST',can_manage:true}],teams:[],departments:[]}};
  global.fetch=async(url,options={})=>{
    calls.push({url,options});
    if(['POST','PATCH'].includes(options.method)){
      if(h.mutationError)return {ok:false,status:409,json:async()=>({code:'VERSION_CONFLICT',detail:h.mutationError})};
      return {ok:true,status:200,json:async()=>({status:'approved'})};
    }
    if(url==='/api/v1/review/metrics'){
      if(h.metricWait)await h.metricWait;
      if(h.metricError)return {ok:false,status:403,json:async()=>({detail:h.metricError})};
      return {ok:true,status:200,json:async()=>({items:h.metrics})};
    }
    if(url.endsWith('/targets/options'))return {ok:true,status:200,json:async()=>h.subjects};
    if(url.startsWith('/api/v1/review/targets')){
      if(h.targetWait)await h.targetWait;
      if(h.targetError)return {ok:false,status:403,json:async()=>({detail:h.targetError})};
    }
    return {ok:true,status:200,json:async()=>({items:h.rows,total:h.rows.length,editable:h.editable,period:{start:'2026-10-01',end:'2026-12-31'}})};
  };
  h.render=async()=>{h.page=await targets.targetRequests();h.page.bind(content);return h.page.html;};
  h.review=async(id=requestId)=>content.onclick({target:{closest:()=>({dataset:{action:'review',id}})}});
  h.submit=async(decision='approved',reason='已核对')=>{
    form.values=[['decision',decision],['reason',reason]];
    await form.onsubmit({preventDefault(){},currentTarget:form});
  };
  h.posts=()=>calls.filter(c=>c.options.method==='POST');
  h.writes=()=>calls.filter(c=>['POST','PATCH'].includes(c.options.method));
  h.action=async(action,id,root=content)=>root.onclick({target:{closest:()=>({dataset:{action,id}})}});
  h.formAction=async(action,id)=>h.action(action,id,document.querySelector('#dialog-form'));
  h.fill=async(values)=>{form.values=Object.entries(values);await form.onsubmit({preventDefault(){},currentTarget:form});};
  h.targetPage=async()=>{core.state.page='targets';core.state.filters={};h.page=await targets.targets();h.page.bind(content);return h.page.html;};
  h.choose=async(name,value)=>{node(`[name=${name}]`).value=value;await node(`[name=${name}]`).onchange?.();};
  return h;
}

test('same-company dictionary supplies custom name/unit/precision; exact text wins over rounded JSON numbers',async()=>{
  const h=await harness();const original=structuredClone(h.rows);
  const html=await h.render();assert.match(html,/合同积分/);assert.match(html,/1\.20 积分/);assert.match(html,/9,999,999,999,999,999\.99 积分/);
  assert(!html.includes('10,000,000,000,000,000'));assert(!html.includes('999.99 个'));
  const dictionary=h.calls.find(c=>c.url==='/api/v1/review/metrics');
  assert.equal(dictionary.options.headers.Authorization,'Bearer synthetic');assert.equal(dictionary.options.headers['X-Company-ID'],'selected-company');
  await h.review();assert.match(h.dialog.innerHTML,/9,999,999,999,999,999\.99 积分/);
  await h.submit();const post=h.posts()[0];assert(post.url.endsWith(`/target-batches/${requestId}/decision`));
  assert.deepEqual(JSON.parse(post.options.body),{decision:'approved',reason:'已核对'});assert(post.options.headers['Idempotency-Key']);
  assert.deepEqual(h.rows,original); // Includes the original request ID and base_version.
});

test('custom amount suffix with integer precision uses its dictionary unit; zero and null differ',async()=>{
  const row={...baseRow,items:[{kind:'custom_amount',previous_amount_text:null,previous_amount:100,proposed_amount_text:'0.00',proposed_amount:999}]};
  const h=await harness({rows:[row],metrics:[{...custom,code:'custom_amount',name:'完成项',unit:'项',decimal_places:0}]});
  const html=await h.render();assert.match(html,/未设置 → 0 项/);assert(!html.includes('元'));
});

test('inactive complete metadata is identified; approval eligibility remains with the server',async()=>{
  const h=await harness({metrics:[{...custom,active:false}]});await h.render();await h.review();
  assert.match(h.dialog.innerHTML,/合同积分（已停用）/);assert.match(h.dialog.innerHTML,/value="approved"/);
  await h.submit();assert.equal(h.posts().length,1);
});

test('dictionary denied aborts target approval and never falls back across authorization failures',async()=>{
  const h=await harness();h.metricError='没有此公司的指标读取权限';
  await assert.rejects(h.render(),error=>error.statusCode===403);assert(!h.dialog.open);assert.equal(h.posts().length,0);
});

test('missing/invalid custom metadata remains identifiable; one unknown item prevents batch approval only',async()=>{
  for(const metrics of [[],[{...custom,name:' '}],[{...custom,unit:' '}],[{...custom,decimal_places:3}]]){
    const h=await harness({metrics});await h.render();await h.review();
    assert(!h.dialog.innerHTML.includes('value="approved"'));assert.match(h.dialog.innerHTML,/驳回申请/);
    assert.match(h.dialog.innerHTML,/合同积分|custom_revenue_count/);
  }
  const h=await harness({rows:[{...baseRow,items:[...baseRow.items,{kind:'custom_missing',proposed_amount:'12.50'}]}]});
  await h.render();await h.review();assert(!h.dialog.innerHTML.includes('value="approved"'));
});

test('all six legacy kinds and legacy single-request route work without dictionary or new text fields',async()=>{
  for(const [kind,name,unit,precision] of legacy){
    const amount=precision?(kind==='collection'||kind==='recognized'||kind==='acv'?'12345000.00':'1234.5'):1234;
    const h=await harness({format:'legacy',rows:[{...baseRow,items:undefined,kind,previous_amount:null,proposed_amount:amount}]});
    const html=await h.render();assert(html.includes(name));assert(html.includes((precision?'1,234.50':'1,234')+' '+unit));
    await h.review();assert.match(h.dialog.innerHTML,/value="approved"/);await h.submit();
    assert(h.posts()[0].url.endsWith(`/target-requests/${requestId}/decision`));
  }
});

test('dialog rereads dictionary; names and units are escaped',async()=>{
  const h=await harness({metrics:[{...custom,name:'<img onerror=x>',unit:'<b>分</b>'}]});
  const html=await h.render();assert.match(html,/&lt;img onerror=x&gt;/);assert.match(html,/&lt;b&gt;分&lt;\/b&gt;/);
  h.metrics=[];await h.review();assert(!h.dialog.innerHTML.includes('value="approved"'));assert.match(h.dialog.innerHTML,/未知指标/);
});

test('company switch A→B→A while dictionary loads never falls back to old-company data',async()=>{
  const h=await harness();let finish;h.metricWait=new Promise(resolve=>{finish=resolve;});
  const pending=h.render();await turn();h.core.setCompany({id:'other-company'});h.core.setCompany({id:'selected-company'});finish();
  await assert.rejects(pending,error=>error.code==='COMPANY_CHANGED');assert(!h.dialog.open);assert.equal(h.posts().length,0);
});

test('identity/page changes discard loading and permission revocation blocks review submission',async()=>{
  for(const change of [h=>{h.core.state.actor={...h.core.state.actor,user_id:'other'};},h=>{h.core.state.page='customers';}]){
    const h=await harness();let finish;h.metricWait=new Promise(resolve=>{finish=resolve;});
    const pending=h.render();await turn();change(h);finish();await assert.rejects(pending,/已变化/);assert.equal(h.posts().length,0);
  }
  const h=await harness();await h.render();await h.review();h.core.state.permissions['target.approve']=false;
  await h.submit();assert.equal(h.posts().length,0);assert.match(h.nodes['.form-error'].innerHTML,/未获/);
});

test('console list reads Review API, exposes custom edit and keeps effective and pending values separate',async()=>{
  const row={...baseRow,kind:custom.code,amount_text:'123.45',amount:999,scope_type:'person',period_type:'quarter',user_name:'测试',can_manage:true,editable:true,pending_change:{proposed_amount:'456.78'}};
  const h=await harness({rows:[row]});h.core.state.page='targets';h.core.state.filters={};
  const view=await h.targets.targets();assert.match(view.html,/合同积分/);assert.match(view.html,/123\.45 积分/);
  assert.match(view.html,/data-action="edit"/);assert.match(view.html,/data-action="metrics"/);assert.match(view.html,/data-action="create-custom"/);
  assert.match(view.html,/456\.78 积分/);assert.match(view.html,/待审批，不计入生效目标/);assert(!view.html.includes('工作报告的目标页调整'));
  assert(h.calls.some(c=>c.url.startsWith('/api/v1/review/targets?')));assert(!h.calls.some(c=>c.url.startsWith('/api/v1/console/targets')));
  row.amount_text=null;const empty=await h.targets.targets();assert.match(empty.html,/未设置/);assert(!empty.html.includes('999.00 积分'));
});

test('legacy edit retains original kind/version and batch API for all six kinds',async()=>{
  for(const [kind,,unit,precision] of legacy){
    const money=['collection','recognized','acv'].includes(kind);
    const row={...baseRow,items:undefined,kind,scope_type:'person',user_ref_id:'person',user_name:'测试',period_type:'quarter',can_manage:true,version_no:7,amount_text:precision?(money?'1002000.00':'100.20'):'100'};
    const h=await harness({rows:[row]});h.core.state.page='targets';
    await h.targets.editTarget(row);await turn();
    assert(h.nodes['#target-values'].innerHTML.includes('（'+unit+'）'));assert(h.nodes['#target-values'].innerHTML.includes('step="'+(precision?'0.000001':'1')+'"'));
    assert(h.nodes['#target-values'].innerHTML.includes('当前生效：'+(precision?'100.20':'100')+' '+unit));
    // The original finance mode groups collection/recognized; other kinds stay independent.
    const mode=['collection','recognized'].includes(kind)?'financial':kind;
    const form=document.querySelector('#dialog-form');form.values=[['mode',mode],['reason','调整依据'],['amount_'+kind,precision?'200.30':'200']];
    if(mode==='financial')form.values.push(['amount_'+(kind==='collection'?'recognized':'collection'),'300.40']);
    await form.onsubmit({preventDefault(){},currentTarget:form});
    const post=h.posts()[0];assert(post,'legacy edit submits');assert(post.url.endsWith('/targets/batch'));
    const body=JSON.parse(post.options.body);assert.equal(body.items.find(i=>i.kind===kind).version_no,7);assert.equal(body.scope,'person');assert.equal(body.user_id,'person');
    assert.equal(body.items.find(i=>i.kind===kind).amount,money?'2003000':precision?'200.30':'200');
  }
});

test('inexact old JSON numbers or out-of-contract precision are shown for review without rounding into approval',async()=>{
  for(const value of [10000000000000000,'1.234']){
    const row={...baseRow,items:[{kind:custom.code,previous_amount:null,proposed_amount:value}]};
    const h=await harness({rows:[row]});const html=await h.render();assert.match(html,/数值精度待核对/);
    await h.review();assert(!h.dialog.innerHTML.includes('value="approved"'));assert.match(h.dialog.innerHTML,/驳回申请/);
  }
});

test('actual V240 PostgreSQL/ASGI console samples retain distinct maximum cents in list/dialog and original decision IDs',async()=>{
  const fixture=require('./fixtures/target_metric_console.synthetic.json').response_samples;
  for(const [format,key] of [['batch','console_batches'],['legacy','console_requests']]){
    const rows=structuredClone(fixture[key].items), original=structuredClone(rows);
    const h=await harness({format,rows,metrics:[fixture.custom_dictionary_entry]});
    const html=await h.render();assert.match(html,/999,999,999,999\.999999 万元/);assert.match(html,/999,999,999,999\.999998 万元/);
    assert.match(html,/合成转化计数/);assert(html.includes(format==='batch'?'12.30 次':'9.70 次'));
    for(const row of rows.filter(r=>r.status==='pending')){
      await h.review(row.id);assert.match(h.dialog.innerHTML,/value="approved"/);
      const items=row.items||[row];
      if(items.some(i=>i.kind==='collection')){
        assert.match(h.dialog.innerHTML,/999,999,999,999\.999999 万元/);assert.match(h.dialog.innerHTML,/999,999,999,999\.999998 万元/);
      }
      if(items.some(i=>i.kind===fixture.custom_dictionary_entry.code))assert(h.dialog.innerHTML.includes(format==='batch'?'12.30 次':'9.70 次'));
      await h.submit();const post=h.posts().at(-1);assert(post.url.endsWith(`/${format==='batch'?'target-batches':'target-requests'}/${row.id}/decision`));
      assert.deepEqual(JSON.parse(post.options.body),{decision:'approved',reason:'已核对'});
    }
    assert.deepEqual(rows,original);
  }
});

const effectiveTarget=()=>({id:requestId,row_key:'selected-company:quarter:person:custom',kind:custom.code,
  scope_type:'person',user_ref_id:'person',team_id:null,department_code:'sales',user_name:'测试',account_code:'TEST',
  period_type:'quarter',period_start:'2026-10-01',period_end:'2026-12-31',amount_text:'9999999999999999.98',
  version_no:7,can_manage:true,editable:true,can_history:true,pending_change:{proposed_amount:'9999999999999999.99'}});
const metricForm={name:'  合同完成项  ',unit:' 项 ',decimal_places:'0',definition:'  已验收合同去重计数  '};

test('management DOM creates a custom metric with a stable code/key and same-company console session',async()=>{
  const h=await harness({rows:[]});await h.targetPage();await h.action('metrics');
  assert.match(h.dialog.innerHTML,/新增指标/);assert.match(h.dialog.innerHTML,/按已确认合同累计积分/);
  await h.formAction('new-metric');assert.match(h.dialog.innerHTML,/创建后口径固定/);
  h.mutationError='版本冲突，请重新打开';await h.fill(metricForm);
  assert(h.dialog.open);assert.match(h.nodes['.form-error'].innerHTML,/记录已被更新/);
  h.mutationError=null;await h.fill(metricForm);
  const [first,second]=h.writes();assert.equal(first.url,'/api/v1/review/metrics');
  assert.equal(first.options.body,second.options.body);assert.equal(first.options.headers['Idempotency-Key'],second.options.headers['Idempotency-Key']);
  const body=JSON.parse(first.options.body);assert.match(body.code,/^custom_[a-z0-9_]{1,57}$/);
  assert.deepEqual({...body,code:'generated'},{code:'generated',active:true,name:'合同完成项',unit:'项',decimal_places:0,definition:'已验收合同去重计数'});
  assert.equal(first.options.headers.Authorization,'Bearer synthetic');assert.equal(first.options.headers['X-Company-ID'],'selected-company');assert(!h.dialog.open);
});

test('invalid metric input and revoked management permission never write',async()=>{
  const h=await harness({rows:[]});await h.targetPage();await h.action('metrics');await h.formAction('new-metric');
  for(const bad of [{name:' '},{unit:'x'.repeat(21)},{definition:''},{decimal_places:'1'}]){
    await h.fill({...metricForm,...bad});assert.equal(h.writes().length,0);assert(h.nodes['.form-error'].innerHTML);
  }
  h.core.state.permissions['target.manage']=false;await h.fill(metricForm);assert.equal(h.writes().length,0);
});

test('metric toggle rereads current version, preserves conflict, and supports re-enabling',async()=>{
  for(const active of [true,false]){
    const h=await harness({metrics:[{...custom,active}]});await h.targets.manageMetrics();
    h.metrics=[{...custom,active,version_no:9}];await h.formAction('toggle-metric',custom.code);
    assert.match(h.dialog.innerHTML,active?/历史记录保留/:/继续配置/);
    h.mutationError='版本冲突';await h.fill({});assert(h.dialog.open);assert.match(h.nodes['.form-error'].innerHTML,/记录已被更新/);
    assert.equal(h.writes().length,1);const patch=h.writes()[0];assert.equal(patch.options.method,'PATCH');
    assert.equal(patch.url,'/api/v1/review/metrics/'+custom.code);assert.deepEqual(JSON.parse(patch.options.body),{active:!active,version_no:9});
    assert(patch.options.headers['Idempotency-Key']);
    h.mutationError=null;await h.fill({});assert.equal(h.writes()[1].options.body,patch.options.body);
    assert.equal(h.writes()[1].options.headers['Idempotency-Key'],patch.options.headers['Idempotency-Key']);
  }
});

test('dictionary errors and stale modal reads do not open a mutable management form',async()=>{
  const h=await harness();h.metricError='无权读取';await assert.rejects(h.targets.manageMetrics(),error=>error.statusCode===403);assert(!h.dialog.open);
  h.metricError=null;let finish;h.metricWait=new Promise(resolve=>{finish=resolve;});
  const loading=h.targets.manageMetrics();await turn();h.core.state.page='other';finish();await loading;
  assert(!h.dialog.open);assert.equal(h.writes().length,0);
});

test('custom adjustment uses fresh target version and exact cents; never sends a legacy batch',async()=>{
  const row=effectiveTarget(), h=await harness({rows:[row]});await h.targetPage();
  h.rows=[{...row,version_no:8}];await h.action('edit',row.id);await turn();
  assert.equal(h.nodes['[name=amount]'].value,'9999999999999999.98');
  assert.match(h.nodes['#review-target-note'].textContent,/9,999,999,999,999,999\.99 积分/);
  assert.match(h.nodes['#review-target-note'].textContent,/关闭对应整张申请/);
  await h.fill({amount:'9999999999999999.99',reason:' 已商定调整 '});
  const post=h.writes()[0];assert.equal(post.url,'/api/v1/review/targets');
  assert.deepEqual(JSON.parse(post.options.body),{scope:'person',user_id:'person',team_id:null,department_code:'sales',period_type:'quarter',anchor_date:'2026-10-01',kind:custom.code,amount:'9999999999999999.99',reason:'已商定调整',version_no:8});
  assert.equal(post.options.headers['X-Company-ID'],'selected-company');assert(post.options.headers['Idempotency-Key']);
});

test('pending-new custom target uses stable row key and null version without treating proposal as effective',async()=>{
  const row={...effectiveTarget(),id:null,version_no:null,amount_text:null,state:'pending_new',can_history:false};
  const h=await harness({rows:[row]});const html=await h.targetPage();assert.match(html,/尚未生效/);assert(!html.includes('data-action="history"'));
  await h.action('edit',row.row_key);await turn();assert.equal(h.nodes['[name=amount]'].value,'');
  assert.match(h.nodes['#review-target-note'].textContent,/当前生效：未设置/);
  await h.fill({amount:'10.20',reason:'确认目标'});assert.equal(JSON.parse(h.writes()[0].options.body).version_no,null);
});

test('new custom target follows authorized people, team and department options only',async()=>{
  for(const [scope,key,id] of [['person','people','person'],['team','teams','team'],['department','departments','fde']]){
    const h=await harness({rows:[]});h.subjects={people:[],teams:[],departments:[],[key]:[{id,name:'测试对象',can_manage:true},{id:'denied',name:'无维护权限',can_manage:false}]};
    await h.targetPage();await h.action('create-custom');
    assert(!h.dialog.innerHTML.includes('无维护权限'));
    if(scope!=='department')await h.choose(scope==='person'?'user_id':'team_id',id);else await turn();
    await h.fill({amount:'12.30',reason:'新目标'});const body=JSON.parse(h.writes()[0].options.body);
    assert.equal(body.scope,scope);assert.equal(body.version_no,null);assert.equal(body.user_id,scope==='person'?id:null);
    assert.equal(body.team_id,scope==='team'?id:null);assert.equal(body.department_code,scope==='department'?id:'sales');
  }
});

test('custom integer precision comes from dictionary; invalid amount and blank reason block submission',async()=>{
  const row={...effectiveTarget(),amount_text:'12.00'};
  const h=await harness({rows:[row],metrics:[{...custom,decimal_places:0}]});await h.targets.editReviewTarget(row);await turn();
  for(const amount of ['1.20','0','-1','1e3','1.234','10000000000000000','NaN']){
    await h.fill({amount,reason:'依据'});assert.equal(h.writes().length,0);assert.match(h.nodes['.form-error'].innerHTML,/目标值/);
  }
  await h.fill({amount:'12',reason:' '});assert.equal(h.writes().length,0);
  await h.fill({amount:'9999999999999999',reason:'依据'});assert.equal(JSON.parse(h.writes()[0].options.body).amount,'9999999999999999');
});

test('fresh object access, active metric, selectable flag, and exact response identity are enforced',async()=>{
  for(const change of [h=>{h.rows[0].can_manage=false;},h=>{h.rows[0].editable=false;},h=>{h.editable=false;},
    h=>{h.rows[0].version_no=null;},h=>{h.rows[0].user_ref_id='different';},h=>{h.rows[0].amount_text=10000000000000000;},
    h=>{h.targetError='无权访问';}]){
    const row=effectiveTarget(),h=await harness({rows:[structuredClone(row)]});change(h);
    await h.targets.editReviewTarget(row);await turn();assert.equal(h.nodes['button[type=submit]'].disabled,true);
    await h.fill({amount:'100',reason:'依据'});assert.equal(h.writes().length,0);
  }
  for(const patch of [{active:false},{goal_selectable:false},{unit:''},{decimal_places:3}]){
    const h=await harness({rows:[effectiveTarget()],metrics:[{...custom,...patch}]});
    await assert.rejects(h.targets.editReviewTarget(effectiveTarget()),/自定义指标/);assert(!h.dialog.open);
  }
});

test('switching company A→B→A or navigating away invalidates target load and form writes',async()=>{
  for(const change of [h=>{h.core.setCompany({id:'other'});h.core.setCompany({id:'selected-company'});},h=>{h.core.state.page='other';}]){
    const h=await harness({rows:[effectiveTarget()]});let finish;h.targetWait=new Promise(resolve=>{finish=resolve;});
    await h.targets.editReviewTarget(effectiveTarget());await turn();change(h);finish();await turn();
    assert.equal(h.nodes['[name=amount]'].value,'');await h.fill({amount:'100',reason:'依据'});assert.equal(h.writes().length,0);
  }
});

test('changing selection discards an earlier target read and requires the matching fresh version',async()=>{
  const h=await harness({rows:[]});h.subjects.people.push({id:'other',name:'另一人',can_manage:true});
  await h.targets.editReviewTarget();let finish;h.targetWait=new Promise(resolve=>{finish=resolve;});
  const old=h.choose('user_id','person');await turn();h.targetWait=null;await h.choose('user_id','other');finish();await old;
  await h.fill({amount:'123.45',reason:'依据'});assert.equal(JSON.parse(h.writes()[0].options.body).user_id,'other');
});

test('custom conflict keeps form and idempotency key; revoked permission blocks retry',async()=>{
  const h=await harness({rows:[effectiveTarget()]});await h.targets.editReviewTarget(effectiveTarget());await turn();
  h.mutationError='目标版本已变化';await h.fill({amount:'100.20',reason:'依据'});
  assert(h.dialog.open);assert.match(h.nodes['.form-error'].innerHTML,/记录已被更新/);
  await h.fill({amount:'100.20',reason:'依据'});assert.equal(h.writes()[0].options.headers['Idempotency-Key'],h.writes()[1].options.headers['Idempotency-Key']);
  assert.equal(JSON.parse(h.writes()[1].options.body).version_no,7);
  h.core.state.permissions['target.manage']=false;await h.fill({amount:'100.20',reason:'依据'});assert.equal(h.writes().length,2);
});

test('legacy pending-new rows retain the six-kind batch editor and row-specific scope',async()=>{
  const row={...effectiveTarget(),id:null,kind:'visit_count',version_no:null,amount_text:null,can_history:false};
  const h=await harness({rows:[row]});await h.targetPage();await h.action('edit',row.row_key);await turn();
  await h.fill({mode:'visit_count',amount_visit_count:'12',reason:'原流程'});
  assert(h.writes()[0].url.endsWith('/console/targets/batch'));const body=JSON.parse(h.writes()[0].options.body);
  assert.equal(body.user_id,'person');assert.deepEqual(body.items,[{kind:'visit_count',amount:'12',version_no:null}]);
});

test('no management permission hides all write entries; object denied rows do not offer edit',async()=>{
  const h=await harness({rows:[{...effectiveTarget(),can_manage:false}]});
  let html=await h.targetPage();assert(!html.includes('data-action="edit"'));
  h.core.state.permissions['target.manage']=false;html=await h.targetPage();
  for(const action of ['metrics','create-custom','create','edit'])assert(!html.includes(`data-action="${action}"`));
  await assert.rejects(h.targets.manageMetrics(),/未获/);await assert.rejects(h.targets.editReviewTarget(),/未获/);assert.equal(h.writes().length,0);
});


test('standard target editor offers the three dashboard metrics and posts forecast recognition through Review in yuan',async()=>{
  const h=await harness({rows:[]});await h.targets.editTarget();await turn();
  for(const name of ['预测回款','回款 PPL 储备','预测确收（含税）','实际确收（不含税）'])assert(h.dialog.innerHTML.includes(name));
  await h.choose('user_id','person');await h.choose('mode','forecast_recognized');
  assert(h.calls.some(c=>c.url.startsWith('/api/v1/review/targets?')&&c.url.includes('kind=forecast_recognized')));
  await h.fill({mode:'forecast_recognized',amount_forecast_recognized:'10',reason:'104'});
  assert.equal(h.writes()[0].url,'/api/v1/review/targets');
  assert.deepEqual(JSON.parse(h.writes()[0].options.body),{scope:'person',user_id:'person',team_id:null,department_code:'sales',period_type:'quarter',anchor_date:h.nodes['[name=anchor_date]'].value,reason:'104',kind:'forecast_recognized',amount:'100000',version_no:null});
});

test('yuan dictionary amounts preserve every cent through list, edit, history and approval using 万元',async()=>{
  for(const code of ['forecast_recognized','custom_yuan']){
    const amountMetric={...custom,code,name:'预测确收',unit:'元'};
    const row={...effectiveTarget(),kind:code,amount_text:'234.56',pending_change:{proposed_amount:'234.57'}};
    const h=await harness({rows:[row],metrics:[amountMetric]});
    const html=await h.targetPage();assert.match(html,/0\.023456 万元/);assert.match(html,/0\.023457 万元/);
    await h.targets.editTarget(row);await turn();assert.equal(h.nodes['[name=amount]'].value,'0.023456');
    await h.fill({amount:'0.023456',reason:'保持精确金额'});
    assert.equal(JSON.parse(h.writes()[0].options.body).amount,'234.56');assert.equal(JSON.parse(h.writes()[0].options.body).version_no,7);
    h.rows=[{before_snapshot:{amount:'234.56'},after_snapshot:{amount:'234.57'}}];await h.action('history',row.id);
    assert.match(h.dialog.innerHTML,/0\.023456 万元/);assert.match(h.dialog.innerHTML,/0\.023457 万元/);
    const review=await harness({metrics:[amountMetric],rows:[{...baseRow,items:[{kind:code,previous_amount_text:'234.56',proposed_amount_text:'234.57'}]}]});
    await review.render();await review.review();assert.match(review.dialog.innerHTML,/0\.023456 万元/);assert.match(review.dialog.innerHTML,/0\.023457 万元/);
  }
});
