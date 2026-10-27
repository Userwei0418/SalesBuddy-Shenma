function id(){return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=Math.floor(Math.random()*16);return(c==='x'?r:(r&3)|8).toString(16);});}
function definitions(rows,{allowEmpty=false}={}){
 if(!Array.isArray(rows)||(!allowEmpty&&!rows.length)||rows.length>100)throw Error(allowEmpty?'推进清单最多 100 项':'请填写 1–100 项推进清单');
 return rows.map(row=>{const title=String(row.title||'').trim(),criteria=String(row.acceptance_criteria||'').trim();
  if(!title||[...title].length>500||!criteria||[...criteria].length>2000)throw Error('每项请填写标题与验收标准');
  return {item_id:id(),definition_version:1,title,acceptance_criteria:criteria};});
}
module.exports={id,definitions};
