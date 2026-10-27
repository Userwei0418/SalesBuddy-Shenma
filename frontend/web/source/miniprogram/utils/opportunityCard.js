const finance=require('./financialDisplay');
function cardFields(item) {
  const actuals=item.actuals;
  const label=key=>actuals&&Object.prototype.hasOwnProperty.call(actuals,key)?finance.totalLabel(actuals,key):'加载中';
  return {recognizedLabel:label('recognized_amount'),collectionLabel:label('collection_amount'),financialHints:[...new Set(['recognized','collection'].flatMap(kind=>finance.aggregate(actuals||{},kind).qualityHints))],
    productLineLabel:typeof item.product_line==='string'&&item.product_line.trim()?item.product_line.trim():'未填写'};
}
// Missing list projections are read from the same adopted financial overview, never rebuilt from raw evidence.
async function loadCardActuals(api,opportunities) {
  const result=opportunities.slice();let cursor=0;
  await Promise.all(Array.from({length:Math.min(4,result.length)},async()=>{
    while(cursor<result.length){
      const index=cursor++,item=result[index];
      if(item.actuals&&['recognized_amount','collection_amount'].every(key=>Object.prototype.hasOwnProperty.call(item.actuals,key))){result[index]={...item,...cardFields(item)};continue;}
      try{
        const r=await api.getOpportunityFinancialOverview(item.id);
        if(!r||r.schema_version!==2||r.opportunity_id!==item.id||r.customer_id!==item.customer_id||!r.totals||!r.totals.recognized||!r.totals.collection)throw Error('实际汇总不完整');
        const recognized=finance.bucket({...r.totals.recognized,status:r.totals.recognized.status||(r.totals.recognized.partial_period_count?'partial':undefined)});
        const collection=finance.bucket({...r.totals.collection,status:r.totals.collection.status||(r.totals.collection.partial_period_count?'partial':undefined)});
        result[index]={...item,recognizedLabel:recognized.amountText,collectionLabel:collection.amountText,financialHints:[...new Set([...recognized.qualityHints,...collection.qualityHints])]};
      }catch(_){result[index]={...item,recognizedLabel:'暂不可用',collectionLabel:'暂不可用'};}
    }
  }));return result;
}
module.exports={cardFields,loadCardActuals};
