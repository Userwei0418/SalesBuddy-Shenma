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
  const prelude=`const state={company:{id:'company'}};
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
