import test from 'node:test';
import assert from 'node:assert/strict';
import {matchesSavedRevision,uncertainSave} from '../department-ui/work-report/save-recovery.js';

test('only uncertain transport and server failures trigger a readback, never known rejection or identity loss',()=>{
  for(const error of [{code:'NETWORK_ERROR'},{message:'request:fail 请求超时，请稍后重试'},{statusCode:500},{status:503}])assert.equal(uncertainSave(error),true);
  for(const error of [{statusCode:409},{statusCode:403},{statusCode:422},{code:'REPORT_IDENTITY_CHANGED'},{message:'字段不完整'}])assert.equal(uncertainSave(error),false);
});

test('save confirmation needs the same complete persisted content and a newer current revision of this document',()=>{
  const content={title:'本周报告',sections:[{id:'ppl',items:[{text:'人工编辑',kind:'manual',source_notes:[{title:'商机A',notice:'不核定正文',facts:['已登记计划']}]}]}],missing_data:[]};
  const intent={version_no:1,content};
  const view={id:'document',title:content.title,version_no:2,latest_revision_id:'r2',revision:{id:'r2',document_id:'document',content:structuredClone(content)}};
  assert.equal(matchesSavedRevision(view,'document',intent),true);
  for(const change of [
    v=>{v.revision.content.title='其他标题';},v=>{v.revision.content.sections[0].items[0].source_notes=[];},
    v=>{v.revision.content.sections[0].items[0].source_notes[0].notice='另一份来源';},v=>{v.revision.content.missing_data=['新增说明'];},
    v=>{v.version_no=1;},v=>{v.id='another';},v=>{v.revision.document_id='another';},v=>{v.latest_revision_id='r3';},
  ]){const mismatch=structuredClone(view);change(mismatch);assert.equal(matchesSavedRevision(mismatch,'document',intent),false);}
});
