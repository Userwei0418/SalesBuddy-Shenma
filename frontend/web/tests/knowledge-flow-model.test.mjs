import {test} from 'node:test';
import assert from 'node:assert/strict';
import {composeKnowledgeQuestion} from '../department-ui/knowledge/flowModel.js';
test('assistant sends user background and purpose without presenting them as retrieved evidence', () => {
 const result = composeKnowledgeQuestion('  需要哪些资料？ ', ' 客户正在评估 ', ' 对客沟通 ');
 assert.equal(result, '用户提供的业务背景（不是资料证据）：客户正在评估\n\n本次用途：对客沟通\n\n需要哪些资料？');
 assert.equal(composeKnowledgeQuestion('问题'), '问题');
 assert.ok(composeKnowledgeQuestion('问'.repeat(3000), '景'.repeat(400), '方案整理').length < 4000);
});
