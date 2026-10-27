// FINANCE001: transaction rows have stable identities; catalog prices are references.
const RELATIONS = [{value:'direct',label:'直客交易'},{value:'distributor',label:'经销商交易'},{value:'downstream',label:'下游终端交易'}];
const USAGES = [{value:'self_use',label:'自用'},{value:'resale',label:'转售'}];
let nextKey = 0;
const empty = value => value === null || value === undefined || String(value).trim() === '';
const text = value => empty(value) ? '' : String(value);
const FIELDS = ['quantity','unit_price','expected_delivery_on','expected_start_on','expected_oa_on','service_months','expected_account_count','expected_consumption_amount','expected_consumption_year','expected_consumption_quarter','note'];
function lineFor(row) {
  const result={client_key:row.client_key || row.id || `new-material-${Date.now()}-${++nextKey}`,id:row.id,version_no:row.version_no,material_id:row.material_id,
    catalog_snapshot:row.catalog_snapshot || {},usage:row.usage || '',is_active:row.is_active !== false};
  FIELDS.forEach(key=>{result[key]=text(row[key]);});
  return decorate(result);
}
function decimal(value, field, scale, whole, positive=false) {
  const raw=text(value).trim();
  if(!new RegExp(`^\\d+(?:\\.\\d{1,${scale}})?$`).test(raw))throw Error(`${field}请输入非负数字，最多${scale}位小数`);
  const [a,b='']=raw.split('.'),normalized=a.replace(/^0+(?=\d)/,'');
  if(normalized.length>whole || (positive && !/[1-9]/.test(normalized+b)))throw Error(`${field}超出范围${positive?'或未大于0':''}`);
  return normalized+(b?'.'+b:'');
}
function integer(value,field,min,max) {
  if(empty(value))return null;
  if(!/^\d+$/.test(String(value)) || Number(value)<min || Number(value)>max)throw Error(`${field}请输入${min}至${max}的整数`);
  return Number(value);
}
function date(value,field) {
  if(empty(value))return null;
  const raw=String(value),d=new Date(raw+'T00:00:00Z');
  if(!/^\d{4}-\d{2}-\d{2}$/.test(raw)||!Number.isFinite(d.getTime())||d.toISOString().slice(0,10)!==raw)throw Error(`${field}不是有效日期`);
  return raw;
}
// Decimal string multiplication avoids binary rounding and is supported by WeChat without BigInt.
function lineTotal(quantity,price) {
  const q=decimal(quantity,'数量',4,12,true),p=decimal(price,'单价',2,16);
  const a=q.replace('.',''),b=p.replace('.','');
  const scale=(q.split('.')[1]||'').length+(p.split('.')[1]||'').length;
  const digits=Array(a.length+b.length).fill(0);
  for(let i=a.length-1;i>=0;i--)for(let j=b.length-1;j>=0;j--)digits[i+j+1]+=Number(a[i])*Number(b[j]);
  for(let i=digits.length-1;i>0;i--){digits[i-1]+=Math.floor(digits[i]/10);digits[i]%=10;}
  let raw=digits.join('').replace(/^0+(?=\d)/,'').padStart(scale+1,'0');
  if(scale>2){const cut=raw.length-scale+2,round=Number(raw[cut])>=5;raw=raw.slice(0,cut);if(round){const ds=raw.split('').map(Number);let carry=1;for(let i=ds.length-1;i>=0&&carry;i--){ds[i]++;carry=ds[i]>9?1:0;ds[i]%=10;}raw=(carry?'1':'')+ds.join('');}}
  else raw+= '0'.repeat(2-scale);
  raw=raw.padStart(3,'0');return raw.slice(0,-2)+'.'+raw.slice(-2);
}
function decorate(line) {
  const snapshot=line.catalog_snapshot||{};let total='—';
  try{total=lineTotal(line.quantity,line.unit_price);}catch(_){}
  const discount=!empty(line.unit_price)&&Number(line.unit_price)>=0&&Number(snapshot.catalog_price)>0?`${(Number(line.unit_price)/Number(snapshot.catalog_price)*100).toFixed(2)}%`:'';
  const rule=snapshot.recognition_rule||'manual';
  return {...line,discount_text:discount,materialLabel:snapshot.name||'物料名称未提供',rule,
    inputVisibility:{delivery:rule==='delivery',monthly:rule==='monthly',oa:rule==='monthly'&&snapshot.rule_parameters?.oa_required===true,consumption:rule==='consumption',accounts:rule==='monthly'},
    productLineLabel:snapshot.product_line_name||'未分产品线',unitLabel:snapshot.unit||'单位未维护',line_amount_text:total};
}
function fromCatalog(item) {
  // Price and usage require an explicit sales input; never seed an actual amount.
  return lineFor({material_id:item.id,catalog_snapshot:{...item,material_id:item.id},quantity:'1',unit_price:'',usage:''});
}
function formFields(row) {
  return {trade_relation:row&&row.trade_relation||'',upstream_opportunity_id:row&&row.upstream_opportunity_id||'',upstream_opportunity_name:row&&row.upstream_opportunity_name||'',
    material_lines:row&&Array.isArray(row.material_lines)?row.material_lines.filter(x=>x.is_active!==false).map(lineFor):row?undefined:[],materials_dirty:false};
}
function payload(form) {
  const lines=form.material_lines;
  if(lines === undefined || lines === null)return {};
  if(!Array.isArray(lines)||lines.length>200)throw Error('物料行最多200条');
  const relation=form.trade_relation||null;
  if((lines.length||relation)&&!RELATIONS.some(r=>r.value===relation))throw Error('请选择本商机的交易关系');
  if(relation==='downstream'&&!form.upstream_opportunity_id)throw Error('请选择上游经销商商机');
  const ids=new Set();
  const values=lines.map((line,index)=>{
    const prefix=`第${index+1}行：`;
    try{
      if(!line.material_id)throw Error('请选择目录物料');
      if(line.id && ids.has(line.id))throw Error('交易物料行重复');if(line.id)ids.add(line.id);
      if(!USAGES.some(u=>u.value===line.usage))throw Error('请选择自用或转售');
      const year=integer(line.expected_consumption_year,'预计消耗年份',2000,2100),quarter=integer(line.expected_consumption_quarter,'预计消耗季度',1,4);
      if((year===null)!==(quarter===null))throw Error('预计消耗年份和季度需同时填写');
      if(text(line.note).length>1000)throw Error('物料备注最多1000字');
      const version=line.catalog_snapshot&&line.catalog_snapshot.version_no;
      if(version!=null&&(!Number.isInteger(version)||version<1))throw Error('物料目录版本无效，请核对目录后重试');
      if(version==null&&!line.id)throw Error('物料目录版本缺失，请刷新目录后重新选择');
      // New and edited rows bind saving to the same adopted rules as preview.
      // Only genuinely versionless persisted history uses the legacy omission.
      return {...(line.id?{id:line.id}:{}),...(version==null?{}:{catalog_version_no:version}),material_id:line.material_id,quantity:decimal(line.quantity,'数量',4,12,true),unit_price:decimal(line.unit_price,'成交含税单价',2,16),usage:line.usage,
        expected_delivery_on:date(line.expected_delivery_on,'预计交付日期'),expected_start_on:date(line.expected_start_on,'预计服务开始日期'),expected_oa_on:date(line.expected_oa_on,'预计OA日期'),
        service_months:integer(line.service_months,'服务月数',1,120),expected_account_count:integer(line.expected_account_count,'预计账号数',0,100000000),
        expected_consumption_amount:empty(line.expected_consumption_amount)?null:decimal(line.expected_consumption_amount,'预计消耗金额',2,16),expected_consumption_year:year,expected_consumption_quarter:quarter,note:text(line.note).trim()};
    }catch(error){throw Error(prefix+error.message);}
  });
  // An untouched legacy record with no modeled relation stays unmodeled.
  return {...(relation?{trade_relation:relation,upstream_opportunity_id:relation==='downstream'?form.upstream_opportunity_id:null}:{}),material_lines:values};
}
// Incomplete drafts are valid preview inputs. Missing values stay null, including
// zero-valued fields; saving still uses the stricter payload above.
function draftPayload(form) {
  const lines=form.material_lines;
  if(!Array.isArray(lines))throw Error('物料尚未读取，请重试后试算');
  if(lines.length>200)throw Error('物料行最多200条');
  const keys=new Set(),ids=new Set();
  const relation=form.trade_relation||null;
  if(!RELATIONS.some(r=>r.value===relation))throw Error('请选择交易关系后查看试算');
  if(relation==='downstream'&&!form.upstream_opportunity_id)throw Error('请选择上游经销商商机后查看试算');
  return {trade_relation:relation,upstream_opportunity_id:relation==='downstream'?(form.upstream_opportunity_id||null):null,
    material_lines:lines.map((line,index)=>{
      try{
        if(!line.client_key||keys.has(line.client_key))throw Error('物料草稿行标识重复或缺失，请重新打开表单');
        keys.add(line.client_key);
        if(line.id&&ids.has(line.id))throw Error('交易物料行重复');if(line.id)ids.add(line.id);
        const version=line.catalog_snapshot&&line.catalog_snapshot.version_no;
        if(line.material_id&&(!Number.isInteger(version)||version<1))throw Error('物料目录版本缺失，请刷新目录后重新选择');
        if(line.usage&&!USAGES.some(u=>u.value===line.usage))throw Error('请选择自用或转售');
        if(text(line.note).length>1000)throw Error('物料备注最多1000字');
        return {client_key:line.client_key,...(line.id?{id:line.id}:{}),material_id:line.material_id||null,catalog_version_no:version??null,
          quantity:empty(line.quantity)?null:decimal(line.quantity,'数量',4,12,true),unit_price:empty(line.unit_price)?null:decimal(line.unit_price,'成交含税单价',2,16),usage:line.usage||null,
          expected_delivery_on:date(line.expected_delivery_on,'预计交付日期'),expected_start_on:date(line.expected_start_on,'预计服务开始日期'),expected_oa_on:date(line.expected_oa_on,'预计OA日期'),
          service_months:integer(line.service_months,'服务月数',1,120),expected_account_count:integer(line.expected_account_count,'预计账号数',0,100000000),
          expected_consumption_amount:empty(line.expected_consumption_amount)?null:decimal(line.expected_consumption_amount,'预计消耗金额',2,16),
          expected_consumption_year:integer(line.expected_consumption_year,'预计消耗年份',2000,2100),expected_consumption_quarter:integer(line.expected_consumption_quarter,'预计消耗季度',1,4),note:text(line.note).trim()};
      }catch(error){throw Error(`第${index+1}行：${error.message}`);}
    })};
}
module.exports={RELATIONS,USAGES,FIELDS,lineFor,fromCatalog,formFields,decorate,lineTotal,payload,draftPayload};
