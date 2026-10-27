import {api, state, esc, head, table, field, options, date, dialog, readForDialog, hasPermission, requirePermission, toast} from './core.js';

const base = '/api/v1/admin/asr-hotwords';
const scenes = [['all','全部语音场景'],['customer_create','客户建档'],['visit_entry','拜访录音'],['management_task','管理任务'],['chatbi','语音问数'],['demo_scene','演示场景'],['visit_import','音频文件导入']];
const categories = [['product','产品名'],['company','公司公开简称'],['industry','行业术语'],['other','其他']];
const sceneName = value => scenes.find(item => item[0] === value)?.[1] || value;
const categoryName = value => categories.find(item => item[0] === value)?.[1] || value;
const cleanItem = item => ({id:item.id || null, term:item.term, category:item.category,
  scenes:[...(item.scenes || ['all'])], enabled:item.enabled !== false, remark:item.remark || ''});
const list = value => Array.isArray(value?.items) ? value.items : [];
const companyName = () => state.company?.name || state.actor?.company_name || '当前公司';
const modeLabel = data => data.model?.available === false ? '已保存配置，语音服务当前不可用'
  : data.model?.supported !== true ? '已保存配置，当前模型未启用热词增强'
  : '保存后用于新开始的语音识别';
const currentContext = () => {
  const company=state.company?.id, actor=JSON.stringify([state.actor?.workspace_id,state.actor?.user_id]), page=state.page;
  return () => company===state.company?.id && actor===JSON.stringify([state.actor?.workspace_id,state.actor?.user_id]) && page===state.page;
};

// Unknown writes remain one operation. Reading a receipt never repeats a model
// call or a configuration mutation, and input remains in the open form.
function mutation(path) {
  let attempt=null;
  return {
    pending:() => !!attempt?.unknown,
    async save(payload) {
      if(attempt?.unknown) throw Error('上次保存结果尚未确认，请先点击“核对保存结果”。输入已保留。');
      attempt={id:crypto.randomUUID(), unknown:false};
      try {
        return await api(path,{method:path===base?'PUT':'POST',key:attempt.id,body:{...payload,operation_id:attempt.id}});
      } catch(error) {
        attempt.unknown=!!error.outcomeUnknown || ['IDEMPOTENCY_IN_PROGRESS','IDEMPOTENCY_UNRESOLVED'].includes(error.code);
        throw error;
      }
    },
    async check() {
      if(!attempt?.unknown) throw Error('当前没有待核对的保存，请先确认并保存。');
      try {
        const result=await api(`${base}/operations/${encodeURIComponent(attempt.id)}`);
        if(result.state && !['completed','succeeded','saved'].includes(result.state)) throw Error('保存结果仍在核对中，请保留输入并稍后再查。');
        attempt.unknown=false;
        return result;
      } catch(error) {
        if(error.statusCode===404) throw Error('暂未查到已完成的保存，请保留输入，稍后再次核对；不会重复提交。');
        throw error;
      }
    },
  };
}

function receiptControls(root, request) {
  const form=root.querySelector('form'), note=root.querySelector('[data-save-note]');
  const check=root.querySelector('[data-check-save]');
  const current=currentContext();
  check.onclick=async()=>{
    if(check.disabled)return;
    check.disabled=true;
    try {
      await request.check();
      if(!current() || root.querySelector('form')!==form || !root.open)return;
      root.close();toast('已确认保存结果');await state.refresh();
    } catch(error) {
      if(current() && root.querySelector('form')===form && root.open) note.textContent=error.message;
    } finally {check.disabled=false;}
  };
}
const saveFooter='<button type="button" data-check-save>核对保存结果</button>';
const saveNote='<p class="help" data-save-note role="status">保存影响当前公司的新识别，不修改历史录音和已开始的任务。</p>';

function edit(initial, item=null) {
  requirePermission('asr_hotword.manage');
  const source=item ? cleanItem(item) : {term:'',category:'product',scenes:['all'],enabled:true,remark:''};
  let snapshot=initial;
  const request=mutation(base), current=currentContext();
  dialog(item?'编辑热词':'新增热词',
    `<p class="notice">当前公司：${esc(companyName())} · 词表 V${esc(initial.version)}。${esc(modeLabel(initial))}。</p>`+
    '<div class="form-grid">'+field('标准词','term',source.term,{required:true,full:true,placeholder:'例如 SalesBuddy'})+
    field('分类','category',source.category,{select:options(categories,source.category,null)})+
    field('状态','enabled',String(source.enabled),{select:options([['true','启用'],['false','停用']],String(source.enabled),null)})+
    '<fieldset class="asr-scenes full"><legend>适用场景</legend>'+scenes.map(([key,label])=>
      `<label><input type="checkbox" name="scene" value="${key}" ${source.scenes.includes(key)?'checked':''}>${label}</label>`).join('')+'</fieldset>'+
    field('备注','remark',source.remark,{type:'textarea',full:true,help:'仅供管理，不作为模型提示词发送。'})+'</div>'+saveNote+
    '<div data-version-note role="status"></div>',{
      submit:initial.model?.supported && initial.model?.available?'保存并生效':'保存配置',wide:true,
      footer:saveFooter+'<button type="button" data-latest>核对最新词表</button>',
      onSubmit:async form=>{
        requirePermission('asr_hotword.manage');
        if(!current())throw Error('公司或页面已切换，请重新打开词表。');
        const selected=form.getAll('scene');
        if(!selected.length)throw Error('请至少选择一个适用场景。');
        if(selected.includes('all') && selected.length>1)throw Error('全部场景不能与单独场景同时选择。');
        const term=String(form.get('term')||'').trim().normalize('NFC');
        if(!term || /[,\r\n\x00-\x1f\x7f]/.test(term))throw Error('标准词不能为空，也不能包含逗号、换行或控制字符。');
        const updated={...source,term,category:form.get('category'),scenes:selected,enabled:form.get('enabled')==='true',remark:String(form.get('remark')||'').trim()};
        if(item && !list(snapshot).some(row=>row.id===item.id))throw Error('此词已被移除，请核对最新词表后重新添加。输入仍保留。');
        const items=list(snapshot).map(row=>row.id===item?.id?updated:cleanItem(row));
        if(!item)items.push(updated);
        await request.save({expected_version:snapshot.version,items,reason:item?'编辑热词':'新增热词'});
        toast('词表已保存');
      },
      afterRender:root=>{
        receiptControls(root,request);
        const form=root.querySelector('form');
        form.elements.term.maxLength=initial.limits?.max_term_length || 64;
        form.elements.remark.maxLength=initial.limits?.max_remark_length || 200;
        root.querySelectorAll('[name=scene]').forEach(input=>input.onchange=()=>{
          if(!input.checked)return;
          root.querySelectorAll('[name=scene]').forEach(other=>{if(other!==input && (input.value==='all' || other.value==='all'))other.checked=false;});
        });
        const button=root.querySelector('[data-latest]');
        button.onclick=async()=>{
          if(request.pending()){root.querySelector('[data-version-note]').textContent='先核对上次保存结果，再读取最新词表。';return;}
          button.disabled=true;
          try {
            const latest=await api(base);
            if(!current() || root.querySelector('form')!==form || !root.open)return;
            const note=root.querySelector('[data-version-note]');
            note.innerHTML=`<p class="notice">最新为 V${esc(latest.version)}，当前编辑基于 V${esc(snapshot.version)}。输入未改变；确认采用最新词表后，只应用本次这一词条的编辑。</p>`+
              table(['标准词','场景','状态'],list(latest),row=>`<tr><td>${esc(row.term)}</td><td>${esc((row.scenes||[]).map(sceneName).join('、'))}</td><td>${row.enabled?'启用':'停用'}</td></tr>`)+
              '<button type="button" data-adopt-version>以此版本为基础继续编辑</button>';
            note.querySelector('[data-adopt-version]').onclick=()=>{
              if(!current() || request.pending())return;
              snapshot=latest;note.textContent=`已采用 V${latest.version} 作为基础，输入保留。请核对后再次保存。`;
            };
          }catch(error){if(current() && root.querySelector('form')===form)root.querySelector('[data-version-note]').textContent=error.message;}
          finally{button.disabled=false;}
        };
      },
    });
}

function change(initial,item,remove=false) {
  requirePermission('asr_hotword.manage');
  const request=mutation(base), current=currentContext();
  const label=remove?'移除':item.enabled?'停用':'启用';
  dialog(`${label}热词`, `<p>确认${label}本公司的“<strong>${esc(item.term)}</strong>”？</p>`+
    '<p class="help">将生成新的词表版本；不删除历史版本，也不重跑已识别录音。</p>'+saveNote,{
      submit:`确认${label}`,footer:saveFooter,
      onSubmit:async()=>{
        requirePermission('asr_hotword.manage');
        if(!current())throw Error('公司已切换，请重新操作。');
        const items=list(initial).filter(row=>!remove || row.id!==item.id).map(row=>
          row.id===item.id?{...cleanItem(row),enabled:!item.enabled}:cleanItem(row));
        await request.save({expected_version:initial.version,items,reason:`${label}热词`});
        toast(`热词已${label}`);
      },afterRender:root=>receiptControls(root,request),
    });
}

async function preview() {
  requirePermission('asr_hotword.read');
  const result=await readForDialog(`${base}/preview?scene=visit_entry`);
  if(!result)return;
  const current=currentContext();
  const render=value=>`<p class="help">词表 V${esc(value.version)} · ${esc(modeLabel(value))}。此预览不上传音频、不调用模型。</p>`+
    `<div class="asr-word-cloud">${(value.terms || value.hotwords || list(value).map(item=>item.term)).map(term=>`<span class="pill">${esc(term)}</span>`).join('') || '<p>此场景没有启用词，将使用普通转写。</p>'}</div>`;
  dialog('热词生效预览', field('业务场景','scene','visit_entry',{select:options(scenes,'visit_entry',null)})+
    `<section data-preview-result aria-live="polite">${render(result)}</section>`,{
      wide:true,afterRender:root=>{
        const form=root.querySelector('form'), output=root.querySelector('[data-preview-result]');
        let sequence=0;
        form.elements.scene.onchange=async()=>{
          const generation=++sequence;
          output.textContent='正在计算本场景词表…';
          try {
            const next=await api(`${base}/preview?scene=${encodeURIComponent(form.elements.scene.value)}`);
            if(current() && root.open && root.querySelector('form')===form && generation===sequence)output.innerHTML=render(next);
          }catch(error){if(current() && root.open && root.querySelector('form')===form && generation===sequence)output.textContent=error.message;}
        };
      },
    });
}

async function history(initial) {
  requirePermission('asr_hotword.read');
  const result=await readForDialog(`${base}/versions`);
  if(!result)return;
  dialog('热词版本记录', '<p class="help">仅显示本公司版本；恢复会生成新版本，不覆盖历史。</p>'+
    table(['版本','修改人 / 时间','原因','操作'],list(result),row=>`<tr><td>V${esc(row.version ?? row.version_no)}</td><td>${esc(row.updated_by || row.created_by || '未记录')}<small>${esc(date(row.updated_at || row.created_at))}</small></td><td>${esc(row.reason)}</td><td><button type="button" data-history-version="${esc(row.version ?? row.version_no)}">查看${hasPermission('asr_hotword.manage')?' / 恢复':''}</button></td></tr>`),{
      wide:true,afterRender:root=>root.querySelectorAll('[data-history-version]').forEach(button=>button.onclick=async()=>{
        try {
          const version=Number(button.dataset.historyVersion);
          const record=await readForDialog(`${base}/versions/${version}`);
          if(!record)return;
          const request=mutation(`${base}/restore`), current=currentContext();
          const old=list(record), now=list(initial);
          const terms=[...new Set([...old,...now].map(item=>item.term))];
          const describe=item=>item?`${item.enabled?'启用':'停用'} · ${(item.scenes||[]).map(sceneName).join('、')} · ${categoryName(item.category)}${item.remark?' · '+item.remark:''}`:'没有此词';
          dialog(`查看词表 V${version}`, `<p class="notice">恢复整个词表将替换当前 V${esc(initial.version)} 的词条、状态和场景，生成新版本。模型、密钥和权限不会恢复。</p>`+
            table(['标准词','当前版本',`历史 V${version}`],terms,term=>`<tr><td>${esc(term)}</td><td>${esc(describe(now.find(item=>item.term===term)))}</td><td>${esc(describe(old.find(item=>item.term===term)))}</td></tr>`)+saveNote,{
              wide:true,submit:'确认恢复为新版本',footer:hasPermission('asr_hotword.manage')?saveFooter:'',
              onSubmit:hasPermission('asr_hotword.manage')?async()=>{
                requirePermission('asr_hotword.manage');if(!current())throw Error('公司已切换，请重新操作。');
                await request.save({expected_version:initial.version,source_version:version});toast('已恢复为新的词表版本');
              }:null,
              afterRender:d=>{if(hasPermission('asr_hotword.manage'))receiptControls(d,request);},
            });
        }catch(error){toast(error.message);}
      }),
    });
}

async function uncertainAttempts() {
  requirePermission('ai.usage_rules_manage');
  const result=await readForDialog(`${base}/unknown-attempts`);
  if(!result)return;
  dialog('待核对的语音调用', '<p class="notice">这些调用可能已发送给语音服务，系统不会自动重复调用。可根据供应商终止证明，或本地发送进程与连接已结束的运维证据，释放本地派发名额；本地连接关闭不代表供应商已停止计算。结果、用量和核对记录都会保留。</p>'+
    table(['操作标识','录入人','发起时间','音频时长','状态','操作'],list(result),row=>`<tr><td>${esc(row.operation_id)}</td><td>${esc(row.actor_id)}</td><td>${esc(date(row.created_at))}</td><td>${esc(Math.round(Number(row.audio_seconds)||0))} 秒</td><td>${row.capacity_released_at?'名额已释放 · 结果待核对':'结果待核对'}</td><td>${row.capacity_released_at?'—':`<button type="button" data-release-attempt="${esc(row.operation_id)}">登记核对结果</button>`}</td></tr>`,'暂无需要核对的调用'),{
      wide:true,afterRender:root=>root.querySelectorAll('[data-release-attempt]').forEach(button=>button.onclick=()=>{
        const row=list(result).find(item=>item.operation_id===button.dataset.releaseAttempt);
        if(!row)return;
        const current=currentContext();
        dialog('登记语音调用核对结果', `<p class="notice">操作 ${esc(row.operation_id)}。请选择已经核实的事实并留下证据。关闭网页或单纯等待超时，不能证明本地发送进程或供应商处理已经结束。仍在发送的请求不能释放；超过10分钟未确认的旧请求，也必须先核实原发送进程与连接已结束。</p>`+
          field('核对依据','evidence','',{type:'textarea',required:true,full:true,help:'记录工单或排查结论，不填写密钥和完整录音内容。'})+
          '<label><input type="radio" name="resolution_kind" value="provider_finished" checked> 供应商已确认处理完成或终止</label>'+
          (['unknown','sending'].includes(row.state)?'<label><input type="radio" name="resolution_kind" value="local_transport_closed"> 本地发送进程与连接已结束（供应商结果仍未知）</label>':'')+
          '<label><input type="checkbox" name="basis_confirmed" required> 我已核实所选事实，并在依据中留下证据</label><p class="help">仅释放本地派发名额；结果仍待核对，用量保留，不自动重新识别。</p>',{
            submit:'登记并释放本地名额',wide:true,onSubmit:async form=>{
              requirePermission('ai.usage_rules_manage');
              if(!current())throw Error('公司已切换，请重新打开调用记录。');
              const evidence=String(form.get('evidence')||'').trim();
              if(!form.get('basis_confirmed') || evidence.length<10)throw Error('请核实所选事实，并填写至少10个字的核对依据。');
              const resolution_kind=form.get('resolution_kind');
              if(!['provider_finished','local_transport_closed'].includes(resolution_kind))throw Error('请选择核对依据。');
              await api(`${base}/attempts/${encodeURIComponent(row.operation_id)}/release-capacity`,{method:'POST',body:{owner_id:row.actor_id,evidence,resolution_kind,[resolution_kind]:true}});
              toast('已登记核对结果，未发起新的识别');
            },
          });
      }),
    });
}

async function budgetPolicy() {
  requirePermission('ai.usage_rules_manage');
  const initial=await readForDialog(`${base}/budget-policy`);
  if(!initial)return;
  const current=currentContext();
  const durationFields=[['daily_seconds','公司每日语音时长（秒）'],['monthly_seconds','公司每月语音时长（秒）'],['user_daily_seconds','每人每日语音时长（秒）']];
  dialog('语音用量与并发控制', '<p class="notice">仅调整当前公司。时长留空表示不额外限制，已有模型额度仍然生效；结果未知的调用继续计入用量，不会自动重发。</p><div class="form-grid">'+
    durationFields.map(([key,label])=>field(label,key,initial[key]??'',{type:'number',help:'留空不额外限制；填写正整数'})).join('')+
    field('公司同时处理数','company_concurrency',String(initial.company_concurrency),{select:options([['1','1'],['2','2'],['3','3'],['4','4']],String(initial.company_concurrency),null)})+
    field('每人同时处理数','user_concurrency',String(initial.user_concurrency),{select:options([['1','1'],['2','2']],String(initial.user_concurrency),null)})+
    '</div><p data-budget-note role="status" class="help">所有语音入口共用以上限制；预览繁忙时跳过，最终识别保留原音频。</p>', {
      wide:true,submit:'保存限制',footer:'<button type="button" data-check-budget>核对当前设置</button>',
      onSubmit:async form=>{
        requirePermission('ai.usage_rules_manage');
        if(!current())throw Error('公司已切换，请重新打开设置。');
        const body={};
        for(const [key] of durationFields){
          const value=String(form.get(key)||'').trim();body[key]=value===''?null:Number(value);
          if(value!=='' && (!Number.isSafeInteger(body[key]) || body[key]<1))throw Error('时长应填写正整数，或留空。');
        }
        body.company_concurrency=Number(form.get('company_concurrency'));
        body.user_concurrency=Number(form.get('user_concurrency'));
        await api(`${base}/budget-policy`,{method:'PUT',body});toast('当前公司的语音用量限制已保存');
      },
      afterRender:root=>{
        const form=root.querySelector('form'), button=root.querySelector('[data-check-budget]');
        button.onclick=async()=>{
          button.disabled=true;
          try{
            const latest=await api(`${base}/budget-policy`);
            if(!current() || !root.open || root.querySelector('form')!==form)return;
            root.querySelector('[data-budget-note]').textContent='服务器当前设置：'+durationFields.map(([key,label])=>`${label}：${latest[key]??'不额外限制'}`).join('；')+`；公司并发 ${latest.company_concurrency}，每人并发 ${latest.user_concurrency}。输入未改变，请核对后决定是否保存。`;
          }catch(error){if(current() && root.open)root.querySelector('[data-budget-note]').textContent=error.message;}
          finally{button.disabled=false;}
        };
      },
    });
}

export async function asrHotwords() {
  requirePermission('asr_hotword.read');
  const data=await api(base), items=list(data), canManage=hasPermission('asr_hotword.manage');
  const counts=items.filter(item=>item.enabled).length;
  return {
    html:head('ASR 热词管理','维护当前公司的产品名、公开简称和行业术语，应用于所有适用语音入口。',
      '<button data-asr-preview>生效预览</button><button data-asr-history>版本记录</button>'+(hasPermission('ai.usage_rules_manage')?'<button data-asr-budget>用量控制</button><button data-asr-unknown>待核对调用</button>':'')+(canManage?'<button class="primary" data-asr-new>新增热词</button>':''))+
      `<section class="card asr-overview"><div><small>当前公司</small><strong>${esc(companyName())}</strong></div><div><small>已保存版本</small><strong>V${esc(data.version)}</strong></div><div><small>启用词 / 全部</small><strong>${counts} / ${items.length}</strong></div><p>${esc(modeLabel(data))}。历史记录和已开始的录音保持原配置。</p></section>`+
      '<section class="card"><div class="toolbar"><input type="search" data-asr-search placeholder="搜索标准词或备注" aria-label="搜索热词"><select data-asr-status aria-label="启用状态"><option value="">全部状态</option><option value="enabled">启用</option><option value="disabled">停用</option></select><select data-asr-scene aria-label="适用场景"><option value="">全部场景</option>'+options(scenes,'',null)+'</select><span data-asr-count></span></div>'+
      '<div data-asr-table></div></section>',
    bind(root) {
      root.querySelector('[data-asr-unknown]')?.addEventListener('click',()=>uncertainAttempts().catch(error=>toast(error.message)));
      root.querySelector('[data-asr-budget]')?.addEventListener('click',()=>budgetPolicy().catch(error=>toast(error.message)));
      root.querySelector('[data-asr-new]')?.addEventListener('click',()=>edit(data));
      root.querySelector('[data-asr-preview]').onclick=()=>preview().catch(error=>toast(error.message));
      root.querySelector('[data-asr-history]').onclick=()=>history(data).catch(error=>toast(error.message));
      const render=()=>{
        const query=root.querySelector('[data-asr-search]').value.trim().toLocaleLowerCase();
        const enabled=root.querySelector('[data-asr-status]').value, scene=root.querySelector('[data-asr-scene]').value;
        const rows=items.filter(item=>(!query || `${item.term} ${item.remark||''}`.toLocaleLowerCase().includes(query)) &&
          (!enabled || !!item.enabled===(enabled==='enabled')) && (!scene || item.scenes?.includes(scene) || (scene!=='all' && item.scenes?.includes('all'))));
        root.querySelector('[data-asr-count]').textContent=`${rows.length} / ${items.length} 个词`;
        root.querySelector('[data-asr-table]').innerHTML=table(['标准词','分类 / 场景','状态','备注','最近修改',...(canManage?['操作']:[])],rows,item=>
          `<tr><td class="title">${esc(item.term)}</td><td>${esc(categoryName(item.category))}<small>${esc((item.scenes||[]).map(sceneName).join('、'))}</small></td><td><span class="badge ${item.enabled?'green':'gray'}">${item.enabled?'启用':'停用'}</span></td><td>${esc(item.remark||'—')}</td><td>${esc(item.updated_by || '—')}<small>${esc(date(item.updated_at))}</small></td>`+
          (canManage?`<td><div class="actions"><button class="link" data-word-edit="${esc(item.id)}">编辑</button><button class="link" data-word-toggle="${esc(item.id)}">${item.enabled?'停用':'启用'}</button><button class="link" data-word-remove="${esc(item.id)}">移除</button></div></td>`:'')+'</tr>',items.length?'暂无匹配的热词':'尚未配置热词，可先添加常用产品名称');
        root.querySelectorAll('[data-word-edit]').forEach(button=>button.onclick=()=>edit(data,items.find(item=>item.id===button.dataset.wordEdit)));
        root.querySelectorAll('[data-word-toggle]').forEach(button=>button.onclick=()=>change(data,items.find(item=>item.id===button.dataset.wordToggle)));
        root.querySelectorAll('[data-word-remove]').forEach(button=>button.onclick=()=>change(data,items.find(item=>item.id===button.dataset.wordRemove),true));
      };
      root.querySelector('[data-asr-search]').oninput=render;
      root.querySelector('[data-asr-status]').onchange=render;
      root.querySelector('[data-asr-scene]').onchange=render;
      render();
    },
  };
}
