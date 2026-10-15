// Memory only: reuse requests/results within an authenticated session. Server
// fingerprints remain authoritative; cached previews must be revalidated.
class AdvicePool {
  constructor(limit=2, capacity=64) { this.limit=limit;this.capacity=capacity;this.active=0;this.jobs=new Map();this.cache=new Map(); }
  key(identity,id,tab){return JSON.stringify([identity,id,tab]);}
  peek(key){const value=this.cache.get(key);if(!value)return null;if(Date.now()-value.at>15*60*1000){this.cache.delete(key);return null;}return value.result;}
  forget(key){this.cache.delete(key);}
  prioritize(key){const job=this.jobs.get(key);if(job)job.priority=100;}
  request(key,loader,{owner,priority=0,valid=()=>true,replaceInvalid=false}={}) {
    const existing=this.jobs.get(key);
    if(existing&&(!replaceInvalid||existing.valid())){existing.owners.add(owner);existing.priority=Math.max(existing.priority,priority);return existing.promise;}
    // Only explicit read callers may replace the old page's cancelled reader.
    // A dispatched mutation must remain shared, never restarted by this pool.
    // Running jobs still occupy a slot until settled; their cleanup cannot
    // delete a replacement job or its verified cache entry under the same key.
    if(existing){this.jobs.delete(key);if(!existing.running)existing.reject(Error('页面已离开'));}
    let resolve,reject;
    const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});
    const job={key,loader,owners:new Set([owner]),priority,valid,resolve,reject,promise,running:false};
    this.jobs.set(key,job);this.drain();return promise;
  }
  release(owner){
    for(const job of this.jobs.values()){
      job.owners.delete(owner);
      if(!job.running&&!job.owners.size){this.jobs.delete(job.key);job.reject(Error('页面已离开'));}
    }
  }
  drain(){
    while(this.active<this.limit){
      const job=[...this.jobs.values()].filter(j=>!j.running).sort((a,b)=>b.priority-a.priority)[0];
      if(!job)return;
      if(!job.valid()){this.jobs.delete(job.key);job.reject(Error('登录身份已变化'));continue;}
      job.running=true;this.active++;
      Promise.resolve().then(job.loader).then(result=>{
        if(!job.valid()||this.jobs.get(job.key)!==job)throw Error('登录身份已变化');
        if(result&&result.status==='succeeded'){
          this.cache.delete(job.key);this.cache.set(job.key,{result,at:Date.now()});
          while(this.cache.size>this.capacity)this.cache.delete(this.cache.keys().next().value);
        }else this.cache.delete(job.key);
        job.resolve(result);
      }).catch(error=>{if(this.jobs.get(job.key)===job)this.cache.delete(job.key);job.reject(error);}).finally(()=>{
        this.active--;if(this.jobs.get(job.key)===job)this.jobs.delete(job.key);this.drain();
      });
    }
  }
}
const opportunityAdvicePool=new AdvicePool();
module.exports={AdvicePool,opportunityAdvicePool};
