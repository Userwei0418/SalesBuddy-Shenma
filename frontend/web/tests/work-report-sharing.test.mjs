import {test} from 'node:test';
import assert from 'node:assert/strict';
import {sharingAudience} from '../department-ui/work-report/sharing.js';
test('report sharing choices use their separate authorized audience directory',()=>{
  const targets={allowed_kinds:['members','team','company'],members:[{id:'reader'}],teams:[{id:'allowed-team'}]};
  assert.deepEqual(sharingAudience({kind:'members',member_ids:['reader','reader'],team_id:'ignored'},targets),{kind:'members',member_ids:['reader']});
  assert.deepEqual(sharingAudience({kind:'team',team_id:'allowed-team',member_ids:['ignored']},targets),{kind:'team',team_id:'allowed-team'});
  assert.deepEqual(sharingAudience({kind:'company',member_ids:['ignored']},targets),{kind:'company'});
  for(const value of [{kind:'members',member_ids:[]},{kind:'members',member_ids:['viewable-but-not-shareable']},{kind:'team',team_id:'outside'}])assert.throws(()=>sharingAudience(value,targets));
  assert.throws(()=>sharingAudience({kind:'company'},{...targets,allowed_kinds:['members']}));
});
