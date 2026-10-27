import React,{useEffect,useRef,useState} from 'react';
import {Alert,Button,Checkbox,Drawer,Select} from 'antd';
import {Input} from '../ImeInput.jsx';
import {identity} from '../pc/data.js';
import {LoadState,Panel,Tag2} from '../pc/ui.jsx';
import {useKnowledgeRead} from './hooks.js';
import {newRequestKey} from './model.js';
import {QUESTION_FIELDS,shareUrl} from './distribution-service.js';
const unknownError=error=>!error.statusCode||error.statusCode>=500;
const externallyScoped=row=>['public','customer'].includes(row?.contribution?.selection?.content_scope);
const defaults=()=>({enabled:true,fields:QUESTION_FIELDS.map(row=>({name:row.name,enabled:true,required:row.name!=='contact_intent'}))});
export default function DistributionDrawer({service,materialService,materialId,onClose,onChanged=()=>{}}) {
  const actor=identity(),alive=useRef(true),flight=useRef(false),intent=useRef(null),shareIntent=useRef(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[unknown,setUnknown]=useState(false),[link,setLink]=useState('');
  const [draft,setDraft]=useState(null);
  const detail=useKnowledgeRead(()=>Promise.all([service.getPublication(materialId),materialService.getMaterial(materialId)]),[service,materialService,materialId]);
  const publication=detail.value?.[0],material=detail.value?.[1];
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  const current=()=>alive.current&&actor===identity();
  useEffect(()=>{if(!publication||intent.current)return;setDraft({version_id:publication.version_id||material?.versions?.[0]?.version_id||null,description:publication.description||'',cover_file_id:publication.cover_file_id,allow_preview:publication.allow_preview,allow_download:publication.allow_download,questionnaire:publication.questionnaire||defaults()});},[publication,material]);
  const version=material?.versions?.find(row=>row.version_id===draft?.version_id),files=version?.files||[];
  const edit=value=>{if(!busy&&!unknown){setDraft(previous=>({...previous,...value}));setNotice('');}};
  const save=async enabled=>{
    if(flight.current||!current()||!publication?.can_publish)return;
    if(!intent.current)intent.current={body:{...draft,expected_revision:publication.revision,enabled},key:newRequestKey()};
    flight.current=true;setBusy(true);setError('');setNotice('');
    try {
      const receipt=await service.publish(materialId,intent.current.body,intent.current.key);
      if(!current())return;
      if(receipt.enabled!==intent.current.body.enabled||receipt.version_id!==intent.current.body.version_id||receipt.revision<=intent.current.body.expected_revision)throw Error('发布回执尚未确认，请确认原操作');
      intent.current=null;setUnknown(false);setLink('');shareIntent.current=null;setNotice(receipt.enabled?'可对客版本已发布，分享将绑定此版本。':'资料已下架，客户链接停止访问。');detail.reload();onChanged();
    }catch(failure){if(current()){setError(failure.message);const pending=unknownError(failure);setUnknown(pending);if(!pending){intent.current=null;if(failure.code==='KNOWLEDGE_PUBLICATION_CONFLICT')detail.reload();}}}
    finally{flight.current=false;if(current())setBusy(false);}
  };
  const copy=async()=>{
    if(flight.current||!current()||unknown||!publication?.can_share)return;
    flight.current=true;setBusy(true);setError('');setNotice('');
    try {
      const fresh=await service.getPublication(materialId);if(!current())return;
      if(!fresh.can_share||!fresh.enabled||fresh.publication_id!==publication.publication_id){setLink('');shareIntent.current=null;detail.reload();throw Error('可分享版本已变化，请核对后重新获取');}
      if(!shareIntent.current)shareIntent.current={publicationId:fresh.publication_id,key:newRequestKey()};
      const receipt=await service.share(materialId,shareIntent.current.publicationId,shareIntent.current.key);if(!current())return;
      const href=shareUrl(receipt);setLink(href);
      try{await navigator.clipboard.writeText(href);if(current())setNotice('客户分享链接已复制，链接绑定当前销售和资料版本。');}catch{if(current())setNotice('分享链接已生成，请从下方选中复制。');}
    }catch(failure){if(current())setError(failure.message);}
    finally{flight.current=false;if(current())setBusy(false);}
  };
  const locked=busy||unknown;
  return <Drawer open width={720} rootClassName="department-ui kb122-drawer" title="对客发布与分享" onClose={busy?undefined:onClose} extra={<Button disabled={locked} onClick={()=>{setError('');setNotice('');detail.reload();}}>重新读取</Button>}>
    <LoadState loading={detail.loading} error={detail.error} onRetry={detail.reload}>{publication&&draft&&<>
      <h2>{publication.content_name}</h2><p><Tag2 tone={publication.enabled?'success':'neutral'}>{publication.enabled?`对客版本 V${publication.version_no}`:'尚未对客发布'}</Tag2></p>
      <Alert type="info" showIcon title="内部资料与对客版本分别授权" description="仅发布已归档文件；原表链接和附件描述不能直接对客下载。每个分享链接绑定分享销售和发布版本。更新发布设置或问卷后需重新生成链接；下架、撤销分享或撤回资料权限后停止访问。" />
      {error&&<Alert type="error" showIcon title={error}/>} {notice&&<Alert type="success" showIcon title={notice}/>}
      {unknown&&<Alert type="warning" showIcon title="原发布结果尚未确认" description="设置已暂时锁定。确认原操作会沿用同一个请求标识，不会自动创建另一次发布。"/>}
      {publication.can_publish?<Panel title="可对客版本设置"><div className="kb-distribution-form">
        <label>资料版本<Select aria-label="对客资料版本" disabled={locked} value={draft.version_id} options={(material?.versions||[]).map(row=>({value:row.version_id,label:`V${row.version_no} · ${row.files?.length||0} 个归档文件${externallyScoped(row)?'':' · 非对客范围'}`,disabled:!row.files?.length||!externallyScoped(row)}))} onChange={value=>edit({version_id:value,cover_file_id:null})}/></label>
        {version&&!externallyScoped(version)&&<Alert type="warning" title="此版本范围为仅内部或伙伴，不能直接对客发布；请在材料详情新增明确的对客版本。"/>}
        {!files.length&&<Alert type="warning" title="此版本没有已归档文件，请先在材料详情补齐并保存新版本。"/>}
        <label>对客简介<Input.TextArea aria-label="对客简介" maxLength={2000} rows={3} disabled={locked} value={draft.description} onChange={event=>edit({description:event.target.value})}/></label>
        <label>封面<Select aria-label="对客封面" allowClear disabled={locked} value={draft.cover_file_id||undefined} placeholder="默认文字封面" options={files.filter(row=>['image/png','image/jpeg','image/webp'].includes(row.media_type)&&row.size_bytes<=2*1024*1024).map(row=>({value:row.file_id,label:row.filename}))} onChange={value=>edit({cover_file_id:value||null})}/></label>
        <p className="pc-muted">可选择本版本已归档的 PNG、JPEG、WebP 图片（不超过 2 MB）作为封面。</p>
        <Checkbox disabled={locked} checked={draft.allow_preview} onChange={event=>edit({allow_preview:event.target.checked})}>允许安全文本正文预览</Checkbox>
        <Checkbox disabled={locked} checked={draft.allow_download} onChange={event=>edit({allow_download:event.target.checked})}>允许客户下载</Checkbox>
        <p className="pc-muted">PDF 和文本支持在线预览；PPT、视频通过下载查看。正文和下载仍按问卷及发布权限开放。</p>
        <h3>轻问卷模板</h3><Checkbox disabled={locked} checked={draft.questionnaire.enabled} onChange={event=>edit({questionnaire:{...draft.questionnaire,enabled:event.target.checked}})}>正文 / 下载前填写问卷</Checkbox>
        {QUESTION_FIELDS.map(field=>{const definition=draft.questionnaire.fields.find(row=>row.name===field.name)||{name:field.name,enabled:false,required:false};const patch=value=>edit({questionnaire:{...draft.questionnaire,fields:QUESTION_FIELDS.map(row=>row.name===field.name?{...definition,...value}:draft.questionnaire.fields.find(item=>item.name===row.name)||{name:row.name,enabled:false,required:false})}});return <div className="kb122-file-row" key={field.name}><Checkbox disabled={locked||!draft.questionnaire.enabled} checked={definition.enabled} onChange={event=>patch({enabled:event.target.checked,required:event.target.checked&&definition.required})}>{field.label}</Checkbox><Checkbox disabled={locked||!draft.questionnaire.enabled||!definition.enabled} checked={definition.required} onChange={event=>patch({required:event.target.checked})}>必填</Checkbox></div>;})}
        <div className="kb122-drawer-actions">{unknown?<Button type="primary" loading={busy} onClick={()=>save(intent.current?.body.enabled)}>确认原操作</Button>:<><Button disabled={busy||!publication.enabled} onClick={()=>save(false)}>下架资料</Button><Button type="primary" loading={busy} disabled={!files.length||!externallyScoped(version)||(!draft.allow_preview&&!draft.allow_download)} onClick={()=>save(true)}>{publication.enabled?'保存并更新对客版本':'发布可对客版本'}</Button></>}</div>
      </div></Panel>:<Panel title="当前对客设置"><p>{publication.description||'暂无简介'}</p><p>{publication.questionnaire.enabled?'客户需先填写问卷，再查看正文或下载。':'本资料无需填写问卷。'}</p><p className="pc-muted">当前账号无发布管理权限，可分享服务端允许的已发布版本。</p></Panel>}
      <Panel title="销售个人分享"><Button type="primary" loading={busy} disabled={unknown||!publication.can_share||!publication.enabled} onClick={copy}>复制客户分享链接</Button>{!publication.can_share&&<p className="pc-muted">此资料目前不可分享，请核对发布状态和资料权限。</p>}{link&&<Input.TextArea aria-label="客户分享链接" readOnly value={link} autoSize onFocus={event=>event.target.select()}/>}</Panel>
    </>}</LoadState>
  </Drawer>;
}
