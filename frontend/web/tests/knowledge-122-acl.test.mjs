import test from 'node:test';
import assert from 'node:assert/strict';
import {createKnowledgeService} from '../department-ui/knowledge/service.js';
const id = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const dto = (overrides = {}) => ({material_id:id(1),subject_kind:'user',items:[{subject_id:id(2),display_name:'财务成员'}],total:21,limit:20,offset:20,...overrides});
// Frozen Backend contract: subject_id/display_name, not generic directory id/name or members/teams.
test('ACL candidates use material context, exact query and fresh object-authorized DTO', async () => {
 const calls=[];const service=createKnowledgeService({request:async options=>{calls.push(options);return dto();},getDirectoryMembers:()=>{throw Error('ordinary directory must never be called');}});
 const result=await service.listAclCandidates(id(1),{subject_kind:'user',q:'财务 & 支持',limit:20,offset:20});
 assert.equal(result.items[0].display_name,'财务成员');assert.equal(result.items[0].subject_id,id(2));assert.equal(result.total,21);
 const url=new URL('https://fixture.test'+calls[0].path);assert.equal(url.pathname,`/knowledge/materials/${id(1)}/acl-candidates`);
 assert.deepEqual(Object.fromEntries(url.searchParams),{subject_kind:'user',q:'财务 & 支持',limit:'20',offset:'20'});assert.equal(calls[0].fresh,true);
});
test('team candidates preserve valid empty teams without role filters or client-side scope reconstruction', async () => {
 const expected=dto({subject_kind:'team',items:[{subject_id:id(3),display_name:'有效空团队'}],total:1,offset:0});
 const service=createKnowledgeService({request:async()=>expected});
 assert.deepEqual(await service.listAclCandidates(id(1),{subject_kind:'team'}),expected);
});
test('candidate errors remain errors and cannot be converted to an empty directory', async () => {
 for(const [code,statusCode] of [['KNOWLEDGE_ACCESS_DENIED',403],['KNOWLEDGE_MATERIAL_NOT_FOUND',404]]) {
  const service=createKnowledgeService({request:async()=>{throw Object.assign(Error(code),{serverCode:code,statusCode});}});
  await assert.rejects(service.listAclCandidates(id(1),{subject_kind:'user'}),error=>error.code===code&&error.statusCode===statusCode);
 }
});
test('wrong object/type, generic-directory shape and missing pagination fail closed', async () => {
 const bad=[dto({material_id:id(9)}),dto({subject_kind:'team'}),{items:[{id:id(2),name:'成员'}],teams:[]},dto({limit:undefined}),dto({items:[{subject_id:id(2),display_name:'成员'},{subject_id:id(2),display_name:'重复'}]})];
 for(const value of bad) {
  const service=createKnowledgeService({request:async()=>value});
  await assert.rejects(service.listAclCandidates(id(1),{subject_kind:'user',offset:20}),/响应与当前材料/);
 }
});
test('query boundaries reject unsupported company lookup and out-of-contract pagination without a request', async () => {
 let calls=0;const service=createKnowledgeService({request:async()=>{calls++;return dto();}});
 for(const args of [{subject_kind:'company'},{subject_kind:'user',q:'x'.repeat(101)},{subject_kind:'user',limit:101},{subject_kind:'team',offset:100001}]) await assert.rejects(service.listAclCandidates(id(1),args),/查询条件/);
 assert.equal(calls,0);
});
