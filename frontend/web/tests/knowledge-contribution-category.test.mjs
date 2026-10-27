import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {CATEGORIES,contributionCategoryAllowed,contributionCategoryOptions,contributionForm} from '../department-ui/knowledge/model.js';
import {canManageContributionCatalog,createKnowledgeService,errorText} from '../department-ui/knowledge/service.js';
const require=createRequire(import.meta.url),access=require('../../miniprogram/utils/access.js');
test('workspace catalog grant is authoritative; role, material manage, team scope and unknown snapshots never broaden contribution',()=>{
 const before=globalThis.SalesRuntime;
 try {
  let actor;globalThis.SalesRuntime={app:{globalData:{get session(){return actor;}},can:key=>access.can(actor,key)},require:name=>name==='utils/access'?access:{}};
  for(const snapshot of [null,{role:'general_manager',can_manage:true},{permissions:{'knowledge.catalog_manage':true}},{permissions:{'knowledge.catalog_manage':true},permissionGrants:[{permission_code:'knowledge.catalog_manage',effect:'allow',scope_code:'teams'}]}]){actor=snapshot;assert.equal(canManageContributionCatalog(),false);}
  actor={permissions:{'knowledge.catalog_manage':true},permissionGrants:[{permission_code:'knowledge.catalog_manage',effect:'allow',scope_code:'workspace'}]};assert.equal(canManageContributionCatalog(),true);
  actor.permissionGrants.push({permission_code:'knowledge.catalog_manage',effect:'deny',scope_code:'workspace'});assert.equal(canManageContributionCatalog(),false);
 }finally{globalThis.SalesRuntime=before;}
});
test('only the writable category options narrow; historical readback and the complete directory enum are preserved',()=>{
 const source={contribution:{selection:{content_category:'product_intro',product_code:'general',content_scope:'customer',content:{file_ids:[],links:['https://example.test/material']}}}},before=structuredClone(source),form=contributionForm(source);
 assert.equal(form.content_category,'product_intro');assert.equal(contributionCategoryAllowed(form.content_category),false);
 assert.deepEqual(contributionCategoryOptions().map(row=>row.value),['customer_solution','conference_speech']);
 assert.equal(contributionCategoryOptions(false,form.content_category)[0].disabled,true);assert.equal(contributionCategoryOptions(true).length,7);assert.equal(contributionCategoryAllowed('product_intro',true),true);assert.equal(contributionCategoryAllowed('invented',true),false);
 assert.equal(CATEGORIES.length,7);assert.equal(CATEGORIES.some(row=>row.disabled),false);assert.deepEqual(source,before);
});
test('create and add-version category denials use the stable safe message without leaking backend text',async()=>{
 const service=createKnowledgeService({request:async()=>{throw Object.assign(Error('private provider or raw error text'),{serverCode:'KNOWLEDGE_CONTRIBUTION_CATEGORY_DENIED',statusCode:403});}});
 for(const action of [()=>service.createMaterial({},'idempotency'),()=>service.addVersion('material',{},'idempotency')])await assert.rejects(action(),error=>error.code==='KNOWLEDGE_CONTRIBUTION_CATEGORY_DENIED'&&error.statusCode===403&&error.message.includes('目录维护权限')&&!error.message.includes('private'));
 assert.match(errorText({code:'KNOWLEDGE_CONTRIBUTION_CATEGORY_DENIED',message:'raw'}),/刷新页面更新权限/);
});
