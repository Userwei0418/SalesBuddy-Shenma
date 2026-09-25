const EMPTY = '未填写';

function text(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : EMPTY;
}

// Source amount is yuan. Format its decimal string without converting rounded “万” text.
function amountCny(value, currency) {
  if (currency && currency !== 'CNY') return '币种待确认';
  if (typeof value !== 'string' && typeof value !== 'number') return EMPTY;
  const raw = String(value).trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(raw)) return EMPTY;
  const [whole, fraction = ''] = raw.split('.');
  const integer = whole.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${integer}.${fraction.padEnd(2, '0')}`;
}

function buildOpportunityAppearanceFields(opportunity = {}) {
  // The five new ShenZhou fields have no confirmed API mapping yet.
  // Keep them explicitly unfilled; never infer them from partner, customer or import metadata.
  return [
    {key: 'name', label: '商机名称', value: text(opportunity.name)},
    {key: 'amount', label: '商机金额（元/人民币）', value: amountCny(opportunity.amount, opportunity.currency)},
    {key: 'agent', label: '代理商名称', value: EMPTY},
    {key: 'end_user', label: '最终用户名称', value: EMPTY},
    {key: 'expected_date', label: '预计签约日期', value: text(opportunity.expected_close_date)},
    {key: 'source', label: '商机来源', value: EMPTY},
    {key: 'background', label: '项目背景', value: EMPTY},
    {key: 'framework', label: '是否框架商机', value: EMPTY},
  ];
}

module.exports = {amountCny, buildOpportunityAppearanceFields};
