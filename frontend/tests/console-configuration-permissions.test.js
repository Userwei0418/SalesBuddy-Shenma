const test=require('node:test'),assert=require('node:assert/strict');
const path=require('node:path'),{pathToFileURL}=require('node:url');
const assets=path.resolve(__dirname,'../../backend/src/sales_backend/web/assets');
const rule={code:'fde_capabilities',label:'FDE规则',current:{id:'current',source:'baseline',version:1,definition:{schema_version:1,visit_entry_enabled:true,role_overrides:{},user_overrides:{old:true}}},fields:[{key:'visit_entry_enabled',label:'默认',type:'boolean'},{key:'user_overrides',label:'个人',type:'capability_users'}],member_options:[{id:'fde',name:'受控FDE'}],versions:[]};
const config={mode:'disabled',protocol:'chat_completions',provider_name:'',endpoint_url:'',model:'',timeout_seconds:30,max_retries:1,voice_id:''};
const model={purpose:'text',label:'文本接口',impact:'仅合成配置',configuration:config,version:0};
function dom(html=''){
 let markup='',form,nodes,lists;
 const root={open:true,isConnected:true,style:{},querySelector,querySelectorAll,close(){this.open=false;},showModal(){this.open=true;},setAttribute(){},removeAttribute(){},addEventListener(){},contains(){return true;}};
 function node(){return {value:'',textContent:'',innerHTML:'',disabled:false,isConnected:true,dataset:{},addEventListener(){},scrollIntoView(){},setAttribute(){},style:{}};}
 function querySelector(selector){
  if(selector==='form'||selector==='#dialog-form'||selector==='#feishu-config')return form;
  if(selector==='button[type=submit]'||selector==='[type=submit]')return markup.includes('type="submit"')?(nodes.submit ||=node()):null;
  if(selector.startsWith('#')&&!markup.includes(`id="${selector.slice(1)}"`))return null;
  return nodes[selector] ||=node();
 }
 function querySelectorAll(selector){
  const match=selector.match(/^\[([a-z-]+)\]$/);if(!match)return [];
  return lists[selector] ||= [...markup.matchAll(/<(?:button|input)\b([^>]+)>/g)].filter(m=>m[1].includes(match[1]+'=')).map(m=>{
   const el=node();for(const a of m[1].matchAll(/data-([\w-]+)="([^"]*)"/g))el.dataset[a[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=a[2];return el;
  });
 }
 Object.defineProperty(root,'innerHTML',{get:()=>markup,set:v=>{
  markup=v;nodes={};lists={};form={...node(),values:[],elements:{api_key:{value:''},mode:{value:'disabled'},app_secret:{value:''}},querySelector,querySelectorAll:()=>[],reportValidity:()=>true};
 }});
 root.innerHTML=html;return root;
}
async function harness(permissions,data={},role='sales'){
 const core=await import(pathToFileURL(path.join(assets,'core.js')).href);
 const modules=await Promise.all(['company-rules','model-api','feishu-sync','activity','agent-audit'].map(f=>import(pathToFileURL(path.join(assets,f+'.js')).href)));
 core.clearSession();Object.assign(core.state,{permissions,actor:{role,user_id:'actor',workspace_id:'w'},company:{id:'w'},token:'fixture',org:null,filters:{},offset:0,refresh:async()=>{}});
 const modal=dom(),toast=dom(),calls=[];global.document={querySelector:s=>s==='#dialog'?modal:s==='#dialog-form'?modal.querySelector(s):toast,dispatchEvent(){}};
 global.FormData=class {constructor(f){this.values=f.values||[];}get(k){return this.values.find(([n])=>n===k)?.[1]??null;}has(k){return this.values.some(([n])=>n===k);}delete(k){this.values=this.values.filter(([n])=>n!==k);}entries(){return this.values[Symbol.iterator]();}[Symbol.iterator](){return this.entries();}};
 global.fetch=async(url,options={})=>{calls.push({url,options});const value=typeof data==='function'?await data(url,options):data;return {ok:true,status:200,json:async()=>value};};
 function bind(view){const root=dom(view.html);view.bind?.(root);return root;}
 const click=(root,action,id)=>root.onclick({target:{closest:()=>({dataset:{action,id}})}});
 return{core,rules:modules[0],models:modules[1],feishu:modules[2],activity:modules[3],agents:modules[4],modal,toast,calls,bind,click};
}
const catalog=items=>({items,can_publish:true,management:{rule_count:1,capability_count:1}});
test('rule.read alone renders FDE rule without organization fetch or write actions',async()=>{
 const h=await harness({'rule.read':true},catalog([rule]));const view=await h.rules.companyRules();
 assert.equal(h.calls.length,1);assert.match(h.calls[0].url,/company-rules$/);assert.doesNotMatch(view.html,/data-action="edit"/);assert.doesNotMatch(view.html,/href="#agentExecution"/);
 const root=h.bind(view);await h.click(root,'edit','fde_capabilities');assert.equal(h.calls.length,1);assert.equal(h.modal.innerHTML,'');
});
test('independent rule draft reads narrow member options and preserves absent historical overrides',async()=>{
 const h=await harness({'rule.read':true,'rule.draft':true},catalog([rule]));const root=h.bind(await h.rules.companyRules());await h.click(root,'edit','fde_capabilities');
 assert.match(h.modal.innerHTML,/受控FDE/);assert.match(h.modal.innerHTML,/name="user_overrides:fde"/);assert.doesNotMatch(h.modal.innerHTML,/id="preview-rule"/);
 const form=h.modal.querySelector('form');form.values=[['visit_entry_enabled','true'],['user_overrides:fde','false'],['reason','合成编辑']];
 await form.onsubmit({preventDefault(){},currentTarget:form});
 const sent=JSON.parse(h.calls.at(-1).options.body);assert.deepEqual(sent.definition.user_overrides,{old:true,fde:false});
 assert(!h.calls.some(c=>c.url.includes('organization')));
});
test('preview-only rule permission does not add save; a later preview revoke prevents a request',async()=>{
 const h=await harness({'rule.read':true,'rule.preview':true},catalog([rule]));const root=h.bind(await h.rules.companyRules());await h.click(root,'edit',rule.code);
 assert.match(h.modal.innerHTML,/id="preview-rule"/);assert.doesNotMatch(h.modal.innerHTML,/type="submit"/);
 h.core.state.permissions['rule.preview']=false;await h.modal.querySelector('#preview-rule').onclick();assert.equal(h.calls.length,1);
 assert.match(h.modal.querySelector('#rule-preview').innerHTML,/未获/);
});
for(const permission of ['rule.publish','rule.restore'])test(permission+' is independent of draft and actor display role',async()=>{
 const item=structuredClone(rule);item.versions=[{id:'version',version_no:1,status:permission.endsWith('publish')?'draft':'retired',definition:item.current.definition,revision_no:1}];
 const h=await harness({'rule.read':true,[permission]:true},catalog([item]));const root=h.bind(await h.rules.companyRules());await h.click(root,'versions',item.code);
 h.modal.querySelectorAll('[data-version]')[0].onclick();assert.match(h.modal.innerHTML,/type="submit"/);assert.doesNotMatch(h.modal.innerHTML,/id="edit-draft"/);
 const form=h.modal.querySelector('form');form.values=[['reason','合成恢复']];h.core.state.permissions[permission]=false;
 await form.onsubmit({preventDefault(){},currentTarget:form});assert.equal(h.calls.length,1);assert.match(form.querySelector('.form-error').innerHTML,/未获/);
});
test('technical edits additionally require config_publish, business guidance does not',async()=>{
 const technical={...rule,code:'agent_execution.battle_map_review',business_rule:{...rule,code:'agent_business.battle_map_review'}};
 const h=await harness({'ai.execution_read':true,'rule.draft':true},catalog([technical]));let view=await h.rules.agentExecution();
 assert.doesNotMatch(view.html,/data-action="edit"/);assert.match(view.html,/data-action="edit-business"/);assert.doesNotMatch(view.html,/href="#companyRules"/);
 h.core.state.permissions['ai.config_publish']=true;view=await h.rules.agentExecution();assert.match(view.html,/data-action="edit"/);
});
test('model read uses permission instead of administrator name; test/publish/restore are independent',async()=>{
 const h=await harness({'ai.config_read':true},{items:[model]});let view=await h.models.modelApis();
 assert.match(view.html,/data-history=/);assert.doesNotMatch(view.html,/data-edit=|data-connectivity/);
 h.core.state.actor.role='administrator';h.core.state.permissions={};await assert.rejects(h.models.modelApis(),/未获/);
 h.core.state.permissions={'ai.config_read':true,'ai.config_test':true};view=await h.models.modelApis();const root=h.bind(view);root.querySelectorAll('[data-edit]')[0].onclick();
 assert.match(h.modal.innerHTML,/测试配置/);assert.doesNotMatch(h.modal.innerHTML,/type="submit"/);
 h.core.state.permissions['ai.config_test']=false;await h.modal.querySelector('#test-connection').onclick();assert.equal(h.calls.length,2);assert.match(h.modal.querySelector('#test-result').textContent,/未获/);
});
test('model historical restore load requires rollback and test, publish requires publish permission',async()=>{
 const h=await harness({'ai.config_read':true,'ai.config_test':true},url=>url.endsWith('/releases')?{items:[{version_no:1,config_snapshot:config}]}:{items:[model]});
 let root=h.bind(await h.models.modelApis());await root.querySelectorAll('[data-history]')[0].onclick();assert.doesNotMatch(h.modal.innerHTML,/data-restore=/);
 h.core.state.permissions['ai.config_rollback']=true;await root.querySelectorAll('[data-history]')[0].onclick();assert.match(h.modal.innerHTML,/data-restore=/);
 h.modal.querySelectorAll('[data-restore]')[0].onclick();assert.doesNotMatch(h.modal.innerHTML,/type="submit"/);
 h.core.state.permissions['ai.config_publish']=true;root=h.bind(await h.models.modelApis());root.querySelectorAll('[data-edit]')[0].onclick();assert.match(h.modal.innerHTML,/type="submit"/);
});
function feishuData(url){return url.endsWith('/catalog')?{objects:[]}:url.endsWith('/status')?{counts:[],unknown:[{event_id:'event',dedupe_key:'key'}],errors:[{id:'event',error_code:'FAILED'}]}:{config:{revision:1,direction:'system_to_base',notification:{routes:[]}},enabled:false,has_credential:true};}
for(const permission of [null,'feishu.configure','feishu.control','feishu.recover'])test('Feishu capability '+permission+' is bounded independently',async()=>{
 const h=await harness({'feishu.read':true,...(permission?{[permission]:true}:{})},feishuData);const view=await h.feishu.feishuSync();const root=h.bind(view);
 assert.equal(view.html.includes('type="submit"'),permission==='feishu.configure');assert.equal(/<fieldset disabled/.test(view.html),permission!=='feishu.configure');
 assert.equal(view.html.includes('data-sync-action='),permission==='feishu.control');assert.equal(view.html.includes('data-recover='),permission==='feishu.recover');
 h.core.state.permissions[permission]=false;
 if(permission==='feishu.control')await root.querySelectorAll('[data-sync-action]')[0].onclick();
 if(permission==='feishu.recover')await root.querySelectorAll('[data-recover]')[0].onclick();
 if(permission==='feishu.configure'){const form=root.querySelector('#feishu-config');await form.onsubmit({preventDefault(){}});assert.match(root.querySelector('#feishu-feedback').textContent,/未获/);}
 assert.equal(h.calls.length,3);
});
for(const [module,fn,permission] of [['activity','businessActivities','audit.business_export'],['agents','agentRuns','ai.run_export']])test(module+' export must be granted and stale actions are blocked',async()=>{
 const data={items:[],total:0,actions:{},categories:{}};const h=await harness({},data);let view=await h[module][fn]();assert.doesNotMatch(view.html,/data-action="export"/);
 h.core.state.permissions[permission]=true;view=await h[module][fn]();assert.match(view.html,/data-action="export"/);const root=h.bind(view);
 h.core.state.permissions[permission]=false;await h.click(root,'export');assert.equal(h.calls.length,2);
});

test('connectivity retry rechecks config_test and never sends a request after revoke',async()=>{
 const h=await harness({'ai.config_test':true},{status:'passed',connection_status:'passed',inference_status:'not_tested',message:'合成回执',id:'fixture'});
 const {bindConnectivityTests}=await import(pathToFileURL(path.join(assets,'ai-connectivity.js')).href);
 const root=dom('<button data-connectivity-kind="direct" data-connectivity-target="text" data-connectivity-label="文本"></button>');
 bindConnectivityTests(root);root.querySelectorAll('[data-connectivity-kind]')[0].onclick();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(h.calls.length,1);assert.match(h.calls[0].url,/ai-connectivity\/direct\/text\/tests$/);
 h.core.state.permissions['ai.config_test']=false;await h.modal.querySelector('[data-connectivity-retry]').onclick();
 assert.equal(h.calls.length,1);assert.match(h.modal.querySelector('[data-connectivity-result]').textContent,/未获/);
});
test('authorized model test can publish, while publish revoked after a passed receipt is blocked',async()=>{
 const h=await harness({'ai.config_read':true,'ai.config_test':true,'ai.config_publish':true},(url,opts)=>url.endsWith('/tests')&&opts.method==='POST'?{id:'test',status:'passed',result:{message:'已检查',elapsed_ms:1}}:{items:[model]});
 const root=h.bind(await h.models.modelApis());root.querySelectorAll('[data-edit]')[0].onclick();const form=h.modal.querySelector('form');form.values=[['mode','disabled']];
 await h.modal.querySelector('#test-connection').onclick();assert.equal(h.calls.length,2);assert.equal(form.querySelector('button[type=submit]').disabled,false);
 h.core.state.permissions['ai.config_publish']=false;await form.onsubmit({preventDefault(){},currentTarget:form});assert.equal(h.calls.length,2);
 h.core.state.permissions['ai.config_publish']=true;await form.onsubmit({preventDefault(){},currentTarget:form});assert.match(h.calls.at(-1).url,/text\/publish$/);assert.deepEqual(JSON.parse(h.calls.at(-1).options.body),{test_id:'test',expected_version:0});
});
for(const permission of ['feishu.configure','feishu.control','feishu.recover'])test('authorized '+permission+' dispatches its own endpoint only',async()=>{
 const h=await harness({'feishu.read':true,[permission]:true},feishuData);const root=h.bind(await h.feishu.feishuSync());
 if(permission==='feishu.configure'){
  const form=root.querySelector('#feishu-config');form.values=[['app_id','app'],['base_token','base'],['routing_mode','single_group'],['sync_direction','system_to_base']];await form.onsubmit({preventDefault(){}});
  assert.equal(h.calls.at(-1).options.method,'PUT');assert.match(h.calls.at(-1).url,/feishu-sync$/);
 }else if(permission==='feishu.control'){
  await root.querySelectorAll('[data-sync-action]')[0].onclick();assert.match(h.calls.at(-1).url,/feishu-sync\/validate$/);
 }else{
  root.querySelector('#sync-recovery-note').value='合成核对';await root.querySelectorAll('[data-recover]')[0].onclick();assert.match(h.calls.at(-1).url,/feishu-sync\/recover$/);
 }
 assert.equal(h.calls.length,4);assert.equal(JSON.parse(h.calls.at(-1).options.body).expected_revision,1);
});
