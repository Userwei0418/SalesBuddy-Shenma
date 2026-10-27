const test=require('node:test');
const assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const path=require('node:path');
const load=()=>import(pathToFileURL(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/permissions.js')).href);

test('delegated save preserves administrator and other editor entries including denies',async()=>{
 const {mergeEditableEntries}=await load();
 const old=[{permission:'customer.read',effect:'deny',scope:'inherit',team_ids:[]},
 {permission:'task.read',effect:'allow',scope:'self',team_ids:[],delegation_user_ref_id:'other',delegation_version:2},
 {permission:'opportunity.create',effect:'allow',scope:'self',team_ids:[],delegation_user_ref_id:'me',delegation_version:1}];
 const changed=[{permission:'customer.read',effect:'allow',scope:'workspace',team_ids:[]},
 {permission:'opportunity.create',effect:'allow',scope:'teams',team_ids:['t']}];
 const saved=mergeEditableEntries(old,changed,{company_administrator:false,user_id:'me'});
 assert.equal(saved.length,3);assert.equal(saved[0].effect,'deny');assert.equal(saved[1].permission,'task.read');
 assert.equal(saved[2].scope,'teams');assert(!saved.some(row=>'delegation_user_ref_id' in row));
});

test('delegated role removal keeps direct company authority and strips provenance from write contract',async()=>{
 const {mergeEditableEntries}=await load();
 const old=[{role_id:'r1',scope:'self',team_ids:[],approved_role_version:1},
 {role_id:'r2',scope:'self',team_ids:[],delegation_user_ref_id:'me',delegation_version:2,approved_role_version:1}];
 assert.deepEqual(mergeEditableEntries(old,[],{company_administrator:false,user_id:'me'},true),[
 {role_id:'r1',scope:'self',team_ids:[],expires_at:null}]);
 assert.deepEqual(mergeEditableEntries(old,[],{company_administrator:true},true),[]);
});
