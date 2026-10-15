// Read only FINANCE001 adopted facts. Raw evidence and old decisions are never added to these values.
function number(value) {
  if(value===null||value===undefined||typeof value==='boolean'||String(value).trim()==='')return null;
  const n=Number(value);return Number.isFinite(n)&&n>=0?n:null;
}
function money(value) {
  const n=number(value);if(n===null)return '未登记';
  return n>=10000?`${(n/10000).toLocaleString('zh-CN',{maximumFractionDigits:2})}万`:`${n.toLocaleString('zh-CN',{maximumFractionDigits:2})}元`;
}
function hints(row={}) {
  const out=[];
  if(row.status==='partial'||row.partial_period_count>0)out.push('仅显示已登记小计，实际尚未完整');
  if(row.missing_material_count>0||row.recognized_missing_material_count>0)out.push(`尚有 ${row.missing_material_count||row.recognized_missing_material_count} 行物料未登记`);
  if(row.tax_unknown||row.tax_unknown_period_count>0)out.push('采用来源的税口径未确认，无法给出完整不含税确收');
  if(row.conflict_period_count>0||row.status==='conflict')out.push('实际来源待运营核对');
  if(row.reference_changed)out.push('参考资料有变化，运营已确认值保留');
  if(row.period_precision_warning==='not_allocated_to_days'||row.unallocated_period_count>0){
    out.push('部分季度金额无法分配到所选日期，未计入本次合计');
    if(number(row.period_only_recognized_amount)!==null)out.push(`待分配季度确收 ${money(row.period_only_recognized_amount)}`);
    if(number(row.period_only_collection_amount)!==null)out.push(`待分配季度回款 ${money(row.period_only_collection_amount)}`);
  }
  if(row.complete===false&&!out.length&&!['missing','withdrawn'].includes(row.status))out.push('实际尚未完整登记');
  return out;
}
const sourceLabels={operations:'运营确认',entries:'历史逐笔采用值',historical:'历史期间采用值'};
const statusLabels={missing:'未登记',confirmed:'已确认',resolved:'已采用',auto:'已采用',single_source:'已采用',partial:'部分登记',withdrawn:'已撤回',conflict:'待核对'};
function bucket(row={}) {
  // Source/quality status is independent of the amount. A single adopted source
  // may have an exclusive subtotal while its full amount is unknown.
  const amount=number(row.amount),subtotal=number(row.confirmed_subtotal)!==null?row.confirmed_subtotal:row.known_exclusive_subtotal;
  const status=row.status||(amount!==null?'confirmed':number(subtotal)!==null?'partial':'missing');
  const displayAmount=status==='withdrawn'?null:amount!==null?row.amount:number(subtotal)!==null?subtotal:null;
  const isSubtotal=displayAmount!==null&&(amount===null||row.complete===false||status==='partial');
  const amountText=status==='withdrawn'?'已撤回':(isSubtotal?'已登记小计 ':'')+money(displayAmount);
  return {...row,status,displayAmount,isSubtotal,valueText:status==='withdrawn'?'已撤回':money(displayAmount),amountText,sourceLabel:sourceLabels[row.source]||'未采用来源',statusLabel:statusLabels[status]||'待核对',qualityHints:hints({...row,...(isSubtotal?{partial_period_count:Math.max(1,row.partial_period_count||0)}:{})}),
    confirmations:(row.confirmations||[]).map((item,index)=>{
      const snapshot=item.material_snapshot||{},line=snapshot.material_line||{},catalog=line.catalog_snapshot||{};
      return {...item,displayKey:item.id||`${item.material_line_id||'collection'}-${index}`,amountText:item.status==='withdrawn'?'已撤回':money(item.amount),
        materialLabel:catalog.name||line.material_name||snapshot.name||(item.material_line_id?'物料行':'商机回款'),statusLabel:statusLabels[item.status]||'待核对'};
    })};
}
function totalLabel(row={},key) {
  return aggregate(row,key.replace('_amount','')).amountText;
}
function aggregate(row={},kind) {
  const value=row[`${kind}_amount`],subtotal=row[`${kind}_confirmed_subtotal`];
  const normalized={kind,amount:value,confirmed_subtotal:subtotal,complete:row[`${kind}_complete`],
    known_exclusive_subtotal:kind==='recognized'?row.known_exclusive_subtotal:undefined,
    missing_material_count:row[`${kind}_missing_material_count`],tax_unknown_period_count:row[`${kind}_tax_unknown_period_count`],
    conflict_period_count:row[`${kind}_conflict_period_count`],partial_period_count:row[`${kind}_incomplete_period_count`],
    period_precision_warning:row.period_precision_warning,unallocated_period_count:row.unallocated_period_count,
    period_only_recognized_amount:row.period_only_recognized_amount,period_only_collection_amount:row.period_only_collection_amount,
    status:number(value)!==null?'confirmed':number(subtotal)!==null?'partial':'missing'};
  return bucket(normalized);
}
module.exports={number,money,hints,bucket,totalLabel,aggregate};
