// New entry uses material-product-lines IDs only. Historical text is read-only.
const MAX_INTENT_PRODUCT_LINES=100;
function formFields(row){
  return {intent_product_line_ids:Array.isArray(row&&row.intent_product_line_ids)?[...row.intent_product_line_ids]:[],intent_product_lines_dirty:false};
}
function projection(form,row,catalog){
  const selected=form.intent_product_line_ids||[];
  const intents=Array.isArray(row&&row.intent_product_lines)?row.intent_product_lines:[];
  const current=new Map((catalog||[]).map(item=>[item.id,item]));
  const known=new Map([...intents,...(row&&row.product_lines||[]),...(catalog||[])].map(item=>[item.id,item]));
  const options=new Map((catalog||[]).map(item=>[item.id,{...item,selected:selected.includes(item.id)}]));
  selected.forEach(id=>{if(!options.has(id))options.set(id,{...(known.get(id)||{id,name:'已选产品线（目录未提供）',is_active:false}),selected:true});});
  const materialLines=Array.isArray(form.material_lines)?form.material_lines.filter(line=>line.is_active!==false):null;
  const derived=new Map();let missing=0;
  (materialLines||[]).forEach(line=>{
    const snapshot=line.catalog_snapshot||{},id=snapshot.product_line_id;
    if(!id){missing++;return;}
    if(!derived.has(id))derived.set(id,{...(known.get(id)||{id,code:'',name:snapshot.product_line_name||'产品线名称未提供',is_active:true})});
  });
  const materialMode=!!(materialLines&&materialLines.length)||(!materialLines&&row&&row.product_line_source==='materials');
  const productLines=materialMode?(materialLines?[...derived.values()]:(row.product_lines||[])):selected.map(id=>current.get(id)||known.get(id)||{id,name:'已选产品线（目录未提供）',is_active:false});
  const legacy=row?(row.legacy_product_line!==undefined?row.legacy_product_line:(!row.product_line_source||row.product_line_source==='legacy'?row.product_line:null)):null;
  return {opportunityProductLines:productLines,productLineOptions:[...options.values()],productLineReadonly:materialMode,
    productLineSource:materialMode?'materials':selected.length?'intent':legacy?'legacy':'none',legacyProductLine:legacy||'',
    productLineMissingCount:missing};
}
function payload(form,row){
  const ids=form.intent_product_line_ids;
  if(!form.intent_product_lines_dirty||!Array.isArray(ids))return {};
  if(ids.length>MAX_INTENT_PRODUCT_LINES)throw Error(`意向产品线最多选择 ${MAX_INTENT_PRODUCT_LINES} 条，请先取消部分选择`);
  if(ids.some(id=>typeof id!=='string'||!id))throw Error('请选择有效的意向产品线');
  const selected=[...new Set(ids)];
  const active=(form.material_lines||[]).some(line=>line.is_active!==false)||(!Array.isArray(form.material_lines)&&row&&row.product_line_source==='materials');
  // An earlier intent edit in this same draft remains an explicit user choice.
  // It is saved only while no materials are present; adding materials does not
  // silently modify the stored intent set.
  if(active)return {};
  return {intent_product_line_ids:selected};
}
module.exports={MAX_INTENT_PRODUCT_LINES,formFields,projection,payload};
