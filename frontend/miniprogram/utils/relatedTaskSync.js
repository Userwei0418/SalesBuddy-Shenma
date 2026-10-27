// Refresh one visible task window; never re-read the complete customer history.
function start(page,{active,reader,request,selector='.related-task-live-row'}){
 if(page.stopRelatedTaskSync)page.stopRelatedTaskSync();let stopped=false,timer;
 const visible=()=>!stopped&&active()&&(typeof document==='undefined'||!document.hidden)&&(typeof navigator==='undefined'||navigator.onLine!==false);
 const schedule=()=>{if(!stopped&&typeof setTimeout==='function')timer=setTimeout(tick,3000);};
 async function tick(){
  try {
   const current=reader();if(!visible()||!current||!current.current())return;
   const prior=current.state('tasks');if(!prior.loaded||prior.loading||prior.syncing||prior.syncDenied)return;
   let first=0;
   if(prior.items.length>100&&typeof wx!=='undefined'&&typeof wx.createSelectorQuery==='function'){
    first=await new Promise(resolve=>{const query=wx.createSelectorQuery().in(page);query.selectAll(selector).boundingClientRect(rows=>resolve(Math.max(0,Array.isArray(rows)?rows.findIndex(row=>row.bottom>0):-1))).exec();});
   }
   if(!visible()||current!==reader()||!current.current())return;
   const row=prior.items[first],offset=prior.items.length>100?Math.max(0,Number.isInteger(row&&row.taskListPosition)?row.taskListPosition:first):0;
   await current.refreshTasks(request,{offset,page_size:100});
  }catch(error){const current=reader();if(visible()&&current&&current.current()){const prior=current.state('tasks');current.pages.tasks={...prior,syncError:error.message||'任务同步暂不可用'};current.emit();}}finally{schedule();}
 }
 page.stopRelatedTaskSync=()=>{stopped=true;clearTimeout(timer);};schedule();
}
module.exports={start};
