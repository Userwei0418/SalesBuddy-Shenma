const finance = require('./financialDisplay');
function dashboardProbability(row) {
  const value=finance.number(row.probability);
  if(value!==null)return value<=100?value:null;
  const stage=require('./opportunity').STAGES.find(item=>item.code===row.stage_code);
  return stage&&finance.number(stage.probability)!==null?stage.probability:null;
}
const { matchesSelectedQuarter } = require('./dashboardQuarters');

// Detail rows must be reconcilable to cents; do not inherit compact 万 labels used elsewhere.
function exactMoney(value) {
  const amount = finance.number(value);
  return amount === null ? '未登记' : `${amount.toLocaleString('zh-CN', {maximumFractionDigits:2})}元`;
}

function cents(value) {
  if (value === null) return null;
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(String(value));
  const result = match ? Number(match[1] + (match[2] || '').padEnd(2, '0')) : NaN;
  return Number.isSafeInteger(result) ? result : NaN;
}
function decimal(value) {
  if (value === null) return null;
  const digits = String(value).padStart(3, '0');
  return `${digits.slice(0, -2)}.${digits.slice(-2)}`;
}
function centsMoney(value) {
  if (value === null) return '未登记';
  const parts = decimal(value).split('.'), fraction = parts[1].replace(/0+$/, '');
  return `${parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${fraction ? `.${fraction}` : ''}元`;
}

// These are the already adopted, authorized leaves used by finance_totals.
// Never join material lines, multiply forecasts, or exclude upstream rows again.
function actualContributions(input, coverage, kind, selected, periods, opportunities) {
  const fallback = reason => ({notice:`${reason}，无法完整核对商机贡献；下方仅保留季度汇总。`, complete:false});
  if (!Array.isArray(input)) return fallback('未提供商机实际明细');
  const seen = new Set();
  for (const row of input) {
    if (!row || typeof row.id !== 'string' || !row.id || seen.has(row.id) ||
        !['recognized','collection'].includes(row.kind) || !Number.isInteger(row.year) ||
        !Number.isInteger(row.quarter) || row.quarter < 1 || row.quarter > 4 ||
        typeof row.complete !== 'boolean' || Number.isNaN(cents(row.amount)) ||
        !Number.isInteger(row.missing_material_count) || row.missing_material_count < 0 ||
        (row.complete && (row.amount === null || row.missing_material_count > 0)) ||
        (row.status === 'withdrawn' && row.amount !== null) ||
        (row.kind === 'recognized' && row.tax_unresolved === true && row.amount !== null)) return fallback('明细标识重复或数据格式异常');
    seen.add(row.id);
  }
  if (coverage && (!Number.isInteger(coverage.count) || coverage.count !== input.length ||
      coverage.basis !== 'same_authorized_effective_actual_rows')) return fallback('明细完整性信息不一致');
  const complete = !!coverage && coverage.complete === true;
  const selectedRows = input.filter(row => row.kind === kind && matchesSelectedQuarter(row, selected));
  const groups = new Map(), totals = new Map();
  for (const row of selectedRows) {
    const period = `${row.year}-Q${row.quarter}`;
    const key = JSON.stringify([row.opportunity_id ? 'opportunity' : 'historical', row.opportunity_id || row.id, period]);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
    if (!totals.has(period)) totals.set(period, {sum:null, count:0, complete:true});
    const amount = cents(row.amount), total = totals.get(period);
    if (amount !== null) { total.sum = (total.sum || 0) + amount; total.count += 1; }
    total.complete = total.complete && row.complete;
    if (total.sum !== null && !Number.isSafeInteger(total.sum)) return fallback('明细金额超出可精确核对范围');
  }
  const attributionUnknown=periods.reduce((sum,row)=>sum+(Number.isInteger(row[`${kind}_attribution_unknown_period_count`])?row[`${kind}_attribution_unknown_period_count`]:0),0);
  if (complete) {
    const periodKeys = new Set();
    for (const row of periods) {
      const key = `${row.year}-Q${row.quarter}`;
      if (periodKeys.has(key)) return fallback('季度汇总期间重复');
      periodKeys.add(key);
      const total = totals.get(key) || {sum:null,count:0,complete:false};
      const expected = row[`${kind}_confirmed_subtotal`] === undefined ? row[`${kind}_amount`] : row[`${kind}_confirmed_subtotal`];
      // Unattributed history makes the full period incomplete but is deliberately
      // excluded from the authorized leaves; amount and record counts must still match.
      const unattributed=row[`${kind}_attribution_unknown_period_count`]>0 && row[`${kind}_complete`]===false && total.complete===true;
      if (cents(expected) !== total.sum || (row[`${kind}_count`] !== undefined && row[`${kind}_count`] !== total.count) ||
          (row[`${kind}_complete`] !== undefined && row[`${kind}_complete`] !== total.complete && !unattributed)) return fallback('明细金额或登记情况与季度汇总不一致');
    }
    if ([...totals.keys()].some(key => !periodKeys.has(key))) return fallback('明细期间与季度汇总不一致');
  }
  const names = new Map(opportunities.map(row => [row.id, row.name]));
  const rows = [...groups.entries()].map(([id, facts]) => {
    const first = facts[0], amounts = facts.map(row => cents(row.amount)).filter(value => value !== null);
    const sum = amounts.length ? amounts.reduce((a,b) => a+b, 0) : null;
    const full = facts.every(row => row.complete), withdrawn = facts.every(row => row.status === 'withdrawn');
    const conflict = facts.some(row => ['needs_confirmation','stale_confirmation'].includes(row.status));
    const bucket = finance.bucket({amount:full ? decimal(sum) : null, confirmed_subtotal:decimal(sum), complete:full,
      status:withdrawn ? 'withdrawn' : conflict ? 'conflict' : full ? 'confirmed' : sum !== null ? 'partial' : 'missing',
      missing_material_count:facts.reduce((maximum,row) => Math.max(maximum,row.missing_material_count),0),
      tax_unknown:facts.some(row => row.tax_unresolved === true)});
    const sources = [...new Set(facts.map(row => ({operations:'实绩登记',entries:'历史逐笔采用值',historical:'历史期间采用值'}[row.source] || '未提供采用来源')))];
    const tax = [...new Set(facts.map(row => ({exclusive:'来源不含税',inclusive:'来源含税',unknown:'来源税别未确认',not_applicable:'来源税口径不适用'}[row.tax_basis] || '来源税别未确认')))];
    return {id, opportunity_id:first.opportunity_id || null,
      name:first.opportunity_id ? names.get(first.opportunity_id) || '历史商机／名称未提供' : '历史实际／未关联商机',
      period:`${first.year} Q${first.quarter}`, amount:withdrawn ? '已撤回' : `${bucket.isSubtotal ? '已登记小计 ' : ''}${centsMoney(sum)}`,
      quality:[bucket.statusLabel,...bucket.qualityHints].join('；'), source:sources.join('；'), tax:tax.join('；'),
      sourceType:first.opportunity_id ? '商机期间贡献' : '未关联商机的历史实际', can_open:false};
  });
  return {rows, complete, notice:complete ? '按商机和确认季度汇总已采用实际；未关联商机的历史实际单独保留。'+(attributionUnknown?`${attributionUnknown} 项历史季度记录归属待核对，未计入个人或部门业绩。`:'')
    : '实际明细尚未完整提供，下方仅列已返回的贡献，无法完整核对总卡。'};
}

const definitions = [
  ['actual_recognized_exclusive', '采用的实际不含税确收，按确认期间汇总；部分登记仅展示已知小计，不将未知金额补为零。', 'period_aggregate'],
  ['actual_collection', '采用的正式回款，按确认期间汇总；已登记零与未登记分别展示。', 'period_aggregate'],
  ['forecast_recognized_inclusive', '销售最后保存的全部季度含税确收计划，不筛阶段、不乘阶段概率；赢单、丢单后的已填计划仍保留，与上方阶段至少70%的预测确收口径不同。', 'forecast'],
  ['forecast_collection', '销售最后保存的全部季度回款计划，不筛回款信心、不乘阶段概率；赢单、丢单后的已填计划仍保留，与上方高信心预测回款口径不同。', 'forecast'],
  ['opportunity_acv', '预计关单日在所选季度的在推商机金额合计，不含已成单和已丢单。', 'opportunity'],
  ['weighted_opportunity_acv', '同范围在推商机金额乘阶段概率后合计，包含10%阶段；不代替销售填写的季度预测。', 'opportunity'],
  ['high_probability_acv', '同范围在推商机中，阶段概率至少70%的商机金额合计。', 'opportunity'],
  ['expected_close_count', '预计关单日在所选季度的在推商机数量，不代表已成交数量。', 'opportunity'],
];

// Formatting only: totals come from the existing financialMetrics result.
function metricDetails({kpis, opportunities, quarterRows, actualRows, actualDetails, actualCoverage, actualOpportunities = opportunities, selected, scopeLabel}) {
  const names = new Map(opportunities.map(row => [row.id, row.name]));
  const details = {};
  definitions.forEach(([key, definition, granularity], index) => {
    const metric = kpis[index];
    metric.key = key;
    const actual = index < 2, forecast = index >= 2 && index < 4;
    const kind = index % 2 === 0 ? 'recognized' : 'collection';
    let columns, rows, contribution;
    if (actual) {
      contribution = actualContributions(actualDetails, actualCoverage, kind, selected, actualRows, actualOpportunities);
      columns = contribution.rows ? [{key:'name',label:'商机或历史实际',kind:'text'}, {key:'period',label:'确认期间',kind:'text'},
        {key:'amount',label:kind === 'recognized' ? '实际不含税贡献' : '实际回款贡献',kind:'text'},
        {key:'quality',label:'登记情况',kind:'text'}, {key:'source',label:'采用来源',kind:'text'}, {key:'tax',label:'来源税口径',kind:'text'}]
        : [{key:'period',label:'确认期间',kind:'text'}, {key:'amount',label:'采用金额',kind:'text'}, {key:'quality',label:'登记情况',kind:'text'}];
      rows = contribution.rows || actualRows.map(row => {
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
      const included = index === 5 ? opportunities.filter(row => dashboardProbability(row) >= 10)
        : index === 6 ? opportunities.filter(row => dashboardProbability(row) >= 70) : opportunities;
      rows = included.map((row, rowIndex) => ({id:row.id || `opportunity:${rowIndex}`, name:row.name || '商机名称未提供',
        amount:exactMoney(row.amount), probability:dashboardProbability(row)===null?'未填写':`${dashboardProbability(row)}%`, expectedClose:row.expected_close_date || '待确认', can_open:false}));
    }
    details[key] = {title:metric.label, definition, periodLabel:selected.label, scopeLabel,
      unit:index === 7 ? '个' : '人民币，明细金额以元列示，保留到分', granularity:contribution && contribution.rows ? 'opportunity_period' : granularity,
      columns, rows, summary:`${metric.value} · ${metric.sub}`,
      ...(actual ? {coverageComplete:contribution.complete} : {}),
      emptyText:actual ? contribution.complete ? '所选期间没有此项采用的实际明细' : '未取得可核对的实际明细' : forecast ? '所选期间没有保存的预测' : '所选期间没有符合口径的在推商机',
      note:actual ? `${contribution.notice} ${kind === 'recognized' ? '贡献金额采用不含税值，未自行换算来源含税金额。' : '回款贡献采用后端已确认金额，不从物料确收或预测推算。'} 本明细不扩展记录查看权限。` : '依据当前授权范围内的看板事实展示；本明细不扩展记录查看权限。'};
  });
  return details;
}

module.exports = {definitions, metricDetails, dashboardProbability};
