import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const window = {};
vm.runInNewContext(fs.readFileSync(new URL('../review-pages.js', import.meta.url), 'utf8'), {window});
let key = 0;
const node = (className, value, props = {}) => ({
  tag: 'view', key: String(key++), attrs: {class: className}, events: {},
  children: typeof value === 'string' ? [{tag: '#text', key: String(key++), text: value}] : value,
  ...props,
});
const copy = nodes => nodes.map(n => n.tag === '#text' ? n.text : copy(n.children || [])).join('');
const adapt = (name, nodes) => window.SalesReviewPages.adapt(nodes, {route: `pages/${name}/index`});

test('removes known page helper copy and wrappers without dropping titles or controls', () => {
  const input = node('search', '', {tag: 'input', events: {input: 'inputCustomer'}});
  const result = adapt('opportunity-create', [node('page', [
    node('hero-title', '新增商机'), node('hero-subtitle', '先选择客户，再完善商机信息'),
    node('heading-extra', [node('card-description', '商机将归档到所选客户名下')]), input,
  ])]);
  assert.equal(copy(result), '新增商机');
  assert.equal(result[0].children.length, 2);
  assert.equal(result[0].children[1], input);
});

test('removes display-only overview hints and activity subtitle', () => {
  const result = adapt('index', [node('web-home-metric-hint', '包含逾期、今天与未来'),
    node('business-subtitle', '最新跟进已同步，客户作战位置已重新评估。'),
    node('business-title', '客户作战地图已更新：测试客户'), node('business-row', '截止日期 2026-10-01')]);
  assert.equal(copy(result), '客户作战地图已更新：测试客户截止日期 2026-10-01');
});

test('same class with a real error or user evidence is preserved', () => {
  const error = node('asset-acv-note', '团队目录加载失败，请重试');
  const result = adapt('customers', [node('asset-acv-note', 'ACV 为当前在推商机总额；本年／历年仅切换确收和回款'), error]);
  assert.equal(result.length, 1);
  assert.equal(result[0], error);
  const evidence = [node('completion-help', '2026年9月20日 15:30'), node('completion-help', '测试负责人 提交的完成说明'), node('review-evidence', '实际交付结果'), node('fde-note', '任务待交接')];
  assert.equal(adapt('task-detail', evidence).length, 4);
});

test('bindings and required action/permission reasons are never removed', () => {
  const action = node('quarter-filter-foot', [node('metric-help', '统计口径', {events: {tap: 'showOpportunityMetricHelp'}})]);
  const denied = node('access-empty', [node('', '此功能暂不可用'), node('', '仅负责销售可修改')]);
  const result = adapt('workbench', [action, denied, node('summary-data-note', '3 个商机未填写关单日期，未计入季度总量')]);
  assert.equal(result[0], action);
  assert.equal(result[1], denied);
  assert.equal(result[2].children[0].text, '3 个商机未填写关单日期，未计入季度总量');
});

test('AI sources, analysis errors and suggestions survive generic note names', () => {
  const notes = [node('readonly-note', '正在分析这次拜访…'), node('readonly-note', '服务请求超时'), node('readonly-note', '下周安排演示'), node('readonly-note', '已归档正文只读；建议由你确认后形成待办。')];
  assert.equal(copy(adapt('visit-detail', notes)), '正在分析这次拜访…服务请求超时下周安排演示');
  const source = node('op-advice-source', '基于当前商机关联资料生成 · 建议供参考');
  assert.equal(adapt('customer-assets', [source])[0], source);
});

test('component helpers are scoped to the actual component boundary', () => {
  const value = '预测金额 = 填写金额 × 商机阶段百分比';
  const component = node('', [node('forecast', [node('hint', value), node('hint', '本季度金额不得少于已登记实绩')])], {tag: 'opportunity-form', component: {}});
  const freeText = node('hint', value);
  const result = adapt('opportunity-create', [component, freeText]);
  assert.equal(copy(result[0].children), '本季度金额不得少于已登记实绩');
  assert.equal(result[1], freeText);
});

test('on-demand confirmation dialogs, alerts and explanatory buttons are retained', () => {
  const value = '请选择一家可申请认领的客户';
  const dialog = node('', [node('', value)], {attrs: {role: 'dialog'}});
  const alert = node('', value, {attrs: {role: 'alert'}});
  const button = node('', value, {tag: 'button', events: {tap: 'selectCustomer'}});
  assert.equal(adapt('customer-claim', [dialog, alert, button]).length, 3);
  assert.equal(copy([dialog]), value);
});

test('mixed descriptions keep permission state, factual counts and history range', () => {
  const note = node('opportunity-readonly-note', '当前账号可查看商业信息。协助名单由有权限的人员维护。');
  const count = node('op-section-caption', '12 条已关联跟进记录 · 最新录入在前');
  assert.equal(copy(adapt('customer-assets', [note, count])), '商机信息只读 · 协助名单只读12 条已关联跟进记录');
  const unknown = node('opportunity-readonly-note', '权限加载失败，请重试');
  assert.equal(copy(adapt('customer-assets', [unknown])), '权限加载失败，请重试');
  const component = node('', [node('records-note', '本人填写并确认归档的拜访记录 · 全部历史')], {tag: 'fde-profile', component: {}});
  assert.equal(copy(adapt('profile', [component])), '全部历史');
});

test('association blockers and unknown dynamic helpers stay visible', () => {
  const result = adapt('visit-entry', [node('web-review-selection-help', '还需选择本人参与的商机，才能提交拜访。'), node('safe-tip', '上传失败，附件未完成'), node('web-review-selection-help', '关联信息待确认，暂不能提交')]);
  assert.equal(copy(result), '待选择本人参与的商机上传失败，附件未完成关联信息待确认，暂不能提交');
});

test('full metric definition becomes an optional disclosure without losing any content', () => {
  const definition = '经营数据按所选成员或团队汇总；实际为0与未登记分别展示。';
  const result = adapt('bi', [node('data-note', [node('', '数据口径', {tag: 'text'}), node('', definition, {tag: 'label'})])]);
  assert.equal(result[0].tag, 'details');
  assert.equal(result[0].children[0].tag, 'summary');
  assert.equal(copy(result), '数据口径' + definition);
});

test('unknown routes and unclassified note/hint/subtitle content remain untouched', () => {
  const nodes = [node('subtitle', '重要业务事实'), node('hint', '请处理真实错误'), node('note', '核验依据')];
  assert.equal(copy(adapt('future-page', nodes)), '重要业务事实请处理真实错误核验依据');
});

test('login removes only redundant account teaching, retaining login and storage notices', () => {
  const result = adapt('login', [node('scope-note', '按账号自动识别身份'), node('remember-note', '仅在此设备保存'), node('password-hint', '首次登录请先修改初始密码。'), node('security-note', '账号由运营开通；忘记密码请联系运营。'), node('login-error', '账号已禁用')]);
  assert.equal(copy(result), '仅在此设备保存首次登录请先修改初始密码。账号由运营开通；忘记密码请联系运营。账号已禁用');
});
