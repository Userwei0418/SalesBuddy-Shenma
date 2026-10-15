const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
async function load(prelude=''){
 const source=fs.readFileSync(path.join(__dirname,'../src/sales_backend/web/assets/feishu-sync.js'),'utf8').replace(/^import .*?;\n/,'');
 return import('data:text/javascript;base64,'+Buffer.from(prelude+source).toString('base64'));
}
test('configuration remains paused and never embeds the credential',async()=>{
 const {buildSyncConfig}=await load();const form=new FormData();
 for(const [k,v] of Object.entries({app_id:'cli_test',base_token:'baseTest',app_secret:'fixture-secret',routing_mode:'single_group','member:table':'tblMember','member:id':'fldId','member:name':'fldName','member:record_status':'fldStatus','member:enabled':'on'}))form.set(k,v);
 const body=buildSyncConfig(form,null,{objects:[{key:'member',fields:['name','record_status']}]},'company',()=> 'uuid');
 assert.equal(body.enabled,false);assert.equal(body.revision,1);assert.equal(body.workspace_id,'company');
 assert.equal(body.mappings.member.fields.name,'fldName');assert.equal(body.notification.enabled,false);
 assert.equal(JSON.stringify(body).includes('fixture-secret'),false);
});
test('editing preserves connection identity and increments revision',async()=>{
 const {buildSyncConfig}=await load();const form=new FormData();form.set('routing_mode','department_routes');form.set('routes','department,oc_one,oc_two');
 const config=buildSyncConfig(form,{connection_id:'same',credential_ref:'key',revision:4},{objects:[]},'company');
 assert.equal(config.connection_id,'same');assert.equal(config.credential_ref,'key');assert.equal(config.revision,5);
 assert.deepEqual(config.notification.routes,[{department_id:'department',chat_ids:['oc_one','oc_two']}]);
});
test('portable template round trip omits company identity and secret',async()=>{
 const {syncTemplate,templateFormValues}=await load();
 const template=syncTemplate({workspace_id:'company',connection_id:'private-id',credential_ref:'key',app_secret:'secret',app_id:'cli_test',base_token:'baseTest',base_url:'https://example.feishu.cn/base/baseTest',mappings:{member:{enabled:true,table_id:'tblMember',id_field_id:'fldId',fields:{name:'fldName',record_status:'fldStatus'},archive_policy:'mark_status'}},notification:{enabled:true,routing_mode:'single_group',default_chat_id:'oc_test',on_create:['customer','opportunity','visit'],routes:[]}});
 const serialized=JSON.stringify(template);
 for(const excluded of ['workspace_id','connection_id','credential_ref','app_secret'])assert.equal(serialized.includes(excluded),false);
 const fields=templateFormValues(template,{objects:[{key:'member',fields:['name','record_status']}]});
 assert.equal(fields['member:name'],'fldName');assert.equal(fields['member:enabled'],true);assert.equal(fields.notifications,true);
 assert.equal(fields.base_url,'https://example.feishu.cn/base/baseTest');assert.equal(fields.chat_id,'oc_test');assert.equal(fields.app_id,'cli_test');
});
test('template rejects credentials, unknown source fields and markup identifiers',async()=>{
 const {templateFormValues}=await load();const catalog={objects:[{key:'member',fields:['name']}]};
 const valid={template_version:1,app_id:'cli_test',base_token:'baseTest',mappings:{},notification:{routing_mode:'single_group'}};
 assert.throws(()=>templateFormValues({...valid,app_secret:'secret'},catalog));
 assert.throws(()=>templateFormValues({...valid,app_id:'<img src=x>'},catalog));
 assert.throws(()=>templateFormValues({...valid,mappings:{member:{table_id:'tblM',id_field_id:'fldId',fields:{password:'fldPassword'}}}},catalog));
 assert.throws(()=>templateFormValues({...valid,template_version:2},catalog));
 for(const base_url of ['https://evil.com/base/baseTest','https://example.feishu.cn/base/other','https://example.feishu.cn/base/baseTest?token=x'])assert.throws(()=>templateFormValues({...valid,base_url},catalog));
});
test('reserved direction round trips as a paused draft',async()=>{
 const {buildSyncConfig,syncTemplate,templateFormValues}=await load();
 const form=new FormData();
 for(const [key,value] of Object.entries({app_id:'cli_test',base_token:'baseTest',sync_direction:'bidirectional',routing_mode:'single_group'}))form.set(key,value);
 const config=buildSyncConfig(form,null,{objects:[]},'company',()=> 'uuid');
 assert.equal(config.direction,'bidirectional');assert.equal(config.enabled,false);
 assert.equal(templateFormValues(syncTemplate(config),{objects:[]}).sync_direction,'bidirectional');
});
test('reserved direction blocks runtime actions while pause stays available',async()=>{
 for(const direction of ['system_to_base','bidirectional']){
  const current={config:{revision:1,direction},has_credential:true,validated_revision:1,enabled:true};
  const prelude=`const state={company:{id:'company'}};const requirePermission=()=>{};const hasPermission=()=>true;
   const fixtures=${JSON.stringify({'/feishu-sync':current,'/feishu-sync/catalog':{objects:[]},'/feishu-sync/status':{counts:[],errors:[],unknown:[]}})};
   const api=async path=>fixtures[path];const esc=value=>String(value??'');const head=()=>'';
   const options=items=>items.map(([value,label])=>'<option value="'+value+'">'+label+'</option>').join('');
   const field=(label,name,value,attributes={})=>'<label>'+label+(attributes.select||'')+(attributes.help||'')+'</label>';`;
  const {feishuSync}=await load(prelude);const {html}=await feishuSync();
  const disabled=action=>new RegExp('data-sync-action="'+action+'"[^>]*disabled').test(html);
  assert.equal(disabled('validate'),direction==='bidirectional');
  assert.equal(disabled('initialize'),direction==='bidirectional');
  assert.equal(disabled('pause'),false);
  if(direction==='bidirectional'){
   assert.equal(disabled('enable'),true);
   assert.match(html,/双向同步仅为配置预留，尚不支持校验或启用/);
   assert.match(html,/双向同步（预留，暂不可启用）/);
  }
 }
});

const minimalTemplate=notification=>({template_version:1,app_id:'cli_test',base_token:'baseTest',mappings:{},notification:{enabled:true,routing_mode:'single_group',default_chat_id:'oc_test',routes:[],...notification}});
function formFromValues(values){
 const form=new FormData();
 for(const [key,value] of Object.entries(values)){if(value===true)form.set(key,'on');else if(value!==false)form.set(key,value);}
 return form;
}

test('updates have a separate switch and unrelated saves preserve configured creation kinds',async()=>{
 const {buildSyncConfig}=await load(),catalog={objects:[]};
 for(const on_create of [['visit'],[],['opportunity','customer']]){
  const previous={connection_id:'same',credential_ref:'key',revision:4,notification:{enabled:true,on_create,on_update:['opportunity']}};
  const form=formFromValues({routing_mode:'single_group',notifications:true,opportunity_updates:true});
  const enabled=buildSyncConfig(form,previous,catalog,'company');
  assert.equal(enabled.notification.enabled,true);assert.deepEqual(enabled.notification.on_update,['opportunity']);assert.deepEqual(enabled.notification.on_create,on_create);
  form.delete('opportunity_updates');const off=buildSyncConfig(form,previous,catalog,'company');
  assert.deepEqual(off.notification.on_update,[]);assert.equal(off.notification.enabled,true);assert.deepEqual(off.notification.on_create,on_create);
  form.set('opportunity_updates','on');form.delete('notifications');const allOff=buildSyncConfig(form,previous,catalog,'company');
  assert.equal(allOff.notification.enabled,false);assert.deepEqual(allOff.notification.on_update,['opportunity']);
 }
});

test('old templates default updates on, while explicitly disabled updates and creation subsets round trip',async()=>{
 const {buildSyncConfig,syncTemplate,templateFormValues}=await load(),catalog={objects:[]};
 const legacy=templateFormValues(minimalTemplate({}),catalog);
 assert.equal(legacy.opportunity_updates,true);assert.deepEqual(JSON.parse(legacy.notification_on_create),['customer','opportunity','visit']);
 const exportedLegacy=syncTemplate({...minimalTemplate({}),connection_id:'old'});
 assert.deepEqual(exportedLegacy.notification.on_update,['opportunity']);
 for(const on_create of [[],['visit'],['opportunity']])for(const on_update of [[],['opportunity']]){
  const imported=templateFormValues(minimalTemplate({on_create,on_update}),catalog);
  assert.equal(imported.opportunity_updates,on_update.length>0);
  const config=buildSyncConfig(formFromValues(imported),{connection_id:'keep',credential_ref:'keep',notification:{on_create:['customer']}},catalog,'new-company');
  assert.deepEqual(config.notification.on_create,on_create);assert.deepEqual(config.notification.on_update,on_update);
  const exported=syncTemplate(config);assert.deepEqual(exported.notification.on_update,on_update);assert.deepEqual(exported.notification.on_create,on_create);
  const again=templateFormValues(exported,catalog);assert.equal(again.opportunity_updates,on_update.length>0);assert.deepEqual(JSON.parse(again.notification_on_create),on_create);
 }
});

test('unknown notification kinds and malformed event selections are rejected before import or save',async()=>{
 const {buildSyncConfig,syncTemplate,templateFormValues}=await load(),catalog={objects:[]};
 for(const notification of [{on_update:['customer']},{on_update:['opportunity','visit']},{on_update:'opportunity'},{on_update:null},{on_create:['unknown']},{on_create:'visit'},{on_create:null}]){
  assert.throws(()=>templateFormValues(minimalTemplate(notification),catalog),/对象类型/);
  assert.throws(()=>syncTemplate(minimalTemplate(notification)),/对象类型/);
 }
 const form=formFromValues({routing_mode:'single_group',notification_on_create:'["unknown"]'});
 assert.throws(()=>buildSyncConfig(form,{connection_id:'c',credential_ref:'k'},catalog,'company'),/对象类型/);
});

async function rendered(notification){
 const config={revision:4,direction:'system_to_base',connection_id:'connection',credential_ref:'key',workspace_id:'company',app_id:'cli_test',base_token:'baseTest',mappings:{},notification:{routing_mode:'single_group',routes:[],...notification}};
 const fixtures={'/feishu-sync':{config,workspace_id:'company',has_credential:true,enabled:true,validated_revision:4},'/feishu-sync/catalog':{objects:[]},'/feishu-sync/status':{counts:[],errors:[],unknown:[]}};
 const prelude=`export const writes=[],notices=[];const state={company:{id:'company'},refresh:async()=>{}};
 const fixtures=${JSON.stringify(fixtures)};
 const api=async(path,options)=>{if(options){writes.push(structuredClone(options.body));return {};}return fixtures[path];};
 const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('"','&quot;');const head=()=>'';
 const options=()=>'';const field=()=>'';const toast=message=>notices.push(message);
 const requirePermission=()=>{};const hasPermission=()=>true;
 class FormData {constructor(form){this.values=new Map([...form.elements].filter(e=>e.type!=='checkbox'||e.checked).map(e=>[e.name,e.type==='checkbox'?'on':e.value]));}get(key){return this.values.get(key)??null;}has(key){return this.values.has(key);}delete(key){this.values.delete(key);}}
 `;
 const module=await load(prelude),view=await module.feishuSync();
 const fields=['app_id','base_token','base_url','sync_direction','routing_mode','chat_id','routes','notification_on_create','app_secret','migrate_target','notifications','opportunity_updates','task_notifications','task_notifications_present'];
 const elements=fields.map(name=>({name,type:['migrate_target','notifications','opportunity_updates','task_notifications'].includes(name)?'checkbox':['notification_on_create','task_notifications_present'].includes(name)?'hidden':'text',value:'',checked:false}));
 for(const field of elements)elements[field.name]=field;
 elements.app_id.value='cli_test';elements.base_token.value='baseTest';elements.sync_direction.value='system_to_base';elements.routing_mode.value='single_group';
 elements.notification_on_create.value=JSON.stringify(notification.on_create??['customer','opportunity','visit']);
 elements.task_notifications_present.value='1';elements.task_notifications.checked=(notification.on_create||[]).includes('task');
 elements.notifications.checked=notification.enabled===true;elements.opportunity_updates.checked=notification.on_update===undefined||notification.on_update.includes('opportunity');
 const button={disabled:false},form={elements,isConnected:true,querySelector:()=>button},importNode={},exportNode={},feedback={};
 const root={isConnected:true,querySelector:s=>({'#feishu-config':form,'#sync-import':importNode,'#sync-export':exportNode,'#feishu-feedback':feedback}[s]),querySelectorAll:()=>[]};
 view.bind(root);
 return {...module,view,form,importNode,feedback};
}

test('rendered legacy config defaults updates on; saved [] stays off independently of the total switch',async()=>{
 for(const enabled of [false,true])for(const on_update of [undefined,[],['opportunity']]){
  const h=await rendered({enabled,on_update});
  assert.equal(/name="opportunity_updates"[^>]*checked/.test(h.view.html),on_update===undefined||on_update.length>0);
  assert.equal(/name="notifications"[^>]*checked/.test(h.view.html),enabled);
  assert.match(h.view.html,/通知总开关/);assert.match(h.view.html,/商机更新通知/);assert.match(h.view.html,/关键字段实际变化/);assert.match(h.view.html,/历史数据只同步，不通知/);
  assert.doesNotMatch(h.view.html,/历史和修改只同步/);
 }
});

test('actual import handler and save retain explicitly disabled updates and imported creation selection',async()=>{
 const h=await rendered({enabled:true,on_update:['opportunity'],on_create:['customer','opportunity','visit']});
 h.form.elements.app_secret.value='do-not-retain';h.form.elements.migrate_target.checked=true;
 const incoming=minimalTemplate({enabled:true,on_update:[],on_create:['visit']});
 const target={files:[{size:100,text:async()=>JSON.stringify(incoming)}],value:'fixture.json'};
 await h.importNode.onchange({target});assert.equal(h.writes.length,0);assert.equal(target.value,'');
 assert.equal(h.form.elements.opportunity_updates.checked,false);assert.equal(h.form.elements.notification_on_create.value,'["visit"]');
 assert.equal(h.form.elements.app_secret.value,'');assert.equal(h.form.elements.migrate_target.checked,false);
 await h.form.onsubmit({preventDefault(){}});assert.equal(h.writes.length,1);
 assert.deepEqual(h.writes[0].config.notification.on_update,[]);assert.deepEqual(h.writes[0].config.notification.on_create,['visit']);assert.equal(h.writes[0].config.notification.enabled,true);assert.equal(h.writes[0].config.enabled,false);
 assert.equal(h.writes[0].config.connection_id,'connection');assert.equal(h.writes[0].expected_revision,4);
});

test('malformed update template leaves all current fields untouched and sends no request',async()=>{
 const h=await rendered({enabled:false,on_update:[],on_create:['customer']});
 const before=h.form.elements.map(e=>[e.name,e.value,e.checked]);
 await h.importNode.onchange({target:{files:[{size:100,text:async()=>JSON.stringify(minimalTemplate({on_update:['task']}))}],value:'bad.json'}});
 assert.deepEqual(h.form.elements.map(e=>[e.name,e.value,e.checked]),before);assert.equal(h.writes.length,0);assert.match(h.notices.at(-1),/对象类型/);
});

test('task dispatch is opt-in and toggling it preserves other notification kinds',async()=>{
 const old=await rendered({enabled:true,on_create:['visit'],on_update:[]});
 assert.equal(old.form.elements.task_notifications.checked,false);
 assert.match(old.view.html,/周任务发放通知/);assert.match(old.view.html,/草稿、历史任务不通知/);
 old.form.elements.task_notifications.checked=true;
 await old.form.onsubmit({preventDefault(){}});
 assert.deepEqual(old.writes[0].config.notification.on_create,['visit','task']);
 assert.deepEqual(old.writes[0].config.notification.on_update,[]);
 const enabled=await rendered({enabled:true,on_create:['customer','task']});
 assert.equal(enabled.form.elements.task_notifications.checked,true);
 enabled.form.elements.task_notifications.checked=false;
 await enabled.form.onsubmit({preventDefault(){}});
 assert.deepEqual(enabled.writes[0].config.notification.on_create,['customer']);
});

test('task opt-in survives template round trip and old template import turns it off',async()=>{
 const h=await rendered({enabled:true,on_create:['task','opportunity']});
 for(const on_create of [['visit','task'],['visit']]){
  await h.importNode.onchange({target:{files:[{size:100,text:async()=>JSON.stringify(minimalTemplate({on_create}))}],value:'fixture.json'}});
  assert.equal(h.form.elements.task_notifications.checked,on_create.includes('task'));
  await h.form.onsubmit({preventDefault(){}});
  assert.deepEqual(h.writes.at(-1).config.notification.on_create,on_create);
  assert.deepEqual(h.syncTemplate(h.writes.at(-1).config).notification.on_create,on_create);
 }
});
