// Format complete server aggregates. Never derive rank or cohort membership from a page of records.
function rankingDisplay(payload, group = false, money = false) {
  if (!payload || !Array.isArray(payload.rows) || !Array.isArray(payload.groups)) throw new Error('排名数据格式异常');
  const source = group ? payload.groups : payload.rows;
  const unknown = source.find(row => row.code === 'unassigned');
  const included = source.filter(row => row.code !== 'unassigned');
  const perCapita = payload.calculation === 'team_followup_per_capita_v1';
  if (perCapita && included.some(row => !Number.isInteger(row.member_count) || row.member_count < 0 || !Array.isArray(row.members) || (row.member_count > 0 && (!Number.isFinite(Number(row.value)) || row.average == null)) || (row.member_count === 0 && (row.value !== null || row.rank !== null)))) throw new Error('团队人均排名数据不完整');
  const total = included.reduce((sum,row) => sum + Number(perCapita ? row.record_count : row.value),0);
  const max = Math.max(1,...included.map(row => Number(row.value)));
  const moneyLabel=value=>`¥${Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2})}`;
  const rankIncomplete=money&&included.some(row=>row.missing_amount_count>0);
  const aggregateMoney=rows=>{
    const missing=rows.reduce((sum,row)=>sum+(row.missing_amount_count||0),0);
    const known=rows.reduce((sum,row)=>sum+(Number.isInteger(row.known_amount_count)?row.known_amount_count:row.value==null?0:1),0);
    if(missing&&!known)return '未填写';
    return `${missing?'已知小计 ':''}${moneyLabel(rows.reduce((sum,row)=>sum+Number(row.value||0),0))}`;
  };
  return {rows:included.map(row => ({...row,key:row.user_id || row.code,
    value:money&&row.missing_amount_count>0&&row.known_amount_count===0?null:Number(row.value),count:Number(row.record_count),customerCount:Number(row.customer_count),
    amount:money&&row.missing_amount_count>0&&row.known_amount_count===0?'未填写':`${money&&row.missing_amount_count>0?'已知小计 ':''}${moneyLabel(row.value)}`,
    rank:money&&row.missing_amount_count>0&&row.known_amount_count===0?null:row.rank,
    rankProvisional:rankIncomplete,amountNote:money&&row.missing_amount_count>0?`${row.missing_amount_count} 个商机 ACV 未填写`:'',
    share:total ? Math.round(Number(perCapita ? row.record_count : row.value)/total*1000)/10 : 0,
    width:`${money&&row.missing_amount_count>0&&row.known_amount_count===0?0:Math.round(Number(row.value)/max*100)}%`,
  })),total:money ? aggregateMoney(included) : total,
    unassignedCount:unknown ? Number(unknown.record_count) : 0,
    unassignedAmount:unknown ? aggregateMoney([unknown]) : '¥0'};
}
module.exports={rankingDisplay};
