import {state, esc, api, head, field, dialog, table, toast} from './core.js';
const labels={organization:'组织目录',customers:'客户基本资料',opportunities:'商机基本资料',visits:'已确认跟进记录',tasks:'待办记录'};
const when=value=>value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—';
const reasons={APPROVED_READ:'按公司批准的范围读取',SUPPORT_ACCESS_DENIED:'授权无效或超出批准范围',TENANT_INACTIVE:'公司暂停或关闭，授权已撤销'};
const actions={approved:'批准',revoked:'撤销',read:'只读访问',denied:'拒绝访问'};
function approveDialog(){
  let verified=null,sequence=0;
  const id=crypto.randomUUID();
  const expiry=new Date(Date.now()+3600000);
  const localExpiry=new Date(expiry.getTime()-expiry.getTimezoneOffset()*60000).toISOString().slice(0,16);
  dialog('批准临时只读支持',
    '<p class="help">仅授予所选类别的本公司全部有效记录读取权；不创建公司账号，不允许修改、导出或下载原件。最长二十四小时，可随时撤销。</p>'+
    field('支持人员的平台账号','operator_account','',{required:true})+
    '<button type="button" data-verify-support>核对支持人员</button><div data-support-person role="status"></div><div data-support-scopes></div>'+
    field('到期时间','expires_at',localExpiry,{type:'datetime-local',required:true})+
    field('批准原因','reason','',{type:'textarea',required:true}),{
      submit:'确认批准',wide:true,
      onSubmit:async(form)=>{
        if(!verified || form.get('operator_account').trim().toUpperCase()!==verified.account_code)throw Error('请先核对支持人员的平台账号和姓名');
        const scopes=form.getAll('scope');
        if(!scopes.length)throw Error('请选择至少一项只读功能');
        await api('/support-access',{method:'POST',body:{id,operator_id:verified.id,scopes,
          expires_at:new Date(form.get('expires_at')).toISOString(),reason:form.get('reason')}});
      },
      afterRender:root=>{
        const account=root.querySelector('[name=operator_account]');
        const person=root.querySelector('[data-support-person]'),scopes=root.querySelector('[data-support-scopes]');
        const clear=()=>{sequence++;verified=null;person.textContent='';scopes.replaceChildren();};
        account.oninput=clear;
        root.querySelector('[data-verify-support]').onclick=async()=>{
          clear();const current=sequence,form=root.querySelector('form');
          try{
            const data=await api('/support-access/directory?account='+encodeURIComponent(account.value.trim()));
            if(current!==sequence || !root.open || root.querySelector('form')!==form)return;
            verified=data.operator;
            person.textContent=verified?`${verified.display_name} · ${verified.account_code}（已启用并绑定 MFA）`:'未找到已启用且完成 MFA 的平台账号';
            if(verified)scopes.innerHTML='<p>批准查看的功能（均为本公司全部有效记录）：</p>'+data.scopes.map(s=>
              `<label class="support-scope"><input type="checkbox" name="scope" value="${esc(s.code)}" ${s.available?'':'disabled'}><span>${esc(s.label)}${s.available?'':'（当前管理员无完整读取权限）'}</span></label>`).join('');
          }catch(e){if(current===sequence && root.open && root.querySelector('form')===form)person.textContent=e.message;}
        };
      },
    });
}
export async function supportAccess(){
  const [grants,audit]=await Promise.all([api('/support-access'),api('/support-access/audit')]);
  const auditRows=rows=>table(['时间','支持人员','动作','功能','返回条数','原因'],rows,r=>
    `<tr><td>${esc(when(r.created_at))}</td><td>${esc(r.display_name)}<small>${esc(r.account_code)}</small></td><td>${esc(actions[r.action])}</td><td>${esc(labels[r.scope_code]||'—')}</td><td>${esc(r.row_count)}</td><td>${esc(reasons[r.reason]||r.reason)}</td></tr>`);
  let cursor=audit.items.at(-1)?.id;
  return {html:head('临时支持访问','默认关闭。只有本公司批准的人员、功能和时间窗口可以读取业务记录。','<button class="primary" id="approve-support">批准临时支持</button>')+
    '<section class="card"><div class="card-head"><h2>有效授权与最近历史（一百条内）</h2></div>'+table(['支持人员','只读范围','有效期','批准人','状态','操作'],grants.items,g=>
      `<tr><td>${esc(g.display_name)}<small>${esc(g.account_code)}</small></td><td>${esc(g.scopes.map(s=>labels[s]).join('、'))}<small>${esc(g.reason)}</small></td><td>${esc(when(g.approved_at))}<br>至 ${esc(when(g.expires_at))}</td><td>${esc(g.approver_name)}</td><td>${g.revoked_at?'已撤销':g.active?'有效':'已失效'}</td><td>${!g.revoked_at?`<button type="button" class="link" data-revoke-support="${esc(g.id)}">撤销</button>`:'—'}</td></tr>`, '尚未批准任何支持访问')+'</section>'+
    '<section class="card"><div class="card-head"><h2>支持访问审计</h2></div><div id="support-audit">'+auditRows(audit.items)+'</div><button id="older-support-audit" '+(audit.items.length<100?'hidden':'')+'>查看更早记录</button></section>',
    bind:root=>{
      root.querySelector('#approve-support').onclick=approveDialog;
      root.querySelectorAll('[data-revoke-support]').forEach(button=>button.onclick=()=>dialog('撤销支持访问',
        '<p>撤销提交后，后续读取立即被拒绝；已经查看的内容无法收回。</p>'+field('撤销原因','reason','',{type:'textarea',required:true}),{
          submit:'确认撤销',onSubmit:async form=>api('/support-access/'+button.dataset.revokeSupport+'/revoke',{method:'POST',body:{reason:form.get('reason')}}),
        }));
      root.querySelector('#older-support-audit').onclick=async event=>{
        const button=event.currentTarget;button.disabled=true;
        try{const data=await api('/support-access/audit?before_id='+cursor);if(!button.isConnected || state.page!=='supportAccess')return;
          cursor=data.items.at(-1)?.id;root.querySelector('#support-audit').innerHTML=auditRows(data.items);button.hidden=data.items.length<100;
        }catch(e){toast(e.message);}finally{button.disabled=false;}
      };
    },
  };
}
