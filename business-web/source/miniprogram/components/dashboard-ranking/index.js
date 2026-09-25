Component({
  properties:{title:String,subtitle:String,period:String,tone:{type:String,value:'blue'},symbol:String,rows:Array,summaryIds:Array,loading:Boolean,error:String,emptySummary:{type:String,value:'本人暂未纳入当前榜单'}},
  data:{open:false,summary:[],detailRows:[],focusId:'',memberTeam:null},
 pageLifetimes:{hide(){this.close();}},
  observers:{
  'loading, error':function(){if(this.properties.loading||this.properties.error)this.close();},'rows, summaryIds':function(){const ids=this.properties.summaryIds || [];const rows=(this.properties.rows||[]).map((row,index)=>({...row,anchorId:'rank-item-'+index,isSelected:ids.includes(row.id)}));this.setData({open:false,memberTeam:null,summary:rows.filter(row=>row.isSelected),detailRows:rows,focusId:(rows.find(row=>row.isSelected)||{}).anchorId||''});}},
  methods:{
    open(){if(!this.properties.loading && !this.properties.error)this.setData({open:true});},
    close(){this.setData({open:false,memberTeam:null});},
  showMembers(event){
   if(this.properties.loading||this.properties.error)return;
   const row=(this.properties.rows||[]).find(item=>item.id===event.currentTarget.dataset.id);
   if(!row||!Array.isArray(row.members))return;
   this.setData({open:true,memberTeam:row});
  },
  backToTeams(){this.setData({memberTeam:null});},retry(){this.triggerEvent('retry');},noop(){},
    touchStart(e){this._touch=e.touches&&e.touches[0];},
    touchEnd(e){const start=this._touch,end=e.changedTouches&&e.changedTouches[0];this._touch=null;if(start&&end&&end.clientY-start.clientY>45&&end.clientY-start.clientY>Math.abs(end.clientX-start.clientX))this.close();},
  },
});
