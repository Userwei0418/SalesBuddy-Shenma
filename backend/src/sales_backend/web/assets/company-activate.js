const form=document.querySelector('#activation-form'),status=document.querySelector('#activation-status');
const company=new URLSearchParams(window.location.search).get('company')||'';
if(/^[a-z0-9][a-z0-9-]{2,47}$/.test(company)){
 form.elements.company_code.value=company;
 document.querySelector('#company-login-link').href='/admin?company='+encodeURIComponent(company);
}
let busy=false;
form.onsubmit=async event=>{
 event.preventDefault();if(busy)return;
 const data=Object.fromEntries(new FormData(form));
 if(data.new_password!==data.confirm_password){status.textContent='两次输入的密码不一致。';return;}
 delete data.confirm_password;data.company_code=data.company_code.trim().toLowerCase();
 busy=true;const button=form.querySelector('button');button.disabled=true;status.textContent='正在激活…';
 try{
  const response=await fetch('/api/v1/platform/activate',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok)throw Object.assign(Error(response.status===429?'尝试较多，请稍后重试。':result.detail==='ACTIVATION_INVALID_OR_EXPIRED'?'激活码无效或已过期，请联系平台重新生成。':'激活未完成，请检查填写内容或联系平台。'),{confirmed:true});
  document.querySelector('#company-login-link').href='/admin?company='+encodeURIComponent(data.company_code);
  form.reset();form.hidden=true;document.querySelector('#activation-success').hidden=false;
 }catch(error){status.textContent=error.confirmed?error.message:'未收到完整激活结果。请先尝试公司登录，或联系平台核对是否已激活，不要反复提交一次性激活码。';}
 finally{busy=false;button.disabled=false;}
};
