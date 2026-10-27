import {api,dialog,esc,field,options,requirePermission,toast,readForDialog,dialogIntent} from './core.js';
import {calculationBody} from './finance-workspace.js';

const relations=[['direct','直接销售'],['distributor','总代交易'],['downstream','下游交易']];
const optionalNumber=(form,key)=>String(form.get(key)||'').trim()===''?null:Number(form.get(key));
const optionalText=(form,key)=>String(form.get(key)||'').trim()||null;

export function validateMaterialDraft(result,request) {
  if(result?.reference_kind!=='sales_forecast_draft_reference'||result.persisted!==false||
    result.draft_revision!==request.draft_revision||result.opportunity_id!==request.opportunity_id||result.opportunity_version!==request.expected_opportunity_version||
    result.period?.year!==request.year||result.period?.quarter!==request.quarter||
    !Array.isArray(result.lines)||result.lines.length!==request.material_lines.length||result.lines.some((line,index)=>
      line.client_key!==request.material_lines[index].client_key||line.catalog_version_no!==request.material_lines[index].catalog_version_no))
    throw Error('草稿参考与当前输入不一致，请保留输入并重新试算。');
  return result;
}

export function materialsPayload(form,current,rows) {
  const relation=String(form.get('trade_relation')||'');
  return {version_no:current.version_no,trade_relation:relation,
    upstream_opportunity_id:relation==='downstream'?optionalText(form,'upstream_opportunity_id'):null,
    reason:String(form.get('reason')||'').trim(),material_lines:rows.flatMap((row,index)=>row?[{
      id:row.id||null,material_id:row.material_id,catalog_version_no:row.catalog_snapshot?.version_no??row.catalog_version_no,
      quantity:String(form.get('quantity:'+index)||'').trim(),unit_price:String(form.get('unit_price:'+index)||'').trim(),
      usage:String(form.get('usage:'+index)||''),
      expected_delivery_on:optionalText(form,'expected_delivery_on:'+index),
      expected_start_on:optionalText(form,'expected_start_on:'+index),expected_oa_on:optionalText(form,'expected_oa_on:'+index),
      service_months:optionalNumber(form,'service_months:'+index),expected_account_count:optionalNumber(form,'expected_account_count:'+index),
      expected_consumption_amount:optionalText(form,'expected_consumption_amount:'+index),
      expected_consumption_year:optionalNumber(form,'expected_consumption_year:'+index),
      expected_consumption_quarter:optionalNumber(form,'expected_consumption_quarter:'+index),
      note:String(form.get('note:'+index)||'').trim(),
    }]:[])};
}

function lineFields(row,index) {
  const snapshot=row.catalog_snapshot||{};
  const f=(label,key,opts={})=>field(label,key+':'+index,row[key]??'',opts);
  return `<section class="card" data-material-row="${index}"><h3>${esc(snapshot.name||'物料')} <small>${esc(snapshot.code||'')} · ${esc(snapshot.unit||'')}</small></h3><button type="button" data-remove-line="${index}">移除此交易行</button><div class="form-grid">`+
    f('数量','quantity',{required:true,type:'number',min:'0.0001',step:'0.0001'})+
    f('成交单价（含税元）','unit_price',{required:true,type:'number',min:'0',step:'0.01'})+
    f('用途','usage',{required:true,select:options([['self_use','自用'],['resale','转售']],row.usage||'','请选择用途')})+
    f('预计交付日','expected_delivery_on',{type:'date'})+f('预计开始日','expected_start_on',{type:'date'})+
    f('预计 OA 日','expected_oa_on',{type:'date'})+f('服务月数','service_months',{type:'number',min:1,max:120,step:1})+
    f('预计开通账号数','expected_account_count',{type:'number',min:0,step:1})+
    f('预计消耗金额（含税元）','expected_consumption_amount',{type:'number',min:0,step:'0.01'})+
    f('预计消耗年份','expected_consumption_year',{type:'number',min:2000,max:2100,step:1})+
    f('预计消耗季度','expected_consumption_quarter',{select:options([['1','Q1'],['2','Q2'],['3','Q3'],['4','Q4']],row.expected_consumption_quarter,'未指定')})+
    f('交易行说明','note',{full:true})+'</div></section>';
}

export async function editOpportunityMaterials(id) {
  requirePermission('opportunity.update');
  const current=await readForDialog(`/api/v1/opportunities/${encodeURIComponent(id)}/materials`);
  if(!current)return;
  const rows=current.items.map(row=>({...row}));
  const parentOptions=current.upstream_opportunity_id?[[current.upstream_opportunity_id,'当前已关联上游商机']]:[];
  dialog('交易关系与物料 · '+current.name,
    '<p class="notice">此处维护交易构成。数量 × 成交单价只计算一次，服务月数和账号数不再次相乘；参考规则不会写入运营实际。已有实际的交易关系及用途受保护，须先处理对应实际。</p><div class="form-grid">'+
    field('交易关系','trade_relation',current.trade_relation,{required:true,select:options(relations,current.trade_relation,'请选择')})+
    field('上游总代商机（下游必选）','upstream_opportunity_id',current.upstream_opportunity_id,{select:options(parentOptions,current.upstream_opportunity_id,'未关联')})+
    field('查找上游商机','upstream_query','',{full:true})+'<button type="button" data-find-upstream>查找总代商机</button></div><div data-upstream-help></div>'+
    '<h3>交易物料</h3><div data-material-lines>'+rows.map(lineFields).join('')+'</div><div class="form-grid">'+
    field('查找物料','material_query','',{full:true})+'<button type="button" data-find-material>查找可用物料</button></div><div data-material-results></div>'+
    '<section class="card"><h3>边填写边核对物料参考</h3><div class="form-grid">'+field('参考年份','draft_year',new Date().getFullYear(),{type:'number',min:2000,max:2100})+
    field('参考季度','draft_quarter','',{select:options([1,2,3,4].map(q=>[q,'Q'+q]),Math.floor(new Date().getMonth()/3)+1,null)})+'</div><div data-material-preview aria-live="polite">请先填写交易关系及物料。</div></section>'+
    field('本次维护说明','reason','',{required:true,type:'textarea',full:true}),{
      wide:true,submit:'保存交易构成',
      afterRender(d) {
        const form=d.querySelector('#dialog-form');let revision=0,timer;
        const draftPreview=async requested=>{
          if(!d.open||document.querySelector('#dialog-form')!==form||requested!==revision)return;
          const data=new FormData(form),saved=materialsPayload(data,current,rows),target=d.querySelector('[data-material-preview]');
          if(!saved.trade_relation||(saved.trade_relation==='downstream'&&!saved.upstream_opportunity_id)){
            target.textContent='请选择交易关系；下游交易还需关联上游商机。';return;
          }
          const year=Number(data.get('draft_year')),quarter=Number(data.get('draft_quarter'));
          if(!Number.isInteger(year)||year<2000||year>2100||![1,2,3,4].includes(quarter)){target.textContent='请选择有效参考年份和季度。';return;}
          const lines=saved.material_lines.map((line,index)=>({...line,client_key:'admin-line-'+rows.indexOf(rows.filter(Boolean)[index]),
            quantity:line.quantity||null,unit_price:line.unit_price||null,usage:line.usage||null}));
          const body={year,quarter,draft_revision:requested,opportunity_id:id,expected_opportunity_version:current.version_no,
            trade_relation:saved.trade_relation,upstream_opportunity_id:saved.upstream_opportunity_id,material_lines:lines};
          target.textContent='正在计算当前草稿参考…';
          try{
            const result=await api('/api/v1/material-forecast-preview',{method:'POST',body});
            if(!d.open||document.querySelector('#dialog-form')!==form||requested!==revision)return;
            target.innerHTML=calculationBody(validateMaterialDraft(result,body));
          }catch(error){if(d.open&&document.querySelector('#dialog-form')===form&&requested===revision)target.textContent=error.message+' 输入已保留；参考未更新。';}
        };
        const schedulePreview=()=>{clearTimeout(timer);revision++;const requested=revision;timer=setTimeout(()=>draftPreview(requested),350);};
        const onInput=event=>{if(!['reason','material_query','upstream_query'].includes(event.target.name))schedulePreview();};
        form.addEventListener('input',onInput);form.addEventListener('change',onInput);
        const bindRemove=()=>d.querySelectorAll('[data-remove-line]').forEach(button=>button.onclick=()=>{
          const index=Number(button.dataset.removeLine);rows[index]=null;button.closest('[data-material-row]').remove();schedulePreview();
        });
        bindRemove();
        d.querySelector('[data-find-material]').onclick=async()=>{
          const stillCurrent=dialogIntent({liveForm:true});
          try {
            const q=d.querySelector('[name=material_query]').value.trim();
            const result=await api('/materials?status=active&limit=50&q='+encodeURIComponent(q));
            if(!stillCurrent())return;
            const target=d.querySelector('[data-material-results]');
            target.innerHTML=result.items.map((item,index)=>`<button type="button" data-add-material="${index}">${esc(item.code)} · ${esc(item.name)} · ${esc(item.product_line_name)}</button>`).join('')+
              `<p class="help">${result.has_more?'还有更多结果，请缩小搜索范围。':result.items.length?'点击物料新增交易行；同一SKU可有多行。':'没有找到可用物料，请先维护目录。'}</p>`;
            target.querySelectorAll('[data-add-material]').forEach(button=>button.onclick=()=>{
              if(rows.filter(Boolean).length>=200){toast('每个商机最多200条有效物料行');return;}
              const item=result.items[Number(button.dataset.addMaterial)],index=rows.length;
              const row={material_id:item.id,catalog_snapshot:item,quantity:'1',unit_price:item.catalog_price,usage:''};
              rows.push(row);d.querySelector('[data-material-lines]').insertAdjacentHTML('beforeend',lineFields(row,index));bindRemove();schedulePreview();
            });
          } catch(error){toast(error.message);}
        };
        d.querySelector('[data-find-upstream]').onclick=async()=>{
          const stillCurrent=dialogIntent({liveForm:true});
          try {
            const query=new URLSearchParams({q:d.querySelector('[name=upstream_query]').value.trim(),trade_relation:'distributor',include_closed:'true',page_size:'50',exclude_customer_id:current.customer_id,exclude_opportunity_id:id});
            const result=await api('/api/v1/opportunities?'+query);
            if(!stillCurrent())return;
            const selected=d.querySelector('[name=upstream_opportunity_id]').value;
            const choices=result.items.map(item=>[item.id,item.name+' · '+(item.customer_name||'')]);
            if(selected&&!choices.some(item=>item[0]===selected))choices.unshift([selected,'当前已关联上游商机']);
            d.querySelector('[name=upstream_opportunity_id]').innerHTML=options(choices,selected,'请选择上游总代商机');
            d.querySelector('[data-upstream-help]').textContent=result.has_more?'还有更多结果，请缩小搜索范围。':'仅显示有权查看且属于其他客户的总代商机。';
          } catch(error){toast(error.message);}
        };
        schedulePreview();
      },
      onSubmit:async(form,key)=>{
        requirePermission('opportunity.update');const payload=materialsPayload(form,current,rows);
        if(payload.trade_relation==='downstream'&&!payload.upstream_opportunity_id)throw Error('请选择上游总代商机');
        await api(`/opportunities/${encodeURIComponent(id)}/materials`,{method:'PUT',key,body:payload});toast('交易构成已保存');
      },
    });
}
