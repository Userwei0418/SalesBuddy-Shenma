// Configuration presence is independent of live service health and tenant activation.
export function installConfigurationStatus({api,node,onSaved=async()=>{}}){
  const $=s=>document.querySelector(s),dialog=$('#configuration-dialog'),body=$('#configuration-body');
  let generation=0,selected=null;
  function clear(){generation++;selected=null;body.replaceChildren();$('#configuration-sampled').textContent='';$('#configuration-title').textContent='公司配置检查';}
  function close(){clear();dialog.close();}
  dialog.addEventListener('close',clear);dialog.addEventListener('cancel',clear);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)close();});
  $('#close-configuration').onclick=close;
  const names={ai:'AI',feishu:'飞书同步',visit:'跟进记录与材料'};
  const purposes={text:'文字模型与业务兜底',asr:'语音转文字',tts:'语音合成（预留）'};
  const sources={tenant_purpose:'本公司用途配置',tenant_legacy:'本公司原有模型配置',instance_default:'继承实例默认配置',disabled:'已明确停用',invalid:'配置格式异常'};
  function render(data){
    const rows=[],identity=data.identity_checks;
    rows.push(node('h3','管理员与权限'));
    rows.push(node('p',`管理员${identity.administrator_ready?'可用':'待配置'} · ${identity.activation_complete?'已激活':'待激活'} · 基础权限${identity.permissions_ready?'已配置':'待配置'}`));
    rows.push(node('h3','已开通功能的必要配置'));
    const readiness=data.configuration_readiness;
    if(!readiness||readiness.kind!=='static_configuration')throw Error('缺少配套的配置检查结果，请刷新后重试。');
    rows.push(node('p',readiness.required_ready?'已开通功能的必要配置齐备。':'已开通功能仍有必要配置缺失，请按下方说明处理。'));
    rows.push(node('p','这里只检查配置字段与授权，不表示已解密或连接成功。公司基础激活不要求先配置 AI/飞书；启用功能前先由公司管理员保存配置。'));
    const issues={purpose_disabled:'此用途已停用',invalid_configuration:'配置格式异常',source_not_authorized:'缺少平台授权或本公司自有配置',fields_missing:'必要字段缺失',credential_missing:'未保存凭据',shared_service_not_configured:'当前 API 服务未配置完整共享接口',connection_missing:'尚未保存连接',direction_unsupported:'需使用系统到飞书单向同步',mapping_missing:'未启用任何对象映射',mapping_fields_missing:'启用的映射缺少表、系统 ID 或记录状态字段'};
    for(const c of readiness.checks){
      const label=c.code==='feishu'?'飞书同步':purposes[c.code];
      const scope=c.required?'已开通，必要配置':c.code==='asr'?'可选语音录入，不阻塞文字业务':c.code==='tts'?'预留用途，不阻塞业务':'模块未开通或已到期';
      rows.push(node('p',label+' · '+scope+' · '+(c.configuration_ready?'配置字段齐备':c.issues.map(x=>issues[x]||'需要核对配置').join('；'))));
    }
    rows.push(node('p','飞书字段齐备后可开通模块，再由公司管理员验证连接并启用同步；尚未验证或尚未启用时不会同步。'));
    rows.push(node('h3','模块开通状态'));
    for(const m of data.modules)rows.push(node('p',names[m.code]+'：'+(m.effective?'已开通':m.enabled?'已到期':'未开通')));
    const policy=data.ai_service;
    rows.push(node('h3','平台共享 AI 服务'));
    rows.push(node('p',policy.shared_enabled?'允许本公司使用平台共享模型和已配置的业务 Agent。':'共享服务已关闭；公司需配置自有模型接口，缺少配置时不会回退到平台。'));
    rows.push(node('p','此开关不授予业务权限，也不会开通 AI 模块。关闭后阻止新的共享请求及重试；已经发出的请求仍结算调用记录。'));
    const form=node('form'),label=node('label'),check=node('input'),reason=node('textarea'),save=node('button','保存共享服务设置'),message=node('p');
    check.type='checkbox';check.checked=policy.shared_enabled;label.append(check,node('span','允许使用平台共享 AI'));
    reason.required=true;reason.maxLength=500;reason.placeholder='变更原因';reason.setAttribute('aria-label','共享 AI 变更原因');save.type='submit';
    form.append(label,reason,save,message);rows.push(form);
    const ticket=generation,tenant=selected;
    form.onsubmit=async e=>{
      e.preventDefault();if(save.disabled||ticket!==generation||!dialog.open)return;
      if(!reason.value?.trim()){message.textContent='请填写变更原因。';return;}
      save.disabled=true;message.textContent='正在保存…';
      try{
        await api('/tenants/'+encodeURIComponent(policy.workspace_id)+'/ai-service',{version_no:policy.version_no,shared_enabled:check.checked,reason:reason.value.trim()});
        if(ticket!==generation||!dialog.open)return;
        await onSaved();if(ticket===generation&&dialog.open)await load(tenant);
      }catch(e){if(ticket===generation&&dialog.open)message.textContent=e.code==='VERSION_CONFLICT'?'公司版本已变化，请点击“刷新配置状态”后重新修改。':e.message;}
      finally{if(ticket===generation)save.disabled=false;}
    };
    rows.push(node('h3','模型配置来源'));
    for(const m of data.model_connections){
      const card=node('section','','runtime-tenant');card.append(node('h4',purposes[m.purpose]));
      card.append(node('p',sources[m.source]+' · 用途版本 '+(m.version_no??'未发布')+(m.source==='tenant_legacy'?' · 原配置版本 '+m.legacy_version_no:'')));
      if(m.source==='disabled')card.append(node('p','此用途已停用。'));
      else if(m.source==='invalid')card.append(node('p','配置格式异常，请由本公司管理员核对。'));
      else if(m.source==='instance_default')card.append(node('p','本公司未保存该用途的独立配置；平台授权见上方；是否已配置且可连接，需要另行验证。'));
      else card.append(node('p',(m.fields_present?'基础字段已填写':'基础字段存在缺项')+' · '+(m.credential_saved?'已保存凭据':'未保存本公司凭据，需核对继承来源')));
      if(m.service_authorized===false && !['disabled','invalid'].includes(m.mode))card.append(node('p','当前无可授权的模型配置，需要公司管理员配置自有接口和密钥。'));
      if(m.source==='tenant_purpose'&&m.credential_saved&&!m.credential_owned)card.append(node('p','历史密钥继承自默认服务，仍受平台共享开关控制；重新填写自有密钥后才作为公司独立配置。'));
      if(m.purpose==='tts')card.append(node('small','语音合成为预留用途，不按业务必需项判断。'));
      rows.push(card);
    }
    rows.push(node('h3','飞书配置'));
    const f=data.feishu;
    if(!f.configured)rows.push(node('p','本公司未保存飞书连接。'));
    else{
      rows.push(node('p','配置版本 '+f.revision+' · '+(f.enabled?'同步已启用':'同步未启用')));
      const checks=[f.fields_present?'基础字段已填写':'基础字段存在缺项',f.credential_saved?'已保存凭据':'未保存本公司凭据',f.direction_supported?'系统到飞书单向同步':'同步方向不受支持','启用映射 '+f.enabled_mappings+' 项'];
      rows.push(node('p',checks.join(' · ')));
      rows.push(node('p',f.validation_pending?'连接验证已申请，等待处理。':f.validated?'当前版本留有通过验证的记录；本次未重新连接飞书。':'当前版本尚无通过验证的记录。'));
    }
    return rows;
  }
  async function load(tenant){
    const ticket=++generation;selected=tenant;body.replaceChildren(node('p','正在读取本公司配置状态…'));
    $('#configuration-sampled').textContent='';$('#configuration-title').textContent=tenant.name+' · 配置检查';
    try{
      const data=await api('/tenants/'+encodeURIComponent(tenant.id)+'/configuration');
      if(ticket!==generation||!dialog.open)return;
      if(data.workspace_id!==tenant.id||data.ai_service?.workspace_id!==tenant.id)throw Error('公司配置响应不匹配，请关闭后重试。');
      body.replaceChildren(...render(data));
      $('#configuration-sampled').textContent='采样时间：'+new Date(data.sampled_at).toLocaleString()+' · 公司版本 '+data.version_no;
    }catch(e){if(ticket===generation&&dialog.open)body.replaceChildren(node('p',e.message));}
  }
  $('#refresh-configuration').onclick=()=>{if(selected)return load(selected);};
  return {close,open(tenant){clear();if(!dialog.open)dialog.showModal();return load(tenant);}};
}
