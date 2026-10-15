import assert from 'node:assert/strict';
import {test} from 'node:test';
import {displayItem,editItem,contentProblem} from '../department-ui/work-report/content.js';

test('human paragraph edits drop automated claims while other content stays exact',()=>{
  const first={kind:'fact',text:'本周跟进 {{metric:visits}} 条。',source_refs:['visit:one'],metric_refs:['visits']};
  const untouched={kind:'plan',text:'下周确认验收。',source_refs:['visit:two'],metric_refs:[]};
  const content={title:'本周工作报告',sections:[{id:'progress',title:'重点进展',items:[first,untouched]}],missing_data:[]};
  assert.equal(displayItem(first,[{metric_id:'visits',display_value:'3'}]).text,'本周跟进 3 条。');
  const edited=editItem(content,'progress',0,'补充尚未登记的工作。');
  assert.deepEqual(edited.sections[0].items[0],{kind:'manual',text:'补充尚未登记的工作。',source_refs:[],metric_refs:[]});
  assert.equal(edited.sections[0].items[1],untouched);assert.equal(content.sections[0].items[0],first);assert.equal(contentProblem(edited),'');
});

test('missing statistic is explicit and empty or oversized manual text cannot save',()=>{
  assert.deepEqual(displayItem({text:'完成 {{metric:completed}} 项。'},[]),{text:'完成 〔统计暂不可用〕 项。',missing:['completed']});
  const doc=text=>({title:'报告',sections:[{id:'progress',items:[{kind:'manual',text,source_refs:[],metric_refs:[]}]}]});
  assert.match(contentProblem(doc(' ')),/空白段落/);
  assert.match(contentProblem(doc('{{metric:visits}}')),/统计占位符/);
  assert.match(contentProblem(doc('长'.repeat(12001))),/长度限制/);
});
