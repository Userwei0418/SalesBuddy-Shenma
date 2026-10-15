// Read-only, company-approved support. Never turn a platform session into a tenant session.
export function installSupport({api,node,notice}){
  const dialog=document.querySelector('#support-dialog'),body=document.querySelector('#support-body');
  let generation=0,expiryTimer,refreshTimer,selection=null;
  const labels={id:'记录 ID',account_code:'账号',display_name:'姓名',status:'状态',customer_id:'客户 ID',opportunity_id:'商机 ID',name:'名称',
    customer_code:'客户编号',industry_code:'行业',lifecycle_status:'客户状态',level_code:'等级',owner_user_ref_id:'负责人 ID',owner_team_id:'团队 ID',
    updated_at:'更新时间',stage_code:'阶段',amount:'金额',currency:'币种',expected_close_date:'预计关单日期',recorder_user_ref_id:'记录人 ID',
    interaction_at:'沟通时间',follow_up_record:'已确认记录（最多一万字）',next_action:'下一步（最多一万字）',title:'标题',description:'说明（最多一万字）',due_at:'截止时间'};
  function clear(){generation++;clearTimeout(expiryTimer);clearTimeout(refreshTimer);selection=null;body.replaceChildren();}
  function close(){clear();dialog.close();}
  dialog.addEventListener('close',clear);
  dialog.addEventListener('cancel',clear);
  document.querySelector('#close-support').onclick=close;
  document.addEventListener('visibilitychange',()=>{if(document.hidden)close();});
  async function assignments(){
    clear();const ticket=generation;dialog.showModal();body.append(node('p','正在读取公司批准的授权…'));
    try{const data=await api('/support-access');if(ticket!==generation || !dialog.open)return;
      body.replaceChildren(node('p','只显示公司批准给你的授权。平台身份不能自行开通业务访问。'));
      if(!data.items.length)body.append(node('p','当前没有公司批准的临时支持访问。'));
      for(const grant of data.items){const card=node('section');card.append(node('h3',grant.company_name),node('p',grant.reason),
        node('small','批准人：'+grant.approver_name+' · 到期：'+new Date(grant.expires_at).toLocaleString()));
        if(!grant.active)card.append(node('p',grant.revoked_at?'已撤销':'已失效'));
        for(const scope of grant.scopes){const button=node('button',scope.label);button.disabled=!grant.active||!scope.available;
          button.onclick=()=>records(grant,scope);card.append(button);}
        body.append(card);
      }
    }catch(e){if(ticket===generation){body.replaceChildren(node('p',e.message));notice(e.message,true);}}
  }
  function invalid(message){clear();body.replaceChildren(node('p',message));}
  async function checkLease(ticket){
    try{const data=await api('/support-access');if(ticket!==generation || !selection)return;
      const grant=data.items.find(g=>g.id===selection.grant.id);
      if(!grant?.active || !grant.scopes.some(s=>s.code===selection.scope.code&&s.available)){invalid('授权已失效或被撤销，已清空页面内容。');return;}
      refreshTimer=setTimeout(()=>checkLease(ticket),15000);
    }catch(e){if(ticket===generation)invalid(e.message);}
  }
  async function records(grant,scope,after=null){
    clear();selection={grant,scope};const ticket=generation;
    body.replaceChildren(node('p','正在校验授权并读取…'));
    expiryTimer=setTimeout(()=>invalid('授权已到期，已清空页面内容。'),Math.max(0,new Date(grant.expires_at)-Date.now()));
    try{const data=await api('/support-access/'+grant.id+'/records/'+scope.code+(after?'?after_id='+encodeURIComponent(after):''));
      if(ticket!==generation || !dialog.open)return;
      body.replaceChildren(node('h3',grant.company_name+' · '+scope.label),node('p','临时只读访问 · 每页最多五十条 · 不支持修改、导出或下载原件'));
      const back=node('button','返回授权列表');back.onclick=assignments;body.append(back);
      if(!data.items.length)body.append(node('p','当前页没有记录。'));
      for(const row of data.items){const card=node('section');for(const [key,value] of Object.entries(row)){
        const line=node('p');line.append(node('strong',(labels[key]||key)+'：'),node('span',value==null?'—':String(value)));card.append(line);
      }body.append(card);}
      if(data.next_cursor){const next=node('button','下一页');next.onclick=()=>records(grant,scope,data.next_cursor);body.append(next);}
      refreshTimer=setTimeout(()=>checkLease(ticket),15000);
    }catch(e){if(ticket===generation)invalid(e.code==='SUPPORT_ACCESS_DENIED'?'授权不存在、已撤销、已到期或不包含这项功能。':e.message);}
  }
  document.querySelector('#open-support').onclick=assignments;
  return {close};
}
