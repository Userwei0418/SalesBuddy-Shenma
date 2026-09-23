import {api, head, field, esc, options, state, toast} from './core.js';

const labels={"original_created_at_raw":"商机原始建单时间（原值）","original_created_at_source":"建单时间来源","associated_partner_ids":"关联伙伴（多选）","associated_partner_names":"关联伙伴名称","customer_ids":"关联客户（多选）","customer_names":"关联客户名称","expected_close_year": "预计关单年份", "expected_close_quarter": "预计关单季度（不代表具体日期）", "original_owner_name": "原销售姓名", "ownership_resolution": "当前归属确认状态", "original_recorder_name": "原跟进人姓名", "manager_user_ref_id": "当前管理人", "manager_name": "当前管理人姓名", "opportunity_ids": "关联商机（多选）", "opportunity_names": "关联商机名称", "short_name": "伙伴简称", "principal_name": "伙伴负责人", "channel_manager_user_ref_id": "渠道经理", "original_channel_manager_name": "原渠道经理姓名", "progress": "伙伴进展", "signed_on": "签约日期", "partner_type": "伙伴类型", "region": "区域", "province": "省份", "collection_confidence": "原始回款信心度", "source_field": "来源字段", "raw_amount": "季度原始金额", "source_unit": "原始金额单位", "tax_basis": "原始税口径", "source_system": "来源系统", "source_base_id": "来源底表", "source_table_id": "来源数据表", "source_external_record_id": "来源记录ID","organization_team_id":"组织归属部门ID","organization_team_name":"组织归属部门（不授予业务权限）","year":"年份","quarter":"季度","recognized_amount":"预测确收（含税元）","collection_amount":"预测回款（含税元）","updated_by":"更新人","follow_up_plan":"商机跟进计划","partner_id":"关联伙伴","company_name":"所属公司","source_version":"系统版本号","synced_at":"最近同步时间","department_names":"所属部门","primary_department_name":"主部门","team_name":"团队名称","name":"名称","account":"登录账号","status":"业务状态","primary_department_id":"主部门ID","department_ids":"部门ID列表","roles":"角色","title":"标题","description":"说明","role":"联系人角色","is_primary":"主要联系人","customer_id":"关联客户","opportunity_id":"关联商机","creator_id":"发起人","owner_id":"负责人","due_at":"截止时间","completion_note":"完成说明","review_note":"验收意见","kind":"类型","amount":"金额（元）","occurred_on":"发生日期","source_ref":"业务凭据编号","note":"备注","confirmed_by":"确认人","voided_at":"作废时间","void_reason":"作废原因","period_type":"周期类型","period_start":"周期开始","period_end":"周期结束","scope_type":"目标范围","user_id":"关联成员","team_id":"团队ID","department_code":"部门编码","record_status":"记录状态","source_created_at":"系统创建时间","source_updated_at":"系统更新时间","system_id":"系统记录ID","industry":"行业","customer_type":"客户类型","priority":"客户优先级","main_business":"主营业务","needs":"客户需求","budget":"客户预算","next_action":"下一步行动","department_id":"部门ID","owner_name":"负责人姓名","owner_account":"负责人账号","department_name":"所属部门","lifecycle_status":"客户阶段","fde_members":"协作FDE","sales_members":"协作销售","potential_score":"客户潜力","relationship_score":"关系深度","quadrant_code":"客户象限","risk_title":"风险提示","stage":"商机阶段","currency":"币种","probability":"赢单概率","expected_close_date":"预计关单日期","product_line":"产品线","sales_channel":"销售渠道","customer_name":"客户名称","grade":"商机等级","content":"沟通内容","interaction_at":"跟进日期","archived_at":"正式归档时间","recorder_id":"跟进人","contact_name":"对接人","contact_title":"联系人职位","interaction_mode":"沟通方式","opportunity_name":"商机名称","visit_goal":"拜访目标","visit_location":"拜访地点","contact_role":"联系人角色","partner_name":"伙伴名称","is_first_visit":"首次拜访","follow_up_score":"跟进质量分","collaborators":"协同人"};
export function buildSyncConfig(form, previous, catalog, workspaceId, uuid=()=>crypto.randomUUID()) {
  const mappings={};
  for(const object of catalog.objects){
    const tableId=String(form.get(`${object.key}:table`)||'').trim();
    if(!tableId) continue;
    const fields={};
    for(const source of object.fields){const target=String(form.get(`${object.key}:${source}`)||'').trim();if(target)fields[source]=target;}
    mappings[object.key]={enabled:form.has(`${object.key}:enabled`),table_id:tableId,id_field_id:String(form.get(`${object.key}:id`)||'').trim(),fields,archive_policy:'mark_status'};
  }
  const routes=String(form.get('routes')||'').split('\n').filter(line=>line.trim()).map(line=>{
    const [department,...chats]=line.split(',').map(s=>s.trim()).filter(Boolean);
    if(!department||!chats.length)throw Error('部门路由每行填写：部门ID,群ID，可填写多个群ID');
    return {department_id:department,chat_ids:chats};
  });
  return {schema_version:1,workspace_id:workspaceId,connection_id:previous?.connection_id||uuid(),credential_ref:previous?.credential_ref||uuid(),revision:(previous?.revision||0)+1,provider:'feishu',enabled:false,direction:form.get('sync_direction')==='bidirectional'?'bidirectional':'system_to_base',app_id:String(form.get('app_id')||'').trim(),base_token:String(form.get('base_token')||'').trim(),base_url:String(form.get('base_url')||'').trim()||null,mappings,notification:{enabled:form.has('notifications'),routing_mode:form.get('routing_mode'),default_chat_id:String(form.get('chat_id')||'').trim()||null,on_create:['customer','opportunity','visit'],routes}};
}

// Portable configuration deliberately excludes workspace/connection identities and credentials.
export function syncTemplate(config) {
  return {template_version:1,direction:config.direction||'system_to_base',app_id:config.app_id,base_token:config.base_token,base_url:config.base_url||null,
    mappings:config.mappings,notification:config.notification};
}
export function templateFormValues(input,catalog) {
  if(!input||input.template_version!==1)throw Error('不支持的配置模板版本');
  const allowed=new Set(['template_version','direction','app_id','base_token','base_url','mappings','notification']);
  if(Object.keys(input).some(key=>!allowed.has(key)))throw Error('模板含不允许的字段；请勿导入公司身份或凭证');
  const text=(value,pattern)=>{if(typeof value!=='string'||!pattern.test(value))throw Error('模板标识格式无效');return value;};
  if(input.direction!==undefined&&!['system_to_base','bidirectional'].includes(input.direction))throw Error('同步方向无效');
  const result={sync_direction:input.direction||'system_to_base',app_id:text(input.app_id,/^[A-Za-z0-9_-]{1,100}$/),base_token:text(input.base_token,/^[A-Za-z0-9_-]{1,100}$/)};
  result.base_url='';
  if(input.base_url){
    const url=new URL(input.base_url);
    if(url.protocol!=='https:'||!url.hostname.endsWith('.feishu.cn')||url.username||url.password||url.port||url.search||url.hash||url.pathname!=='/base/'+input.base_token)throw Error('详情链接须为对应飞书Base的HTTPS地址');
    result.base_url=input.base_url;
  }
  const objects=new Map(catalog.objects.map(o=>[o.key,o]));
  if(!input.mappings||typeof input.mappings!=='object'||Array.isArray(input.mappings))throw Error('缺少字段映射');
  for(const [kind,mapping] of Object.entries(input.mappings)){
    const object=objects.get(kind);if(!object)throw Error('模板含未知对象');
    if(!mapping||Object.keys(mapping).some(k=>!['enabled','table_id','id_field_id','fields','archive_policy'].includes(k)))throw Error('映射格式无效');
    result[kind+':table']=text(mapping.table_id,/^tbl[A-Za-z0-9_-]+$/);
    result[kind+':id']=text(mapping.id_field_id,/^fld[A-Za-z0-9_-]+$/);
    result[kind+':enabled']=mapping.enabled===true;
    if(!mapping.fields||typeof mapping.fields!=='object'||Array.isArray(mapping.fields))throw Error('字段映射格式无效');
    for(const [source,target] of Object.entries(mapping.fields)){
      if(!object.fields.includes(source))throw Error('模板含未支持的源字段');
      result[kind+':'+source]=text(target,/^fld[A-Za-z0-9_-]+$/);
    }
  }
  const n=input.notification||{};
  if(Object.keys(n).some(k=>!['enabled','routing_mode','default_chat_id','on_create','routes'].includes(k)))throw Error('通知配置格式无效');
  if(!['single_group','department_routes'].includes(n.routing_mode))throw Error('通知路由方式无效');
  result.notifications=n.enabled===true;result.routing_mode=n.routing_mode;
  result.chat_id=n.default_chat_id?text(n.default_chat_id,/^oc_[A-Za-z0-9]+$/):'';
  if(n.routes!==undefined&&!Array.isArray(n.routes))throw Error('部门路由格式无效');
  result.routes=(n.routes||[]).map(r=>{
    const department=text(r.department_id,/^[0-9a-f-]{36}$/i);
    if(!Array.isArray(r.chat_ids)||!r.chat_ids.length)throw Error('部门通知群不能为空');
    return [department,...r.chat_ids.map(c=>text(c,/^oc_[A-Za-z0-9]+$/))].join(',');
  }).join('\n');
  return result;
}

export async function feishuSync(){
  const companyId=state.company?.id;
  const [current,catalog,status]=await Promise.all([api('/feishu-sync'),api('/feishu-sync/catalog'),api('/feishu-sync/status')]);
  const config=current.config, notification=config?.notification||{};
  const supportedDirection=!config?.direction||config.direction==='system_to_base';
  const valid=Boolean(config&&current.validated_revision===config.revision);
  const mappingHtml=catalog.objects.map(object=>{
    const m=config?.mappings?.[object.key]||{};
    return `<details class="card card-pad feishu-sync-card"><summary><strong>${esc(object.label)}</strong> · ${m.enabled?'已选入同步范围':'未选入同步范围'}</summary><div class="form-grid">
      <label class="field"><b>参与同步</b><input type="checkbox" name="${esc(object.key)}:enabled" ${m.enabled?'checked':''}></label>
      ${field('目标表 ID',object.key+':table',m.table_id||'')}${field('系统 ID 字段 ID',object.key+':id',m.id_field_id||'')}
      ${object.fields.map(source=>field(labels[source]||source,object.key+':'+source,m.fields?.[source]||'',{help:'目标字段 ID；留空则不同步此字段'})).join('')}
      </div></details>`;
  }).join('');
  return {html:`${head('飞书同步','按当前公司配置。系统是数据来源，飞书修改不会写回系统。')}
    <section class="card card-pad feishu-sync-card"><h2>连接状态：${current.enabled?'同步已启用':'同步未启用'}</h2><p>凭证：${current.has_credential?'已安全保存':'未配置'} · 连接校验：${current.validation_requested?'校验中':valid?'已通过':'未通过或尚未校验'}</p>${current.last_error_code?`<p class="error-box">${esc(current.last_error_code)}</p>`:''}
    <div class="actions"><button type="button" data-sync-action="validate" ${!supportedDirection||!current.has_credential?'disabled':''}>检查连接与字段</button><button type="button" data-sync-action="enable" ${!supportedDirection||!valid||current.enabled?'disabled':''}>启用同步</button><button type="button" data-sync-action="pause" ${!current.enabled?'disabled':''}>暂停同步</button><button type="button" data-sync-action="initialize" ${!supportedDirection||!current.enabled?'disabled':''}>同步历史数据（不通知）</button></div>
    ${!supportedDirection?'<p class="error-box">双向同步仅为配置预留，尚不支持校验或启用。请切换为单向同步并保存。</p>':''}
    <p class="muted">保存配置会暂停同步，需检查通过后重新启用。校验只读取表格与群信息。</p></section>
    <form id="feishu-config" autocomplete="off"><section class="card card-pad feishu-sync-card"><h2>应用与通知</h2><div class="form-grid">
    ${field('App ID','app_id',config?.app_id||'',{required:true})}
    ${field('多维表格 Base Token','base_token',config?.base_token||'',{required:true})}
    ${field('Base详情链接','base_url',config?.base_url||'',{help:'通知卡片的详情入口，填写 https://企业域名.feishu.cn/base/BaseToken，不含问号后的参数'})}
    ${field('同步方向','sync_direction','',{select:options([['system_to_base','单向同步：系统 → 飞书'],['bidirectional','双向同步（预留，暂不可启用）']],config?.direction||'system_to_base',null),help:'当前仅支持系统 → 飞书。双向配置可保存为草稿，不能校验或启用。'})}
    <label class="field"><b>App Secret</b><input name="app_secret" type="password" autocomplete="new-password" placeholder="${current.has_credential?'留空保留现有凭证':'填写应用凭证'}"><small>仅提交至当前系统加密保存，不回显、不存入浏览器。</small></label>
    <label class="field"><b>新增通知</b><input type="checkbox" name="notifications" ${notification.enabled?'checked':''}><small>仅客户、商机、正式跟进新增时通知；历史和修改只同步。</small></label>
    ${field('默认通知群 ID','chat_id',notification.default_chat_id||'')}
    ${field('通知方式','routing_mode','',{select:options([['single_group','统一群'],['department_routes','按部门分群']],notification.routing_mode||'single_group',null)})}
    <label class="field full"><b>部门路由</b><textarea name="routes" rows="3" placeholder="部门ID,群ID">${esc((notification.routes||[]).map(r=>[r.department_id,...r.chat_ids].join(',')).join('\n'))}</textarea><small>每行一个部门；统一群模式忽略此处。未匹配部门使用默认群。</small></label></div></section>
    ${config?'<section class="card card-pad feishu-sync-card"><h2>迁移同步目标</h2><label class="field"><b><input type="checkbox" name="migrate_target"> 确认迁移到新的应用、Base 或表映射</b><small>先暂停同步并等待正在处理的任务结束。旧飞书数据保留，旧队列终止；新目标需重新校验，历史数据重新同步但不通知。更换应用时须填写新凭证。</small></label></section>':''}
    <section class="card card-pad feishu-sync-card"><h2>配置模板</h2><p class="muted">导入只回填当前表单，不保存、不启用同步。模板不包含凭证和公司身份。</p><div class="actions"><label>导入 JSON 模板 <input id="sync-import" type="file" accept="application/json,.json"></label><button id="sync-export" type="button">导出当前表单模板</button></div></section>
    <h2>对象与字段映射</h2><p class="muted">每类对象使用独立表，系统 ID 为文本字段，必须映射记录状态。关联字段须指向对应对象表。</p>${mappingHtml}
    <button class="primary" type="submit">保存配置并暂停同步</button><p id="feishu-feedback" role="status"></p></form>
    <section class="card card-pad feishu-sync-card"><h2>处理状态</h2><p>${esc(status.counts.map(r=>`${({pending:'待处理',running:'处理中',succeeded:'已完成',failed:'等待重试',dead_letter:'需要处理'})[r.status]||r.status}: ${r.count}`).join(' · ')||'暂无同步事件')}</p>
    <p class="muted">处理前请暂停同步，等待在途任务结束。结果未知的通知先到对应群核对；以下操作不会立即发送消息。</p>
    <label class="field"><b>处理说明</b><textarea id="sync-recovery-note" rows="2" maxlength="1000" placeholder="记录核对结果或重试原因，请勿填写密码或凭证"></textarea></label>
    ${(status.unknown||[]).map(r=>`<div class="card card-pad"><p>通知结果待核对 · ${esc(r.chat_id)} · ${esc(r.created_at)}</p><div class="actions"><button type="button" data-recover="confirm_sent" data-event="${esc(r.event_id)}" data-key="${esc(r.dedupe_key)}" ${current.enabled?'disabled':''}>已在群中看到</button><button type="button" data-recover="suppress" data-event="${esc(r.event_id)}" data-key="${esc(r.dedupe_key)}" ${current.enabled?'disabled':''}>结束此通知，不再发送</button></div></div>`).join('')}
    ${status.errors.map(r=>`<div><p>${esc(catalog.objects.find(o=>o.key===r.object_kind)?.label||r.object_kind)} · ${esc(r.error_code)}</p>${r.id&&r.error_code!=='TARGET_MIGRATED'?`<button type="button" data-recover="retry" data-event="${esc(r.id)}" ${current.enabled?'disabled':''}>重新排队</button>`:''}</div>`).join('')}</section>`,bind(root){
      const stillCurrent=()=>{if(state.company?.id!==companyId||!root.isConnected)throw Error('公司或页面已切换，请刷新后操作');};
      const form=root.querySelector('#feishu-config');
      root.querySelector('#sync-import').onchange=async event=>{
        try{stillCurrent();const file=event.target.files?.[0];if(!file)return;if(file.size>1024*1024)throw Error('模板不能超过1MB');
          const values=templateFormValues(JSON.parse(await file.text()),catalog);stillCurrent();
          // Validate the whole file before changing any current field.
          for(const element of form.elements){
            if(!element.name||['app_secret','migrate_target'].includes(element.name))continue;
            if(element.name.includes(':')||Object.hasOwn(values,element.name)){
              if(element.type==='checkbox')element.checked=values[element.name]===true;
              else element.value=values[element.name]??'';
            }
          }
          form.elements.app_secret.value='';form.elements.migrate_target&&(form.elements.migrate_target.checked=false);
          toast('模板已回填，请核对目标公司、应用和表格后保存');
        }catch(error){toast(error.message);}finally{event.target.value='';}
      };
      root.querySelector('#sync-export').onclick=()=>{
        try{stillCurrent();const configToExport=buildSyncConfig(new FormData(form),config,catalog,current.workspace_id||config?.workspace_id);
          const template=syncTemplate(configToExport);templateFormValues(template,catalog);
          const url=URL.createObjectURL(new Blob([JSON.stringify(template,null,2)],{type:'application/json'}));
          const link=document.createElement('a');link.href=url;link.download='feishu-sync-template.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
        }catch(error){toast(error.message);}
      };
      form.onsubmit=async event=>{
        event.preventDefault(); const button=form.querySelector('[type=submit]');button.disabled=true;
        try{stillCurrent();const values=new FormData(form);const body={expected_revision:config?.revision||0,migrate_target:values.has('migrate_target'),config:buildSyncConfig(values,config,catalog,current.workspace_id||config?.workspace_id)};
          const secret=values.get('app_secret');if(secret)body.app_secret=secret;
          form.elements.app_secret.value='';values.delete('app_secret');
          try{await api('/feishu-sync',{method:'PUT',body});}finally{delete body.app_secret;}
          stillCurrent();toast(body.config.direction==='system_to_base'?'配置已保存，请检查连接与字段':'双向配置草稿已保存，尚不支持校验或启用');await state.refresh();
        }catch(error){if(form.isConnected)root.querySelector('#feishu-feedback').textContent=error.message;}finally{button.disabled=false;}
      };
      root.querySelectorAll('[data-recover]').forEach(button=>button.onclick=async()=>{
        button.disabled=true;try{stillCurrent();const note=root.querySelector('#sync-recovery-note').value.trim();
          if(!note)throw Error('请填写处理说明');
          await api('/feishu-sync/recover',{method:'POST',body:{expected_revision:config.revision,
            event_id:button.dataset.event,action:button.dataset.recover,delivery_key:button.dataset.key||null,note}});
          stillCurrent();toast('处理结果已记录；重新启用后继续同步');await state.refresh();
        }catch(error){toast(error.message);}finally{button.disabled=false;}
      });
      root.querySelectorAll('[data-sync-action]').forEach(button=>button.onclick=async()=>{
        button.disabled=true;try{stillCurrent();await api('/feishu-sync/'+button.dataset.syncAction,{method:'POST',body:{expected_revision:config.revision}});stillCurrent();toast('操作已提交');await state.refresh();}catch(error){toast(error.message);}finally{button.disabled=false;}
      });
    }};
}
