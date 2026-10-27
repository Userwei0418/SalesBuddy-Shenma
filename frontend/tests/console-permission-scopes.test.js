const test=require('node:test');
const assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const path=require('node:path');
const load=()=>import(pathToFileURL(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/permissions.js')).href);
const catalog=[{code:'access.console',label:'后台入口',module:'入口',scopes:['workspace']},
 {code:'visit.upload',label:'上传',module:'拜访',scopes:['self']},
 {code:'customer.read',label:'客户',module:'客户',scopes:['self','assigned','teams','workspace']}];

test('role editor exposes fixed scopes and marks legacy inheritance for explicit save',async()=>{
 const {permissionRows}=await load();
 const html=permissionRows(catalog,[],[]);
 const selects=[...html.matchAll(/<select name="scope:[^"]+"[^>]*>(.*?)<\/select>/g)].map(m=>m[1]);
 assert.equal(selects.length,3);
 assert(!selects[0].includes('inherit'));assert.match(selects[0],/value="workspace" selected/);
 assert(!selects[1].includes('inherit'));assert.match(selects[1],/value="self" selected/);
 assert.match(selects[2],/value="inherit" selected/);
 assert(!html.includes('旧配置'));
 assert.match(permissionRows(catalog,[{permission:'access.console',scope:'inherit'}],[]),/旧配置为继承范围.*保存后生效/);
});

test('save normalizes fixed role scopes but preserves business inheritance and whole-function denies',async()=>{
 const {collectPermissions}=await load();const form=new FormData();
 for(const p of catalog){form.set('enabled:'+p.code,'on');form.set('scope:'+p.code,'inherit');}
 assert.deepEqual(collectPermissions(form,catalog).map(p=>p.scope),['workspace','self','inherit']);
 form.set('scope:access.console','self');assert.throws(()=>collectPermissions(form,catalog),/不支持/);
 form.set('scope:access.console','workspace');form.set('scope:visit.upload','workspace');assert.throws(()=>collectPermissions(form,catalog),/不支持/);
 for(const p of catalog)form.set('effect:'+p.code,'deny');
 assert(collectPermissions(form,catalog,true).every(p=>p.effect==='deny'&&p.scope==='inherit'&&!p.team_ids.length));
 form.set('effect:access.console','allow');form.set('scope:access.console','inherit');
 assert.throws(()=>collectPermissions(form,catalog,true),/不支持/);
});

// Model the actual reused <dialog> and replaced child <form>. Change bubbles to
// the dialog, so a listener retained there reproduces the role→account crash.
function editorFixture(account){
 const selection=account?{value:''}:{checked:true};
 const scope={value:'self'},teams={hidden:false};
 const controls={hidden:false,querySelector:s=>s==='select'?scope:teams};
 const row={dataset:{permission:'customer.read',label:'客户'},querySelector:s=>{
  if(s==='[data-scope-controls]')return controls;
  if(s==='input[type=checkbox]')return account?null:selection;
  if(s==='[name="effect:customer.read"]')return account?selection:null;
  throw Error('unexpected selector '+s);
 }};
 const search={};const events=[];
 const form={addEventListener:(name,fn)=>events.push({name,fn}),querySelector:s=>s==='[data-permission-search]'?search:null,
  querySelectorAll:s=>s==='[data-permission]'?[row]:[]};
 return {selection,scope,teams,controls,form,events};
}

test('reused dialog switches role/account controls without stale listeners or checkbox crashes',async()=>{
 const {bindPermissionEditor}=await load();const dialogEvents=[];
 let current;
 const dialog={querySelector:s=>s==='#dialog-form'?current.form:current.form.querySelector(s),
  querySelectorAll:s=>current.form.querySelectorAll(s),addEventListener:(name,fn)=>dialogEvents.push({name,fn})};
 const change=()=>{for(const {fn} of [...current.events,...dialogEvents])fn();};
 current=editorFixture(false);bindPermissionEditor(dialog);
 assert.equal(current.controls.hidden,false);
 current.selection.checked=false;change();assert.equal(current.controls.hidden,true);
 current.selection.checked=true;current.scope.value='teams';change();assert.equal(current.teams.hidden,false);
 const old=current;
 current=editorFixture(true);bindPermissionEditor(dialog,true);
 assert.equal(current.controls.hidden,true);
 current.selection.value='allow';change();assert.equal(current.controls.hidden,false);
 current.scope.value='teams';change();assert.equal(current.teams.hidden,false);
 current.selection.value='deny';change();assert.equal(current.controls.hidden,true);
 assert.equal(old.controls.hidden,false);
 assert.equal(dialogEvents.length,0);
 current=editorFixture(false);bindPermissionEditor(dialog);change();assert.equal(current.controls.hidden,false);
});
