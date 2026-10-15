const finance = require('./financialDisplay');
const { opportunityProbability } = require('./customerDetail');

// Detail rows must be reconcilable to cents; do not inherit compact 万 labels used elsewhere.
function exactMoney(value) {
  const amount = finance.number(value);
  return amount === null ? '未登记' : `${amount.toLocaleString('zh-CN', {maximumFractionDigits:2})}元`;
}

const definitions = [
  ['actual_recognized_exclusive', '采用的实际不含税确收，按确认期间汇总；部分登记仅展示已知小计，不将未知金额补为零。', 'period_aggregate'],
  ['actual_collection', '采用的正式回款，按确认期间汇总；已登记零与未登记分别展示。', 'period_aggregate'],
  ['forecast_recognized_inclusive', '销售最后保存的季度含税确收预测，不乘阶段概率；赢单、丢单后的已填计划仍保留。', 'forecast'],
  ['forecast_collection', '销售最后保存的季度回款预测，不乘阶段概率；它与实际回款分别记录。', 'forecast'],
  ['opportunity_acv', '预计关单日在所选季度的在推商机金额合计，不含已成单和已丢单。', 'opportunity'],
  ['weighted_opportunity_acv', '同范围在推商机金额乘阶段概率后合计，包含10%阶段；不代替销售填写的季度预测。', 'opportunity'],
  ['high_probability_acv', '同范围在推商机中，阶段概率至少70%的商机金额合计。', 'opportunity'],
  ['expected_close_count', '预计关单日在所选季度的在推商机数量，不代表已成交数量。', 'opportunity'],
];

// Formatting only: totals come from the existing financialMetrics result.
function metricDetails({kpis, opportunities, quarterRows, actualRows, selected, scopeLabel}) {
  const names = new Map(opportunities.map(row => [row.id, row.name]));
  const details = {};
  definitions.forEach(([key, definition, granularity], index) => {
    const metric = kpis[index];
    metric.key = key;
    const actual = index < 2, forecast = index >= 2 && index < 4;
    const kind = index % 2 === 0 ? 'recognized' : 'collection';
    let columns, rows;
    if (actual) {
      columns = [{key:'period',label:'确认期间',kind:'text'}, {key:'amount',label:'采用金额',kind:'text'}, {key:'quality',label:'登记情况',kind:'text'}];
      rows = actualRows.map(row => {
        const bucket = finance.aggregate(row, kind);
        return {id:`period:${row.year}-Q${row.quarter}`, period:`${row.year} Q${row.quarter}`,
          amount:bucket.status === 'withdrawn' ? '已撤回' : `${bucket.isSubtotal ? '已登记小计 ' : ''}${exactMoney(bucket.displayAmount)}`,
          quality:bucket.qualityHints.join('；') || bucket.statusLabel, can_open:false};
      });
    } else if (forecast) {
      columns = [{key:'name',label:'商机',kind:'text'}, {key:'period',label:'预测期间',kind:'text'}, {key:'amount',label:'销售填写值',kind:'text'}];
      rows = quarterRows.map((row, rowIndex) => ({id:row.id || `forecast:${row.opportunity_id || rowIndex}:${row.year}-Q${row.quarter}`,
        name:row.opportunity_name || names.get(row.opportunity_id) || '商机名称未提供', period:`${row.year} Q${row.quarter}`,
        amount:finance.number(row[`${kind}_amount`]) === null ? '未填写' : exactMoney(row[`${kind}_amount`]), can_open:false}));
    } else {
      columns = [{key:'name',label:'商机',kind:'text'}, {key:'amount',label:'商机金额',kind:'text'}, {key:'probability',label:'阶段概率',kind:'text'}, {key:'expectedClose',label:'预计关单',kind:'text'}];
      const included = index === 5 ? opportunities.filter(row => opportunityProbability(row) >= 10)
        : index === 6 ? opportunities.filter(row => opportunityProbability(row) >= 70) : opportunities;
      rows = included.map((row, rowIndex) => ({id:row.id || `opportunity:${rowIndex}`, name:row.name || '商机名称未提供',
        amount:exactMoney(row.amount), probability:`${opportunityProbability(row)}%`, expectedClose:row.expected_close_date || '待确认', can_open:false}));
    }
    details[key] = {title:metric.label, definition, periodLabel:selected.label, scopeLabel,
      unit:index === 7 ? '个' : '人民币，明细金额以元列示，保留到分', granularity, columns, rows, summary:`${metric.value} · ${metric.sub}`,
      emptyText:actual ? '所选期间没有已登记的实际汇总' : forecast ? '所选期间没有保存的预测' : '所选期间没有符合口径的在推商机',
      note:actual ? '本明细按季度汇总展示，不代表逐笔实际记录。' : '依据当前授权范围内的看板事实展示；本明细不扩展记录查看权限。'};
  });
  return details;
}

module.exports = {definitions, metricDetails};
