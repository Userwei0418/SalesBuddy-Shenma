const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assets=path.resolve(__dirname,'../../backend/src/sales_backend/web/assets');
const row={id:'op',customer_id:'c',customer_name:'授权客户',name:'本人商机',owner_user_ref_id:'self',owner_name:'本人',amount:'10000',probability:10,status:'open',sales_channel:'direct',can_edit:true,can_quote:false,quarterly_forecasts:[],changes:[],visits:[],quote_references:[]};
async function harness(permissions){
 const core=await import(pathToFileURL(path.join(assets,'core.js')).href), pages=await import(pathToFileURL(path.join(assets,'pages.js')).href);
 core.clearSession();Object.assign(core.state,{permissions,org:null,actor:{workspace_id:'w',user_id:'self',role:'sales'},token:'fixture',filters:{},offset:0,refresh:async()=>{}});
 const choices={teams:[{id:'t',name:'本人团队'}],default_team_id:'t',default_owner_id:'self',owners:[{id:'self',name:'本人',account_code:'SELF',teams:[{id:'t',name:'本人团队'}],default_team_id:'t'}]};
 const calls=[];const data={items:[row],total:1,total_amount:'10000',stages:[]};
 const nodes={};const input=key=>nodes[key] ||= {value:'',hidden:false,innerHTML:'',style:{},setAttribute(){},scrollIntoView(){}};
 input('[name=sales_channel]').value='direct';
 const form={values:[],addEventListener(){},querySelector:s=>input(s)};
 const dialog={innerHTML:'',open:false,removeAttribute(){},setAttribute(){},addEventListener(){},querySelectorAll:()=>[],
  querySelector(s){if(s==='#dialog-form')return form;if(s.startsWith('[data-action=')){const action=s.slice(13,-1);return this.innerHTML.includes('data-action="'+action+'"')?input(s):null;}return input(s);},
  close(){this.open=false;},showModal(){this.open=true;}};
 global.document={querySelector:s=>s==='#dialog'?dialog:s==='#dialog-form'?form:input(s),dispatchEvent(){}};
 global.FormData=class{constructor(f){this.values=f.values;}get(n){return this.values.find(([k])=>k===n)?.[1]??null;}getAll(n){return this.values.filter(([k])=>k===n).map(([,v])=>v);}has(n){return this.values.some(([k])=>k===n);}[Symbol.iterator](){return this.values[Symbol.iterator]();}};
 global.fetch=async(url,options={})=>{
  calls.push({url,options});let result;
  if(options.method==='POST')result={id:'new',changed:true};
  else if(url.endsWith('/console/opportunities/amount-policy'))result={definition:{schema_version:1,warning_threshold_wan:1000}};
  else if(url.includes('/create-options'))result=choices;
  else if(url.includes('/claim-pool')||url.startsWith('/api/v1/console/customers'))result={items:[{id:'c',name:'授权客户'}]};
  else if(url.startsWith('/api/v1/console/opportunities/op'))result={...row};
  else result=data;
  return{ok:true,status:200,json:async()=>result};
 };
 const render=async()=>{const view=await pages.opportunities();const root={querySelector:()=>null};view.bind(root);return{view,click:(action,id='')=>root.onclick({target:{closest:()=>({dataset:{action,id}})}})};};
 return{core,pages,calls,data,choices,dialog,form,nodes,render};
}

test('denied create/export and read-only rows have no write buttons, and create guard blocks stale clicks',async()=>{
 const h=await harness({'opportunity.read':true,'opportunity.create':false,'opportunity.export':false,'opportunity.update':false});
 const page=await h.render();assert.doesNotMatch(page.view.html,/data-action="(?:create|export|edit)"/);
 await assert.rejects(h.pages.opportunityForm(),/未获/);assert.equal(h.calls.length,1);
});

test('self create with no organization directory selects only API-authorized self and submits omitted owner',async()=>{
 const h=await harness({'opportunity.create':true,'customer.claim_directory':true});
 await h.pages.opportunityForm();assert.match(h.dialog.innerHTML,/value="self" selected/);
 const owner=h.dialog.innerHTML.match(/<select name="owner_user_ref_id"[^>]*>(.*?)<\/select>/s)[1];
 assert.equal([...owner.matchAll(/<option value="[^"]+"/g)].length,1);
 assert.match(h.dialog.innerHTML,/name="owner_team_id"/);assert(!h.calls.some(x=>x.url.includes('organization')));
 assert(h.calls.some(x=>x.url.includes('claim-pool')));assert(h.calls.some(x=>x.url.includes('include_owners=true')));
 h.form.values=Object.entries({customer_id:'c',name:'新商机',stage:'10',amount_wan:'1',expected_close_date:'2026-12-01',sales_channel:'direct',owner_user_ref_id:'self',owner_team_id:'t',forecast_quarter:''});
 await h.form.onsubmit({preventDefault(){},currentTarget:h.form});
 const sent=JSON.parse(h.calls.find(x=>x.options.method==='POST').options.body);
 assert(!('owner_user_ref_id' in sent));assert.equal(sent.owner_team_id,'t');
});

test('customer selection uses allowed existing directory and explains missing permission before any request',async()=>{
 const h=await harness({'opportunity.create':true});
 await assert.rejects(h.pages.opportunityForm(),/客户选择目录/);assert.equal(h.calls.length,0);
 h.core.state.permissions['customer.read']=true;
 assert.equal(h.pages.opportunityCustomerDirectory('测试'),'/customers?limit=100&q='+encodeURIComponent('测试'));
});

test('editing uses saved owner without organization read and requires row-level edit permission',async()=>{
 const h=await harness({'opportunity.update':true});
 await h.pages.opportunityForm({...row});assert.match(h.dialog.innerHTML,/编辑商机/);assert.match(h.dialog.innerHTML,/原负责人|本人/);
 assert.equal(h.calls.length,1);assert.match(h.calls[0].url,/\/material-product-lines\?status=active$/);
 await assert.rejects(h.pages.opportunityForm({...row,can_edit:false}),/可编辑/);
});

for(const [canEdit,canQuote] of [[true,false],[false,true],[false,false]])test(`detail controls separate edit=${canEdit} and quote=${canQuote}`,async()=>{
 const h=await harness({'opportunity.read':true,'opportunity.update':canEdit,'opportunity.quote_create':canQuote});
 const previous={...row};Object.assign(row,{can_edit:canEdit,can_quote:canQuote});
 try{const page=await h.render();await page.click('detail','op');
 assert.equal(h.dialog.innerHTML.includes('data-action="edit"'),canEdit);
 assert.equal(h.dialog.innerHTML.includes('data-action="quote"'),canQuote);
 }finally{Object.assign(row,previous);}
});


test('incomplete creation options explain retry instead of a null-object crash',async()=>{
 const h=await harness({'opportunity.create':true,'customer.claim_directory':true});delete h.choices.owners;
 await assert.rejects(h.pages.opportunityForm(),/创建选项未完整返回/);assert.equal(h.dialog.open,false);
});
