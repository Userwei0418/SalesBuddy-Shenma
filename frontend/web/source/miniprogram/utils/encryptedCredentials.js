// Passwords use only WeChat's asynchronous encrypted storage (base library 2.21.3+).
// Account labels live separately; a storage failure never falls back to plaintext.
const PREFIX = 'salesEncryptedCredentialV1:';
const queues = new Map();
const versions = new Map();
let generation = 0;
function available() {
  try {
    if (typeof wx.setStorage !== 'function' || typeof wx.getStorage !== 'function') return false;
    const info = typeof wx.getAppBaseInfo === 'function' ? wx.getAppBaseInfo() : typeof wx.getSystemInfoSync === 'function' ? wx.getSystemInfoSync() : {};
    const parts = String(info.SDKVersion || '').split('.').map(Number);
    if (parts.length < 3 || parts.some(n => !Number.isInteger(n))) return false;
    const minimum = [2,21,3];
    for (let i=0;i<3;i++) { if(parts[i]>minimum[i])return true;if(parts[i]<minimum[i])return false; }
    return true;
  } catch (_) { return false; }
}
function identity(value) {
  const baseUrl=String(value?.baseUrl || '').replace(/\/$/,''), workspace=String(value?.workspace || '').trim().toLowerCase(),account=String(value?.account || '').trim().toUpperCase();
  if(!baseUrl || !workspace || !account || workspace.length>128 || account.length>128)return null;
  return {baseUrl,workspace,account};
}
function keyOf(value) { const item=identity(value);return item ? PREFIX+encodeURIComponent(JSON.stringify([item.baseUrl,item.workspace,item.account])) : null; }
function run(key,action) {
  const previous=queues.get(key) || Promise.resolve();
  const next=previous.catch(()=>undefined).then(action);
  queues.set(key,next);
  next.finally(()=>{if(queues.get(key)===next)queues.delete(key);}).catch(()=>undefined);
  return next;
}
function remove(key) {
  return new Promise((resolve,reject)=>{
    if(typeof wx.removeStorage==='function')wx.removeStorage({key,success:resolve,fail:()=>reject(Error('无法清除本机保存的密码，请重试'))});
    else {try{wx.removeStorageSync(key);resolve();}catch(_){reject(Error('无法清除本机保存的密码，请重试'));}}
  });
}
function invalidate() { generation+=1; }
function clear(value) {
  const key=keyOf(value);if(!key)return Promise.resolve();
  versions.set(key,(versions.get(key)||0)+1);
  return run(key,()=>remove(key));
}
function read(value,current=()=>true) {
  const expected=identity(value),key=keyOf(value),epoch=generation,version=versions.get(key)||0;
  if(!key || !available())return Promise.resolve(null);
  const live=()=>epoch===generation && version===(versions.get(key)||0) && current();
  return run(key,()=>new Promise(resolve=>{
    if(!live())return resolve(null);
    wx.getStorage({key,encrypt:true,success:result=>{
      const stored=result.data,actual=identity(stored);
      if(!live() || stored?.version!==1 || JSON.stringify(actual)!==JSON.stringify(expected) || typeof stored.password!=='string' || !stored.password || stored.password.length>128)return resolve(null);
      resolve(stored.password);
    },fail:()=>resolve(null)});
  }));
}
function save(value,password,current=()=>true) {
  const item=identity(value),key=keyOf(value),epoch=generation,version=versions.get(key)||0;
  if(!key || !available() || typeof password!=='string' || !password || password.length>128)return Promise.resolve(false);
  const live=()=>epoch===generation && version===(versions.get(key)||0) && current();
  return run(key,async()=>{
    if(!live())return false;
    try {
      await new Promise((resolve,reject)=>wx.setStorage({key,encrypt:true,data:{version:1,...item,password},success:resolve,fail:()=>reject(Error('密码未保存，请下次手动输入'))}));
    } catch (error) {
      // An uncertain failed write must not leave an unacknowledged credential.
      await remove(key);throw error;
    }
    if(!live()){await remove(key);return false;}
    return true;
  });
}
module.exports={available,identity,keyOf,read,save,clear,invalidate};
