const api=require('../../utils/apiClient');
const unique=rows=>[...new Map((rows||[]).filter(r=>r&&r.id).map(r=>[String(r.id),r])).values()];
Component({
 properties:{hideOptional:Boolean,compactForm:Boolean,selected:{type:Array,value:[]},disabled:Boolean,title:{type:String,value:'协助 FDE'},hint:{type:String,value:'商机协助人员，可搜索并选择多位 FDE'},excludedIds:{type:Array,value:[]},restrictToTeams:Boolean,allowedTeamIds:{type:Array,value:[]}},
 data:{open:false,query:'',items:[],draft:[],rows:[],loading:false,error:'',total:0,offset:0},
 observers:{'selected, restrictToTeams, allowedTeamIds':function(){this.decorate();}},
 lifetimes:{detached(){this.closed=true;clearTimeout(this.timer);this.serial=(this.serial||0)+1;}},
 methods:{
  open(){if(this.properties.disabled)return;this.setData({open:true,query:'',items:[],draft:unique(this.properties.selected),offset:0,error:''});this.decorate();this.load();},
  cancel(){this.serial=(this.serial||0)+1;clearTimeout(this.timer);this.setData({open:false,loading:false});},
  noop(){},
  search(e){this.setData({query:e.detail.value});this.serial=(this.serial||0)+1;clearTimeout(this.timer);this.timer=setTimeout(()=>this.load(),250);},
  async load(e){const more=!!(e&&e.currentTarget&&e.currentTarget.dataset.more);if(more&&this.data.loading)return;const serial=this.serial=(this.serial||0)+1;this.setData({loading:true,error:''});try{const r=await api.listFdeMembers({q:this.data.query.trim(),offset:more?this.data.offset:0,limit:50});if(this.closed||serial!==this.serial)return;if(!Array.isArray(r.items)||!Number.isInteger(r.total))throw Error('成员目录响应不完整，请重试');const items=unique(more?this.data.items.concat(r.items):r.items);this.setData({items,total:r.total,offset:(more?this.data.offset:0)+r.items.length,loading:false});this.decorate();}catch(e){if(serial===this.serial)this.setData({loading:false,error:e.message||'成员目录加载失败'});}},
  locked(row){return this.properties.restrictToTeams && !(this.properties.allowedTeamIds||[]).includes(row.team_id);},
  decorate(){const ids=new Set(this.data.draft.map(r=>String(r.id)));const excluded=new Set(this.properties.excludedIds||[]);const decorated=row=>({...row,locked:this.locked(row)});this.setData({selectedRows:(this.properties.selected||[]).map(decorated),draft:this.data.draft.map(decorated),rows:this.data.items.map(r=>({...decorated(r),selected:ids.has(String(r.id)),excluded:excluded.has(r.id)}))});},
  toggle(e){if(this.properties.disabled)return;const id=String(e.currentTarget.dataset.id);const row=this.data.items.find(r=>String(r.id)===id)||this.data.draft.find(r=>String(r.id)===id);if(!row||this.locked(row)||(this.properties.excludedIds||[]).includes(row.id))return;const removing=this.data.draft.some(r=>String(r.id)===id);if(!removing&&this.data.draft.length>=30){this.setData({error:'最多选择 30 名 FDE，请先移除其他成员'});return;}this.setData({draft:removing?this.data.draft.filter(r=>String(r.id)!==id):unique(this.data.draft.concat(row)),error:''});this.decorate();},
  confirm(){if(this.properties.disabled)return;this.triggerEvent('change',{members:unique(this.data.draft),memberIds:unique(this.data.draft).map(r=>r.id)});this.cancel();}
 }
});
