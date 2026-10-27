import React,{useEffect,useRef,useState} from 'react';
import {QUESTION_FIELDS} from './distribution-model.js';
import {browserIdentifier,createPublicService,shareToken} from './public-service.js';
import './public-share.css';
import FilePreview from './FilePreview.jsx';
const emptyAnswers=()=>Object.fromEntries(QUESTION_FIELDS.map(field=>[field.name,'']));
const uuid=()=>crypto.randomUUID();
export default function PublicShare({service:injected,visitorId:injectedVisitor}) {
  const [boot]=useState(()=>{try{return {service:injected||createPublicService(shareToken()),visitor:injectedVisitor||browserIdentifier()};}catch(error){return {error:error.message};}});
  const [value,setValue]=useState(null),[error,setError]=useState(boot.error||''),[loading,setLoading]=useState(!boot.error),[cover,setCover]=useState('');
  const [answers,setAnswers]=useState(emptyAnswers),[form,setForm]=useState(false),[busy,setBusy]=useState(false),[grant,setGrant]=useState(null),[preview,setPreview]=useState(null),[unknown,setUnknown]=useState(false);
  const mounted=useRef(true),serial=useRef(0),flight=useRef(false),intent=useRef(null),action=useRef(null),coverUrl=useRef('');
  const [transfer,setTransfer]=useState(null);
  const filePending=useRef(false),downloadCleanups=useRef(new Set());
  const clearDownloads=()=>{for(const cleanup of downloadCleanups.current)cleanup();downloadCleanups.current.clear();};
  const closePreview=()=>{serial.current++;flight.current=false;filePending.current=false;setBusy(false);setPreview(null);clearDownloads();};
  const current=id=>mounted.current&&id===serial.current;
  const read=async()=>{
    if(!boot.service||(flight.current&&!filePending.current)||intent.current)return;
    const id=++serial.current;flight.current=false;filePending.current=false;setBusy(false);clearDownloads();if(coverUrl.current)URL.revokeObjectURL(coverUrl.current);coverUrl.current='';setCover('');setValue(null);setLoading(true);setError('');setGrant(null);setPreview(null);setForm(false);setUnknown(false);intent.current=null;action.current=null;
    try {
      const row=await boot.service.read(boot.visitor);
      if(!current(id))return;
      if(!row?.publication_id||!Number.isInteger(row.questionnaire_version)||!Array.isArray(row.questionnaire?.fields)||!Array.isArray(row.files)||row.files.some(file=>!file.file_id))throw Error('资料信息不完整，请联系分享人');
      setValue(row);
      if(coverUrl.current)URL.revokeObjectURL(coverUrl.current);coverUrl.current='';setCover('');
      if(row.has_cover){const blob=await boot.service.cover();if(!current(id))return;if(['image/png','image/jpeg','image/webp'].includes(blob.type)){coverUrl.current=URL.createObjectURL(blob);setCover(coverUrl.current);}}
    }catch(failure){if(current(id)){setError(failure.message);setValue(null);}}
    finally{if(current(id))setLoading(false);}
  };
  useEffect(()=>{mounted.current=true;read();return()=>{mounted.current=false;serial.current++;clearDownloads();if(coverUrl.current)URL.revokeObjectURL(coverUrl.current);};},[boot]);
  async function fileAccess(file,kind,access) {
    if(!access||access.publication_id!==value.publication_id||Date.parse(access.expires_at)<=Date.now()){setGrant(null);throw Error('访问授权已过期，请重新填写问卷');}
    const id=serial.current;
    filePending.current=true;setTransfer({received:0,total:file.size_bytes});
    if(kind==='preview')setPreview({name:file.filename,blob:null});
    try {
      const blob=await boot.service.file(file.file_id,kind,access.grant_token,progress=>{if(current(id))setTransfer({...progress,total:progress.total||file.size_bytes});});
      if(!current(id))return;
      if(kind==='preview')setPreview({name:file.filename,blob});
      else {
        const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=file.filename||'资料';document.body.appendChild(link);link.click();link.remove();
        const cleanup=()=>{clearTimeout(timer);URL.revokeObjectURL(url);downloadCleanups.current.delete(cleanup);};
        const timer=setTimeout(cleanup,60000);downloadCleanups.current.add(cleanup);
      }
    }catch(failure){if(current(id))setPreview(null);throw failure;}
    finally{if(current(id)){filePending.current=false;setTransfer(null);}}
  }

  async function submit(event) {
    event?.preventDefault();if(flight.current||!value||!action.current)return;
    const body={browser_id:boot.visitor,questionnaire_version:value.questionnaire_version,answers:value.questionnaire.enabled?Object.fromEntries(QUESTION_FIELDS.map(field=>[field.name,value.questionnaire.fields.find(row=>row.name===field.name)?.enabled?String(answers[field.name]||'').trim():''])):{}};
    if(!intent.current)intent.current={body,key:uuid()};
    flight.current=true;setBusy(true);setError('');const id=serial.current;
    try {
      const receipt=await boot.service.respond(intent.current.body,intent.current.key);
      if(!current(id))return;
      if(receipt?.publication_id!==value.publication_id||receipt.questionnaire_version!==value.questionnaire_version||!receipt.grant_token||!Number.isFinite(Date.parse(receipt.expires_at)))throw Object.assign(Error('提交结果尚未确认，请确认原提交'),{code:'NETWORK_ERROR'});
      intent.current=null;setUnknown(false);setGrant(receipt);setForm(false);setAnswers(emptyAnswers());
      const selected=action.current;
      try {await fileAccess(selected.file,selected.kind,receipt);} catch (fileError) {if(current(id)){setError(fileError.message);if(fileError.statusCode===403){setGrant(null);setForm(value.questionnaire.enabled);}}}
    }catch(failure){if(current(id)){setError(failure.message);setUnknown(failure.code==='NETWORK_ERROR'||failure.statusCode>=500);if(failure.code==='KNOWLEDGE_QUESTIONNAIRE_REQUIRED')setGrant(null);if(failure.statusCode&&failure.statusCode<500)intent.current=null;}}
    finally{if(current(id)){flight.current=false;setBusy(false);}}
  }
  async function access(file,kind) {
    if(flight.current||unknown||!value)return;
    setPreview(null);action.current={file,kind};setError('');
    if(!grant||Date.parse(grant.expires_at)<=Date.now()){
      if(value.questionnaire.enabled){setForm(true);return;}
      await submit();return;
    }
    flight.current=true;setBusy(true);const id=serial.current;
    try{await fileAccess(file,kind,grant);}catch(failure){if(current(id)){setError(failure.message);if(failure.statusCode===403){setGrant(null);setForm(true);}}}finally{if(current(id)){flight.current=false;setBusy(false);}}
  }
  return <main className="kb-public"><header className="kb-public-brand">神码 SalesBuddy <span>资料分享</span></header><button disabled={loading||unknown||(busy&&!filePending.current)} onClick={read}>重新读取资料</button>
    {loading?<section className="kb-public-card" role="status">正在读取资料…</section>:value&&<>
      <section className="kb-public-cover">{cover&&<img src={cover} alt="资料封面"/>}<div><span className="kb-public-eyebrow">精选资料 · V{value.version_no}</span><h1>{value.content_name}</h1><p>{value.description||'查看资料内容与附件。'}</p><small>{value.requires_questionnaire?'填写简短信息后，可查看正文或下载；同次授权无需重复填写。':'可按本页授权查看资料。'}</small></div></section>
      <section className="kb-public-card"><h2>资料文件</h2>{value.files.length?value.files.map(file=><div className="kb-public-file" key={file.file_id}><div><b>{file.filename}</b><small>{Math.ceil(file.size_bytes/1024)} KB{!file.preview_supported?' · 此格式请下载查看':''}</small></div><div className="kb-public-actions">{value.allow_preview&&file.preview_supported&&<button disabled={busy||unknown} onClick={()=>access(file,'preview')}>查看正文</button>}{value.allow_download&&<button className="primary" disabled={busy||unknown} onClick={()=>access(file,'download')}>下载资料</button>}</div></div>):<p>暂无可访问文件，请联系分享人。</p>}</section>
      {form&&<form className="kb-public-card" onSubmit={submit}><h2>填写资料访问问卷</h2><p>信息将用于本次资料服务及你所选择的联系意向，由分享销售查看。</p>{QUESTION_FIELDS.filter(field=>value.questionnaire.fields.find(row=>row.name===field.name)?.enabled).map(field=>{const definition=value.questionnaire.fields.find(row=>row.name===field.name);return <label key={field.name}>{field.label}{definition.required&&<span> *</span>}<input aria-label={field.label} value={answers[field.name]} disabled={busy||unknown} required={definition.required} maxLength={field.maxLength} autoComplete={field.name==='name'?'name':field.name==='company'?'organization':'off'} placeholder={field.name==='contact_intent'?'例如：希望了解方案、预约演示，或暂不联系':''} onChange={event=>{setAnswers(previous=>({...previous,[field.name]:event.target.value}));intent.current=null;}}/></label>;})}<button className="primary" type="submit" disabled={busy}>{busy?'正在处理…':unknown?'确认原提交':'提交并访问资料'}</button></form>}
      {busy&&transfer&&<section className="kb-public-card" role="status">正在读取文件：已接收 {Math.round(transfer.received/1024)} KB{transfer.total>0?` / ${Math.ceil(transfer.total/1024)} KB`:''}，请保持页面打开。</section>}
      {preview&&<FilePreview {...preview} onClose={closePreview}/>}
    </>}
    {error&&<section className="kb-public-error" role="alert"><p>{error}</p>{!busy&&(unknown ? !form&&<button onClick={submit}>确认原提交</button> : null)}</section>}
    {!loading&&!value&&!error&&<section className="kb-public-card">此分享暂不可用。</section>}
    <footer>下载按钮发起文件请求；是否保存到设备，请以浏览器提示为准。<br/>资料下架或分享撤销后，将停止新的访问授权。</footer>
  </main>;
}
