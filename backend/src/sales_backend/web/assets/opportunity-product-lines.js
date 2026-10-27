import {esc} from './core.js';

export function productLineFields(row,directory) {
  const legacy=row.legacy_product_line;
  const history=legacy?`<p class="help">历史产品线原文：${esc(legacy)}（原文保留，未自动映射）</p>`:'';
  if(row.product_line_source==='materials')return '<fieldset class="field full"><legend>产品线（由交易物料确定）</legend><p>'+esc((row.product_lines||[]).map(line=>line.name).join('、')||'未提供')+'</p><p class="help">支持跨产品线的多个物料；请在交易物料中调整归属，不能另外填写矛盾的产品线。</p>'+history+'</fieldset>';
  const chosen=new Set(row.intent_product_line_ids||[]),lines=new Map((directory.items||[]).filter(line=>line.is_active).map(line=>[line.id,line]));
  for(const line of row.intent_product_lines||[])if(chosen.has(line.id)&&!lines.has(line.id))lines.set(line.id,line);
  // Preserve explicit historical IDs even if their metadata cannot be read.
  for(const id of chosen)if(!lines.has(id))lines.set(id,{id,name:'已选择的历史意向产品线',is_active:false});
  return '<fieldset class="field full"><legend>意向产品线（可多选）</legend><p class="help">尚无交易物料时可从统一目录选择。添加物料后按物料归属显示，原意向会单独保留。</p>'+
    [...lines.values()].map(line=>`<label class="check"><input type="checkbox" name="intent_product_line_ids" value="${esc(line.id)}" ${chosen.has(line.id)?'checked':''}>${esc(line.name)}${line.is_active===false?'（已停用，可保留或移除）':''}</label>`).join('')+
    (!lines.size?'<p class="help">目录暂无可用产品线，请由运营维护。</p>':'')+history+'</fieldset>';
}

export function productLinePayload(form,row) {
  if(row.product_line_source==='materials')return {};
  const selected=[...new Set(form.getAll('intent_product_line_ids').map(String))];
  const previous=row.intent_product_line_ids||[];
  return JSON.stringify([...selected].sort())===JSON.stringify([...previous].sort())?{}:{intent_product_line_ids:selected};
}
