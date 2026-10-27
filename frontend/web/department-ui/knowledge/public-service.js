import {distributionError, publicErrorCode} from './distribution-model.js';
const TOKEN=/^[A-Za-z0-9_-]{32,128}$/;
export function shareToken(hash = globalThis.location?.hash || '') {
  const params=new URLSearchParams(hash.replace(/^#/,''));
  const value=params.get('token');
  if(!TOKEN.test(value||''))throw Error('分享链接不完整，请联系分享人重新获取');
  return value;
}
export function browserIdentifier(storage=globalThis.localStorage,random=()=>crypto.randomUUID()) {
  const key='salesbuddy:knowledge-visitor';
  try {const saved=storage.getItem(key);if(/^[A-Za-z0-9_-]{16,128}$/.test(saved||''))return saved;}catch{}
  const value=random();try{storage.setItem(key,value);}catch{}
  return value;
}
export function createPublicService(token,fetcher=globalThis.fetch,{idleTimeoutMs=60000}={}) {
  if(!TOKEN.test(token||''))throw Error('分享链接无效');
  async function request(path,{body,key,grant,binary=false,onProgress}={}) {
    let result;
    const controller=new AbortController();
    let timer;
    const reset=()=>{clearTimeout(timer);timer=setTimeout(()=>controller.abort(),binary?idleTimeoutMs:30000);};
    reset();
    try {
      result=await fetcher('/api/v1/public/knowledge'+path,{method:body?'POST':'GET',credentials:'omit',cache:'no-store',redirect:'error',referrerPolicy:'no-referrer',signal:controller.signal,headers:{'X-Knowledge-Share':token,...(grant?{'X-Knowledge-Grant':grant}:{}),...(body?{'Content-Type':'application/json'}:{}),...(key?{'Idempotency-Key':key}:{})},...(body?{body:JSON.stringify(body)}:{})});
    if(!result.ok){let data;try{data=await result.json();}catch{}
      const unknown=Boolean(body)&&(result.status>=500||result.status===408),retryable=!body&&[408,429,500,502,503,504].includes(result.status)&&data?.retryable!==false;
      throw distributionError({serverCode:publicErrorCode(data?.code)||publicErrorCode(data?.detail?.code)||publicErrorCode(data?.detail),statusCode:result.status,
        requestId:data?.request_id||result.headers.get('X-Request-Id'),outcomeUnknown:unknown,retryable,
        recoveryAction:unknown?'check_status':retryable?'retry_read':result.status===401?'login':result.status===403?'contact_admin':'correct_request'});
    }
    if(binary){
      const reader=result.body?.getReader();
      let blob;
      if(reader){
        const chunks=[];let received=0;const total=Number(result.headers.get('Content-Length'))||0;
        try{while(true){const {done,value}=await reader.read();if(done)break;chunks.push(value);received+=value.byteLength;reset();onProgress?.({received,total});}}
        finally{reader.releaseLock();}
        blob=new Blob(chunks,{type:result.headers.get('Content-Type')||'application/octet-stream'});
      }else blob=await result.blob();
      if(!blob.size)throw Error('文件为空，请联系分享人');return blob;
    }
    return await result.json();
    }catch(error){
      if(error.statusCode||error.code==='KNOWLEDGE_QUESTIONNAIRE_CHANGED')throw error;
      throw Object.assign(Error(binary?'文件传输中断，请点击查看正文或下载资料重试，无需重复填写问卷':body?'提交结果尚未确认，请先重新读取处理结果，勿重复提交':'网络连接未完成，请稍后重新读取；不会自动重复提交'),{code:'NETWORK_ERROR',outcomeUnknown:Boolean(body),retryable:!body,recoveryAction:body?'check_status':'retry_read'});
    }finally{clearTimeout(timer);}
  }
  return {
    read:browserId=>request('/share?browser_id='+encodeURIComponent(browserId)),
    respond:(body,key)=>request('/responses',{body,key}),
    cover:()=>request('/cover',{binary:true}),
    file:(fileId,kind,grant,onProgress)=>{
      if(!['preview','download'].includes(kind)||!grant)throw Error('请先完成资料访问问卷');
      return request(`/files/${encodeURIComponent(fileId)}/${kind}`,{grant,binary:true,onProgress});
    },
  };
}
