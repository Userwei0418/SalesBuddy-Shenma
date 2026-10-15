const {beijingDateParts}=require('./opportunityQuarter');
const {bucket,number}=require('./financialDisplay');

// One opportunity's adopted period facts; never combine entries/history/confirmations again.
async function loadQuarterActuals(api,filters,asOf) {
  const result=await api.getOpportunityFinancialOverview(filters.opportunity_id);
  if(!result||result.schema_version!==2||result.opportunity_id!==filters.opportunity_id||result.customer_id!==filters.customer_id||!Array.isArray(result.periods))throw Error('财务期间响应不完整，请使用配套版本');
  const current=beijingDateParts(asOf),groups={},years=new Set([current.year]);
  for(const row of result.periods){
    if(!Number.isInteger(row.year)||row.year<2000||row.year>2100||!Number.isInteger(row.quarter)||row.quarter<1||row.quarter>4||!['recognized','collection'].includes(row.kind))throw Error('财务期间无效');
    for(const field of ['amount','confirmed_subtotal','known_exclusive_subtotal'])if(row[field]!=null&&number(row[field])===null)throw Error('实绩金额无效');
    const key=`${row.year}-Q${row.quarter}`;years.add(row.year);
    if(!groups[key])groups[key]={year:row.year,quarter:row.quarter};
    if(groups[key][row.kind])throw Error('财务期间重复');
    groups[key][row.kind]=bucket(row);
  }
  const options=[...years].sort((a,b)=>b-a).flatMap(year=>[1,2,3,4].map(quarter=>({key:`${year}-Q${quarter}`,label:`${year} Q${quarter}`})));
  return {groups,options,currentKey:`${current.year}-Q${current.quarter}`};
}
function quarterActualDisplay(groups,key) {
  const group=groups[key]||{},recognized=group.recognized||bucket(),collection=group.collection||bucket();
  return {quarterCollection:collection.amountText,quarterRecognized:recognized.amountText,
    quarterEntryCount:[recognized,collection].reduce((n,row)=>n+row.confirmations.filter(x=>x.status==='confirmed').length,0),
    quarterFinancial:{year:group.year||Number(key.split('-')[0]),quarter:group.quarter||Number(key.split('Q')[1]),recognized,collection},
    quarterActualHint:[...new Set([...recognized.qualityHints,...collection.qualityHints])].join('；')};
}
module.exports={loadQuarterActuals,quarterActualDisplay};
