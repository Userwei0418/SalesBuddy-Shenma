const { amountText } = require('./customerDetail');
const { beijingDateParts } = require('./opportunityQuarter');

// The database groups confirmed ledger entries by their business date. Never
// download all entries or substitute forecast amounts for actual amounts.
async function loadQuarterActuals(api, filters, asOf) {
  const result=await api.getCustomerAssetQuarters({...filters,as_of:asOf});
  if(!result||!Array.isArray(result.items)||!Array.isArray(result.years)||result.as_of!==asOf)throw Error('季度实绩响应不完整');
  const current=beijingDateParts(asOf),groups={},years=new Set([current.year]);
  for(const year of result.years){if(!Number.isInteger(year)||year<1900||year>9999)throw Error('实绩年份无效');years.add(year);}
  for(const row of result.items){
    if(!Number.isInteger(row.year)||!Number.isInteger(row.quarter)||row.quarter<1||row.quarter>4||!years.has(row.year))throw Error('实绩季度无效');
    const key=`${row.year}-Q${row.quarter}`;
    if(groups[key])throw Error('实绩季度重复');
    if(!Number.isInteger(row.entry_count)||row.entry_count<0)throw Error('实绩记录数无效');
    const group={count:row.entry_count};
    for(const kind of ['collection','recognized']){
      const value=row[`${kind}_amount`],count=row[`${kind}_count`];
      if(!Number.isInteger(count)||count<0||(count===0?value!==null:(!['number','string'].includes(typeof value)||String(value).trim()===''||!Number.isFinite(Number(value))||Number(value)<0)))throw Error('实绩金额或记录数无效');
      if(count>0)group[kind]=Number(value);
    }
    if(row.collection_count+row.recognized_count!==row.entry_count)throw Error('实绩记录数不一致');
    groups[key]=group;
  }
  const options=[...years].sort((a,b)=>b-a).flatMap(year=>[1,2,3,4].map(quarter=>({key:`${year}-Q${quarter}`,label:`${year} Q${quarter}`})));
  return {groups,options,currentKey:`${current.year}-Q${current.quarter}`};
}
function quarterActualDisplay(groups,key) {
  const group=groups[key]||{};
  return {quarterCollection:group.collection===undefined?'未登记':amountText(group.collection),quarterRecognized:group.recognized===undefined?'未登记':amountText(group.recognized),quarterEntryCount:group.count||0};
}
module.exports={loadQuarterActuals,quarterActualDisplay};
