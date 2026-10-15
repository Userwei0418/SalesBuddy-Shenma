import {test} from 'node:test';
import assert from 'node:assert/strict';
import {decisionLabel,evidenceText,riskClosed,riskCount,riskEvent,riskStatus,riskSuggestion,riskVersioned} from '../department-ui/risk-presentation.js';
import {readFileSync} from 'node:fs';
import {inspectStyles} from '../scripts/check-ui-contract.mjs';

test('suggestion decisions and accepted risks never imply closed; explicit clearing is authoritative',()=>{
  const r={status:'accepted',suggested_action:'',next_action:'旧建议',current_decision:{decision:'adopt'}};
  assert.equal(riskClosed(r),false);assert.equal(riskStatus(r),'已接受 · 未关闭');
  assert.equal(riskSuggestion(r),'');assert.equal(decisionLabel(r.current_decision.decision),'已采纳');
  assert.equal(riskVersioned({...r,version_no:'1'}),false);assert.equal(riskVersioned({...r,version_no:1}),true);
});
test('counts preserve true zero and unknown; evidence and audit never expose raw import metadata',()=>{
  assert.equal(riskCount(0),0);assert.equal(riskCount(undefined),null);assert.equal(riskCount(-1),null);
  assert.equal(evidenceText({synthetic:true,source_ids:['private']}),'');
  assert.equal(evidenceText('{"facts":[{"detail":"回款延期"}],"source_ids":["private"]}'),'回款延期');
  const event=riskEvent({event_type:'suggestion_decided',payload:{decision:'defer',note:'等待客户新预算',suggested_action:'先确认预算',request_id:'internal'}});
  assert.equal(event.title,'建议暂不采纳');assert.equal(event.note,'等待客户新预算');assert.doesNotMatch(JSON.stringify(event),/internal|request_id/);
});
test('new risk and insights styles use the shared visual tokens',()=>{
  for(const file of ['risk-review.css','bi.css'])assert.deepEqual(inspectStyles(readFileSync(new URL('../department-ui/'+file,import.meta.url),'utf8'),file),[]);
});
