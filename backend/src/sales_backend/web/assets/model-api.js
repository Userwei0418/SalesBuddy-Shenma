import {state, $, api, esc, date, field, options, dialog, toast, head} from './core.js';
import {bindConnectivityTests} from './ai-connectivity.js';

const base = '/api/v1/admin/model-apis';
const modes = [['custom','使用独立接口'],['inherit','继承现有默认接口'],['disabled','停用该用途的直连接口']];
const protocols = {chat_completions:'文字对话 · 兼容 Chat Completions',audio_transcriptions:'语音转写 · 兼容 Audio Transcriptions',senseaudio_tts:'语音合成 · 现有语音协议'};

export async function modelApis() {
  if (state.actor?.role !== 'administrator') return {html:'<div class="card"><div class="card-head"><h2>模型接口配置</h2></div><div class="empty">此页面需要系统管理员权限。</div></div>'};
  const {items} = await api(base);
  return {
    html:head("模型接口配置","按用途管理直连接口。修改经测试、发布后生效。",'<a class="button" href="#ai">查看调用记录 →</a>') + `
      <div class="model-api-note">业务智能体仍按已发布的运行策略优先调用中台；这里管理通用能力及原接口兜底。各公司独立配置，当前操作仅影响 <strong>${esc(state.company?.name || '当前公司')}</strong>。</div>
      <div class="model-api-grid">${items.map(item=>{
        const c=item.configuration, mode=c.mode;
        return `<section class="card model-api-card"><div class="card-head"><div><h2>${esc(item.label)}</h2><small>${esc(item.impact)}</small></div><span class="badge ${mode==='disabled'?'red':mode==='custom'?'green':'blue'}">${mode==='custom'?'独立接口':mode==='disabled'?'已停用':'继承默认'}</span></div>
          <dl class="model-api-summary"><div><dt>服务商</dt><dd>${esc(c.provider_name || '未设置')}</dd></div><div><dt>模型</dt><dd>${esc(c.model || '未设置')}</dd></div><div class="full"><dt>请求地址</dt><dd>${esc(c.endpoint_url || '未设置')}</dd></div><div><dt>密钥</dt><dd>${esc(item.key_hint)}</dd></div><div><dt>发布版本</dt><dd>${item.version ? '第 '+item.version+' 版' : '现有基线'}</dd></div></dl>
          <div class="model-api-footer"><small>${item.published_at?'发布于 '+esc(date(item.published_at)):'尚未发布独立配置'}</small><div><button data-connectivity-kind="direct" data-connectivity-target="${item.purpose}" data-connectivity-label="${esc(item.label)}">测试连通性</button><button data-history="${item.purpose}">版本记录</button><button class="primary" data-edit="${item.purpose}">编辑接口</button></div></div></section>`;
      }).join('')}</div>
      <div class="model-api-note">密钥加密保存，不会回显或写入浏览器存储。连接测试使用固定样本，可能产生少量调用费用；通过连接测试不代表所有业务场景已验收。相同协议可直接更换服务商，不同协议需要适配。</div>`,
    bind(root) {
      bindConnectivityTests(root);
      root.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>edit(items.find(i=>i.purpose===b.dataset.edit)));
      root.querySelectorAll('[data-history]').forEach(b=>b.onclick=async()=>{
        const item=items.find(i=>i.purpose===b.dataset.history), company=state.company?.id;
        try {
          const {items:history}=await api(`${base}/${item.purpose}/releases`);
          const d=dialog(`${item.label} · 版本记录`, `<p class="muted">载入历史配置后，需使用当前密钥或新密钥重新测试、发布。不会恢复旧密钥。</p>${history.length?history.map(r=>`<div class="model-api-version"><div><strong>第 ${r.version_no} 版</strong><small>${esc(date(r.created_at))} · ${esc(r.created_by || '管理员')}</small><small>${esc(modes.find(m=>m[0]===r.config_snapshot.mode)?.[1])} · ${esc(r.config_snapshot.model)}</small></div><button type="button" data-restore="${r.version_no}">载入配置</button></div>`).join(''):'<div class="empty">尚无发布记录</div>'}`,{wide:true});
          d.querySelectorAll('[data-restore]').forEach(b=>b.onclick=()=>{
            if(state.company?.id!==company){d.close();return;}
            const r=history.find(v=>v.version_no===Number(b.dataset.restore));d.close();edit(item,r);
          });
        }catch(e){toast(e.message);}
      });
    }
  };
}

function edit(item, restored=null) {
  const company=state.company?.id, c={...(restored?.config_snapshot || item.configuration)};
  let tested=null, requestId=crypto.randomUUID(), testing=false;
  const d=dialog(`${item.label} · 编辑接口`,
    `<div class="model-api-note">${esc(item.impact)}。当前第 ${item.version} 版；测试通过后可发布。${restored?'正在载入第 '+restored.version_no+' 版。':''}</div>
    <div class="form-grid">${field('使用方式','mode',c.mode,{select:options(modes,c.mode,null),full:true})}
    ${field('服务商名称','provider_name',c.provider_name,{required:true})}
    ${field('模型名称','model',c.model,{required:true})}
    ${field('完整请求地址','endpoint_url',c.endpoint_url,{type:'url',required:true,full:true,help:'填写公网 HTTPS 接口完整路径；更换服务商地址时必须重新填写密钥。'})}
    ${field('接口密钥','api_key','',{type:'password',full:true,help:item.has_api_key?'已配置；留空保留当前密钥。':'尚未配置，请填写。'})}
    ${field('单次超时（秒）','timeout_seconds',Math.min(120,Math.max(1,Math.round(c.timeout_seconds))),{type:'number',min:1,max:120,required:true})}
    ${field('失败重试次数','max_retries',Math.min(3,c.max_retries),{type:'number',min:0,max:3,required:true})}
    ${item.purpose==='tts'?field('音色编号','voice_id',c.voice_id,{required:true,full:true}):''}</div>
    <p class="muted">${esc(protocols[c.protocol])}。测试只调用一次，最长等待 45 秒。</p>
    <div class="model-api-test"><button type="button" id="test-connection">测试配置</button><div id="test-result" role="status">修改尚未生效。</div></div>`,
    {wide:true,submit:'发布配置',onSubmit:async()=>{
      if(state.company?.id!==company)throw Error('公司已切换，请重新打开配置');
      if(!tested || tested.status!=='passed')throw Error('请先完成成功测试');
      await api(`${base}/${item.purpose}/publish`,{method:'POST',body:{test_id:tested.id,expected_version:item.version}});
      toast('接口配置已发布，后续调用将使用新配置');
    },afterRender:root=>{
      const f=$('#dialog-form',root), publish=$('button[type=submit]',f), test=$('#test-connection',root), result=$('#test-result',root);
      root.addEventListener('close',()=>{f.elements.api_key.value='';},{once:true});
      const assertContext=()=>{if(state.company?.id!==company)throw Error('公司已切换，请重新打开配置');};
      const updateMode=()=>{
        const custom=f.elements.mode.value==='custom';
        [...f.querySelectorAll('input')].forEach(el=>{el.disabled=!custom;});
        test.textContent=custom?'测试接口':'检查配置';
      };
      const dirty=()=>{tested=null;requestId=crypto.randomUUID();publish.disabled=true;result.textContent='配置已修改，请重新测试。';updateMode();};
      publish.disabled=true;updateMode();
      f.addEventListener('input',dirty);f.addEventListener('change',dirty);
      test.onclick=async()=>{
        if(testing || !f.reportValidity())return;
        testing=true;test.disabled=true;publish.disabled=true;result.textContent='正在测试，请稍候…';
        const data=new FormData(f), custom=data.get('mode')==='custom';
        const config=custom?{mode:'custom',provider_name:data.get('provider_name'),model:data.get('model'),endpoint_url:data.get('endpoint_url'),protocol:c.protocol,timeout_seconds:Number(data.get('timeout_seconds')),max_retries:Number(data.get('max_retries')),voice_id:data.get('voice_id') || ''}:{...c,mode:data.get('mode')};
        const body={request_id:requestId,expected_version:item.version,configuration:config};
        if(custom&&data.get('api_key'))body.api_key=data.get('api_key');
        // Restoring requires an exact old configuration; editing it creates an ordinary new release.
        if(restored && Object.keys(config).every(k=>config[k]===restored.config_snapshot[k]))body.restored_from_version=restored.version_no;
        const generation=requestId;
        try {
          assertContext();
          const response=await api(`${base}/${item.purpose}/tests`,{method:'POST',body});
          if(!d.open || $('#dialog-form')!==f || generation!==requestId)return;
          assertContext();tested=response;
          result.textContent=response.status==='running'?'该测试仍在处理中，可稍后再次检查。':`${response.result.message}（${(response.result.elapsed_ms/1000).toFixed(1)} 秒）`;
          publish.disabled=response.status!=='passed';
          if(response.status!=='running') requestId=crypto.randomUUID();
        } catch(e) {if($('#dialog-form')===f && generation===requestId)result.textContent=e.message;}
        finally {testing=false;test.disabled=false;}
      };
    }});
}
