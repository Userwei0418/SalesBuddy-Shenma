const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const STATUSES=['queued','processing','succeeded','failed'];
function newKey(){return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=Math.floor(Math.random()*16);return (c==='x'?r:(r&3)|8).toString(16);});}
function validate(receipt,key){
 const invalid=()=>{throw Object.assign(Error('上传回执未能确认，本地文件已保留，请继续查回'),{code:'UPLOAD_RECEIPT_INVALID'});};
 if(!UUID.test(key||'')||!receipt||receipt.upload_intent_id!==key||typeof receipt.filename!=='string'||!receipt.filename.trim())return invalid();
 if(receipt.state==='registered'){
  if(!UUID.test(receipt.id||'')||!STATUSES.includes(receipt.status))return invalid();
 }else if(['processing','uncertain','retryable_failure'].includes(receipt.state)){
  if(receipt.id!==null||receipt.status!==null||receipt.retryable!==(receipt.state==='retryable_failure'))return invalid();
 }else return invalid();
 return receipt;
}
module.exports={newKey,validate,UUID,STATUSES};
