const { amountText } = require('./customerDetail');

function cardFields(item) {
  const actuals = item.actuals;
  const amount = key => actuals && Object.prototype.hasOwnProperty.call(actuals,key)
    ? (actuals[key] === null ? '未登记' : amountText(actuals[key])) : '加载中';
  return {
    recognizedLabel: amount('recognized_amount'), collectionLabel: amount('collection_amount'),
    productLineLabel: typeof item.product_line === 'string' && item.product_line.trim() ? item.product_line.trim() : '未填写',
  };
}

async function allAssets(api, filters, expectedView) {
  const rows = [];
  for (let page = 0; page < 100; page += 1) {
    const result = await api.getCustomerAssets({ period: 'all', page_size: 100, offset: rows.length, ...filters });
    if (!result || result.view !== expectedView || !Array.isArray(result.items)) throw new Error('实际记录格式不完整');
    rows.push(...result.items);
    if (result.has_more === false) return rows;
    if (result.has_more !== true || !result.items.length) throw new Error('实际记录分页不完整');
  }
  throw new Error('实际记录超出读取上限');
}

// Only confirmed actual entries linked to this opportunity count. Never allocate
// customer-level amounts or quarterly forecasts to an opportunity.
// BACKEND-CONTRACT GET /api/v1/customer-assets：先 view=customers，再按客户读 view=entries，offset 分页。
// amount 单位元；此函数依赖接口只返回确认且未作废记录，未在本地再次检查 confirmed/voided。
// 后端建议提供商机级实绩汇总接口以减少每客户一次分页请求；尚未接入，勿将此建议写成现有 API。
async function loadCardActuals(api, opportunities) {
  if (!opportunities.length) return [];
  if (opportunities.every(item => item.actuals && ['recognized_amount','collection_amount'].every(key => Object.prototype.hasOwnProperty.call(item.actuals,key)))) {
    return opportunities.map(item => ({ ...item, ...cardFields(item) }));
  }
  const unavailable = item => ({ ...item, recognizedLabel: '暂不可用', collectionLabel: '暂不可用' });
  try {
    const customers = await allAssets(api, {}, 'customers');
    const available = new Set(customers.map(row => row.customer_id));
    const ids = [...new Set(opportunities.map(item => item.customer_id).filter(id => id && available.has(id)))];
    const totals = new Map();
    const failed = new Set();
    let cursor = 0;
    await Promise.all(Array.from({ length: Math.min(4, ids.length) }, async () => {
      while (cursor < ids.length) {
        const customerId = ids[cursor++];
        try {
          const entries = await allAssets(api, { customer_id: customerId }, 'entries');
          const byOpportunity = new Map();
          for (const entry of entries) {
            if (entry.customer_id !== customerId || !entry.opportunity_id) continue;
            if (!['recognized', 'collection'].includes(entry.kind)) continue;
            if (!['number', 'string'].includes(typeof entry.amount) || String(entry.amount).trim() === '' || !Number.isFinite(Number(entry.amount))) throw new Error('实际金额无效');
            const total = byOpportunity.get(entry.opportunity_id) || {};
            total[entry.kind] = (total[entry.kind] || 0) + Number(entry.amount);
            byOpportunity.set(entry.opportunity_id, total);
          }
          totals.set(customerId, byOpportunity);
        } catch (_) { failed.add(customerId); }
      }
    }));
    return opportunities.map(item => {
      if (!item.customer_id || failed.has(item.customer_id)) return unavailable(item);
      const total = (totals.get(item.customer_id) || new Map()).get(item.id) || {};
      return {
        ...item,
        recognizedLabel: total.recognized === undefined ? '未登记' : amountText(total.recognized),
        collectionLabel: total.collection === undefined ? '未登记' : amountText(total.collection),
      };
    });
  } catch (_) { return opportunities.map(unavailable); }
}

module.exports = { cardFields, loadCardActuals, allAssets };
