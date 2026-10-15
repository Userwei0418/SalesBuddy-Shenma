// File-only check of the frozen synthetic PostgreSQL response; no API requests.
// REVIEW_D03_SCOPE_NAMES_RAW_PATH=<D03_SCOPE_NAMES_POSTGRES_RESULT.json> node --test tests/review-task-scope-names-raw.mjs
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {dirname,join} from 'node:path';
import {build} from 'esbuild';
import {objectScope,objectPeriod} from '../department-ui/review/tasks/model.js';

assert.ok(process.env.REVIEW_D03_SCOPE_NAMES_RAW_PATH,'Set REVIEW_D03_SCOPE_NAMES_RAW_PATH to the retained backend response');
const rawPath=process.env.REVIEW_D03_SCOPE_NAMES_RAW_PATH;
const bytes=readFileSync(rawPath),receiptBytes=readFileSync(join(dirname(rawPath),'D03_SCOPE_NAMES_RESULT.json'));
const sha=value=>createHash('sha256').update(value).digest('hex');
assert.equal(sha(receiptBytes),'77ac31f1289cc5fe269013a57768efc0844cc0df151323f9f8b2b4738c5a512c');
assert.equal(sha(bytes),'eddc8e6a71de49428cddfc19d7b40e8aee6fd79fabad2cea63fe2380aaf1e014');
const receipt=JSON.parse(receiptBytes),raw=JSON.parse(bytes),alignment=raw.samples.v2_alignment;
assert.equal(receipt.postgres.sha256,sha(bytes));
const objects=[...alignment.targets,...alignment.key_results];

test('frozen PG task alignment displays actual target and KR scope names without changing raw facts',()=>{
  const before=JSON.stringify(alignment);
  assert.equal(alignment.contract_version,2);
  assert.equal(objects.length,4);
  for(const row of objects){
    assert.equal(objectScope(row),row.scope.scope_type==='team'?row.scope.team_name:row.scope.user_name);
    assert.equal(objectPeriod(row),'季度 2026-10-01 至 2026-12-31');
  }
  assert.equal(objectScope(alignment.targets.find(row=>row.scope.scope_type==='person')),'own_SUBJECT589b99a78b2c');
  assert.equal(objectScope(alignment.targets.find(row=>row.scope.scope_type==='team')),'own_TEAM');
  assert.equal(JSON.stringify(alignment),before);
});

// These are deliberate adversarial variants of the saved raw, not claimed to
// be additional PostgreSQL responses. Directory restriction is nullable by V1.
const misleadingNames={owner_name:'DO_NOT_DISPLAY_OWNER',user_name:'DO_NOT_DISPLAY_PERSON',team_name:'DO_NOT_DISPLAY_TEAM'};
const unnamed=row=>({...row,...misleadingNames,scope:{...row.scope,user_name:null,team_name:null}});
test('nullable nested names never borrow owner or unrelated directory names; flat target names still work',()=>{
  for(const row of objects){
    assert.equal(objectScope({...row,...misleadingNames}),objectScope(row));
    const hidden=unnamed(row);
    assert.doesNotMatch(objectScope(hidden),/DO_NOT_DISPLAY/);
    const id=row.scope.scope_type==='team'?row.scope.team_id:row.scope.user_id;
    assert.equal(objectScope(hidden),`${row.scope.scope_type==='team'?'团队':'个人'}（${id}）`);
    const missing={...hidden,scope:{...hidden.scope}};
    delete missing.scope.user_name;delete missing.scope.team_name;
    assert.equal(objectScope(missing),objectScope(hidden));
  }
  assert.equal(objectScope({scope_type:'person',user_ref_id:'person-a',user_name:'目录成员',owner_name:'DO_NOT_DISPLAY_OWNER'}),'目录成员');
  assert.equal(objectScope({scope_type:'team',team_id:'team-a',team_name:'目录团队'}),'目录团队');
  assert.equal(objectScope({scope_type:'person',user_ref_id:'person-a',owner_name:'DO_NOT_DISPLAY_OWNER'}),'个人（person-a）');
});

test('actual TaskAlignmentSummary renders raw names and does not rehydrate restricted objects from KR directory',async()=>{
  const root=fileURLToPath(new URL('../',import.meta.url));
  const bundled=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
    import React from 'react';import {renderToStaticMarkup} from 'react-dom/server';
    import {TaskAlignmentSummary} from './department-ui/review/tasks/TaskAlignmentFields.jsx';
    export const render=(alignment,keyResults=[])=>renderToStaticMarkup(<TaskAlignmentSummary alignment={alignment} keyResults={keyResults}/>);
  `},bundle:true,platform:'node',format:'cjs',write:false,loader:{'.css':'empty','.svg':'dataurl'},define:{'process.env.NODE_ENV':'"production"'}});
  const module={exports:{}};
  new Function('require','module','exports',bundled.outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
  const rendered=module.exports.render(alignment);
  assert.equal((rendered.match(/own_TEAM/g)||[]).length,2);
  assert.equal((rendered.match(/own_SUBJECT589b99a78b2c/g)||[]).length,2);
  assert.equal((rendered.match(/季度 2026-10-01 至 2026-12-31/g)||[]).length,4);
  const nullNames={...alignment,targets:alignment.targets.map(unnamed),key_results:alignment.key_results.map(unnamed)};
  assert.doesNotMatch(module.exports.render(nullNames,alignment.key_results),/DO_NOT_DISPLAY|own_TEAM|own_SUBJECT/);
  const restricted={...alignment,targets:[],key_results:[],restricted_target_count:2,restricted_key_result_count:2};
  const restrictedHtml=module.exports.render(restricted,alignment.key_results);
  assert.match(restrictedHtml,/另有 2 项业绩目标受查看权限限制/);
  assert.match(restrictedHtml,/另有 2 项 KR 受查看权限限制/);
  assert.doesNotMatch(restrictedHtml,/own_TEAM|own_SUBJECT|2026-10-01/);
});
