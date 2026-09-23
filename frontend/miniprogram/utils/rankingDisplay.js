// Format complete server aggregates. Never derive rank or cohort membership from a page of records.
function rankingDisplay(payload, group = false, money = false) {
  if (!payload || !Array.isArray(payload.rows) || !Array.isArray(payload.groups)) throw new Error('排名数据格式异常');
  const source = group ? payload.groups : payload.rows;
  const unknown = source.find(row => row.code === 'unassigned');
  const included = source.filter(row => row.code !== 'unassigned');
  const total = included.reduce((sum,row) => sum + Number(row.value),0);
  const max = Math.max(1,...included.map(row => Number(row.value)));
  return {rows:included.map(row => ({...row,key:row.user_id || row.code,
    value:Number(row.value),count:Number(row.record_count),customerCount:Number(row.customer_count),
    amount:`¥${Math.round(Number(row.value)).toLocaleString('zh-CN')}`,
    share:total ? Math.round(Number(row.value)/total*1000)/10 : 0,
    width:`${Math.round(Number(row.value)/max*100)}%`,
  })),total:money ? `¥${Math.round(total).toLocaleString('zh-CN')}` : total,
    unassignedCount:unknown ? Number(unknown.record_count) : 0,
    unassignedAmount:unknown ? `¥${Math.round(Number(unknown.value)).toLocaleString('zh-CN')}` : '¥0'};
}
module.exports={rankingDisplay};
