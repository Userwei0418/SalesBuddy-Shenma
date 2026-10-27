import {state,api,esc,field,options,dialog,table,pager,head,query,filters,search,bindCommon,hasPermission,requirePermission,toast,readForDialog} from './core.js';
import {yuan} from './opportunity-finance.js';

const rules=[['manual','销售自行填写参考'],['delivery','按预计交付季度'],['monthly','按自然月分摊'],['consumption','按预计消耗期间']];
const ruleGuidance={
  manual:{scenario:'参考无法由目录规则确定时，由销售依据业务计划填写。',inputs:'不自动计算参考金额；销售仍需明确填写季度预测。'},
  delivery:{scenario:'一次性交付，并在预计交付季度计入全部成交额。',inputs:'交易数量、成交单价、用途、预计交付日。'},
  monthly:{scenario:'覆盖多个月份的服务，按自然月等额分摊，余分补在最后月份。',inputs:'交易数量、成交单价、用途、预计开始日、服务月数（或目录默认月数）、分摊方式；要求 OA 时另填预计 OA 日，并配置截止日与延期方式。'},
  consumption:{scenario:'按明确季度的预计消耗金额计入，不用充值额或成交总额替代。',inputs:'交易数量、成交单价、用途、预计消耗含税金额、消耗年份与季度。'},
};
export function materialRuleGuide(rule) {
  const guide=ruleGuidance[rule];
  return guide?`<p><strong>适用场景：</strong>${esc(guide.scenario)}</p><p><strong>计算所需输入：</strong>${esc(guide.inputs)}</p><p class="help">只使用固定规则和结构化参数；业务说明不执行公式。数量乘成交单价一次，月数与账号数不再乘入成交总额。</p>`:'<p class="notice">请选择已支持的固定规则。</p>';
}

const businessTextFields=[['description','业务说明',1000],['applicability','适用范围',2000],['exceptions','异常与例外说明',2000]];
const normalizeDefinition=value=>({description:value?.description??'',applicability:value?.applicability??'',exceptions:value?.exceptions??'',
  examples:(value?.examples||[]).map(item=>({scenario:item.scenario??'',explanation:item.explanation??''})).filter(item=>item.scenario!==''||item.explanation!=='')});

export function materialBusinessFields(definition,{historical=false}={}) {
  const value=normalizeDefinition(definition);
  // HTML strips one initial textarea newline; prefix one so user text survives.
  const textarea=(label,name,text,max)=>`<label class="field full"><b>${esc(label)}</b><textarea name="${esc(name)}" maxlength="${max}">\n${esc(text)}</textarea></label>`;
  return '<fieldset><legend>物料业务说明（可选）</legend><input type="hidden" name="business_definition_editor" value="1">'+
    '<p class="notice">这里保存人工业务解释与场景说明，不参与金额计算。参考金额仍按结构化规则生成；实际金额以业务确认记录为准。本次维护原因请另填下方“本次维护说明”。</p>'+
    (definition==null?`<p class="muted">${historical?'历史未维护':'尚未维护'}业务说明；留空保存会保留此状态。</p>`:'')+
    businessTextFields.map(([key,label,max])=>textarea(label,`business_${key}`,value[key],max)).join('')+
    '<details><summary>场景示例（最多5条，可只填写需要的行）</summary>'+
    Array.from({length:5},(_,index)=>{const item=value.examples[index]||{};return `<div class="import-row">`+
      textarea(`场景 ${index+1}`,`business_scenario:${index}`,item.scenario||'',500)+
      textarea(`业务解释 ${index+1}`,`business_explanation:${index}`,item.explanation||'',1000)+'</div>';}).join('')+
    '</details><small>删除全部业务说明和场景示例后保存，会清空当前物料说明；已保存的交易快照不变。</small></fieldset>';
}

export function materialBusinessPayload(form,definition) {
  if(form.get('business_definition_editor')!=='1')return undefined;
  const text=name=>String(form.get(name)??'');
  const value=Object.fromEntries(businessTextFields.map(([key])=>[key,text(`business_${key}`)]));
  value.examples=Array.from({length:5},(_,index)=>({scenario:text(`business_scenario:${index}`),explanation:text(`business_explanation:${index}`)}))
    .filter(item=>item.scenario!==''||item.explanation!=='');
  if(JSON.stringify(value)===JSON.stringify(normalizeDefinition(definition)))return null;
  return businessTextFields.every(([key])=>value[key]==='')&&value.examples.length===0?{}:value;
}

export function materialPayload(form,row={}) {
  const numberOrNull=name=>{const text=String(form.get(name)||'').trim();return text===''?null:Number(text);};
  const body={code:String(form.get('code')||'').trim(),name:String(form.get('name')||'').trim(),
    product_line_id:String(form.get('product_line_id')||''),unit:String(form.get('unit')||'').trim(),
    catalog_price:String(form.get('catalog_price')||'').trim(),currency:'CNY',tax_rate:String(form.get('tax_rate')||'').trim(),
    recognition_rule:String(form.get('recognition_rule')||'manual'),is_active:form.get('is_active')==='true',
    reason:String(form.get('reason')||'').trim(),rule_parameters:{
      default_service_months:numberOrNull('default_service_months'),oa_required:form.get('oa_required')==='on',
      oa_cutoff_day:numberOrNull('oa_cutoff_day'),monthly_allocation:String(form.get('monthly_allocation')||'')||null,
      oa_deferral_policy:String(form.get('oa_deferral_policy')||'')||null}};
  const definition=materialBusinessPayload(form,row.rule_parameters?.business_definition);
  if(definition!==undefined)body.rule_parameters.business_definition=definition;
  if(row.id)body.version_no=row.version_no;
  return body;
}

async function editMaterial(row={}) {
  requirePermission('material.manage');
  const catalog=await readForDialog('/material-product-lines?status=all');
  if(!catalog)return;
  const lines=catalog.items.filter(r=>r.is_active||r.id===row.product_line_id);
  const params=row.rule_parameters||{};
  return dialog(row.id?'修改物料':'新增物料',
    '<p class="notice">目录价格用于参考，交易行保存自己的成交价格。目录改价、改规则或停用不会改写已引用交易快照。</p>'+
    field('物料编码','code',row.code||'',{required:true})+field('物料名称','name',row.name||'',{required:true})+
    field('产品线','product_line_id','',{required:true,select:options(lines.map(r=>[r.id,r.name+(r.is_active?'':'（已停用）')]),row.product_line_id,'请选择产品线')})+
    field('计价单位','unit',row.unit||'',{required:true})+field('目录单价（含税元）','catalog_price',row.catalog_price??'',{required:true})+
    field('税率（小数，例如0.06）','tax_rate',row.tax_rate??'',{required:true})+
    `<fieldset class="full"><legend>固定计算规则${row.version_no?' · 当前目录 v'+Number(row.version_no):''}</legend>`+
    field('参考规则','recognition_rule','',{select:options(rules,row.recognition_rule||'manual',null)})+'<div data-rule-guide>'+materialRuleGuide(row.recognition_rule||'manual')+'</div>'+
    field('默认服务月数（可选）','default_service_months',params.default_service_months??'',{type:'number'})+
    field('月度分摊方式','monthly_allocation','',{select:options([['equal_calendar_months','按自然月等额分摊']],params.monthly_allocation,'未配置，不计算')})+
    `<label><input type="checkbox" name="oa_required" ${params.oa_required?'checked':''}>此规则要求OA日期</label>`+
    field('OA截止日（1—31，可选）','oa_cutoff_day',params.oa_cutoff_day??'',{type:'number'})+
    field('超过OA截止日的处理','oa_deferral_policy','',{select:options([['shift_full_term','整体顺延完整服务期'],['keep_original_end','保留原结束月份，不重分摊']],params.oa_deferral_policy,'未配置，不猜测')})+'</fieldset>'+
    materialBusinessFields(params.business_definition,{historical:Boolean(row.id)})+
    field('状态','is_active','',{select:options([['true','启用'],['false','停用']],String(row.is_active??true),null)})+
    field('本次维护说明','reason','',{required:true,type:'textarea'}),
    {wide:true,onSubmit:async(form,key)=>{requirePermission('material.manage');return api(row.id?`/materials/${encodeURIComponent(row.id)}`:'/materials',{
      method:row.id?'PUT':'POST',key,body:materialPayload(form,row)});},
      afterRender:d=>{if(row.id)d.querySelector('[name=code]').readOnly=true;d.querySelector('[name=recognition_rule]').onchange=event=>{d.querySelector('[data-rule-guide]').innerHTML=materialRuleGuide(event.target.value);};}});
}

async function editProductLines() {
  requirePermission('material.manage');
  const current=await readForDialog('/material-product-lines?status=all');
  if(!current)return;
  const lineRow=(r,index)=>`<div class="import-row" data-product-line="${index}">`+
    `<label class="field"><b>编码</b><input name="code:${index}" required maxlength="100" value="${esc(r.code||'')}" ${r.id?'readonly':''}></label>`+
    `<label class="field"><b>名称</b><input name="name:${index}" required maxlength="200" value="${esc(r.name||'')}"></label>`+
    `<label class="field"><b>顺序</b><input name="sort:${index}" type="number" min="0" max="10000" value="${Number(r.sort_order??index)}"></label>`+
    `<label><input type="checkbox" name="active:${index}" ${r.is_active!==false?'checked':''}>启用</label></div>`;
  let entries=current.items.slice();
  return dialog('维护产品线目录','<p class="notice">产品线供物料归属和早期商机意向共同选择。一个商机可含多条产品线；添加物料后，产品线由物料派生。旧条目可停用，历史未映射文字保留原文。</p><div data-product-lines>'+entries.map(lineRow).join('')+'</div><button type="button" data-add-line>新增产品线</button>'+field('本次维护说明','reason','',{required:true}),{
    wide:true,afterRender:d=>{d.querySelector('[data-add-line]').onclick=()=>{if(entries.length>=200){toast('最多200条产品线');return;}const index=entries.length;entries.push({});d.querySelector('[data-product-lines]').insertAdjacentHTML('beforeend',lineRow({},index));};},
    onSubmit:async(form,key)=>{requirePermission('material.manage');return api('/material-product-lines',{method:'PUT',key,body:{version_no:current.version_no,reason:String(form.get('reason')||'').trim(),items:entries.map((r,index)=>({id:r.id||null,code:String(form.get('code:'+index)||'').trim(),name:String(form.get('name:'+index)||'').trim(),sort_order:Number(form.get('sort:'+index)||0),is_active:form.get('active:'+index)==='on'}))}});}});
}

export async function materials() {
  const allowed=hasPermission('material.manage');
  const [result,lines]=await Promise.all([api('/materials'+query({offset:state.offset,limit:50,status:allowed?'all':'active'})),api('/material-product-lines?status='+(allowed?'all':'active'))]);
  return {html:head('产品线与物料目录','产品线统一管理归属，物料保存价格、税率和固定计算规则；交易引用后保留当时版本。',allowed?'<button data-action="lines">维护产品线</button><button class="primary" data-action="create">新增物料</button>':'')+
    `<section class="card"><h2>产品线目录</h2><p class="help">商机可跨多个产品线。有物料时按物料归属展示，无物料时选择意向产品线。</p>${table(['编码','产品线','状态'],lines.items,r=>`<tr><td>${esc(r.code)}</td><td>${esc(r.name)}</td><td>${r.is_active?'启用':'停用'}</td></tr>`,'尚未维护产品线')}</section>`+
    `<section class="card">${filters(search('搜索物料编码或名称'))}${table(['编码 / 名称','产品线','单位 / 目录价','税率 / 规则','状态 / 版本','操作'],result.items,r=>
      `<tr><td><strong>${esc(r.name)}</strong><small>${esc(r.code)}</small></td><td>${esc(r.product_line_name)}</td><td>${esc(r.unit)}<small>${esc(yuan(r.catalog_price))}</small></td><td>${esc(r.tax_rate)}<small>${esc(rules.find(x=>x[0]===r.recognition_rule)?.[1]||r.recognition_rule)}</small></td><td>${r.is_active?'启用':'停用'} · v${r.version_no}</td><td>${allowed?`<button class="link" data-action="edit" data-id="${esc(r.id)}">编辑</button>`:''}</td></tr>`,'暂无物料；请由有权限的运营维护目录')}${pager(result.total)}</section>`,
    bind:root=>bindCommon(root,{create:()=>editMaterial(),edit:id=>editMaterial(result.items.find(r=>r.id===id)),lines:()=>editProductLines()})};
}
