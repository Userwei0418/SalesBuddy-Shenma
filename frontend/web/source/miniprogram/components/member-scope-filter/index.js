Component({
  properties:{members:Array,selected:Array,disabled:Boolean},
  data:{open:false,query:'',draft:[],options:[],label:'全部成员'},
  observers:{'members, selected':function(){this.updateOptions();}},
  methods:{
    updateOptions(){const ids=this.data.open?this.data.draft:(this.properties.selected||[]),q=this.data.query.trim().toLowerCase();this.setData({label:(this.properties.selected||[]).length?'已选 '+this.properties.selected.length+' 人':'全部成员',options:(this.properties.members||[]).filter(r=>r.id&&(!q||String(r.name).toLowerCase().includes(q))).map(r=>({...r,checked:ids.includes(r.id)}))});},
    toggle(){if(this.properties.disabled)return;this.setData({open:!this.data.open,query:'',draft:(this.properties.selected||[]).slice()});this.updateOptions();},
    search(e){this.setData({query:e.detail.value});this.updateOptions();},
    select(e){const id=e.currentTarget.dataset.id;this.setData({draft:this.data.draft.includes(id)?this.data.draft.filter(v=>v!==id):this.data.draft.concat(id)});this.updateOptions();},
    clear(){this.setData({draft:[]});this.updateOptions();},
    cancel(){this.setData({open:false});this.updateOptions();},
    apply(){const ids=this.data.draft.slice();this.setData({open:false});this.triggerEvent('change',{ids});},
    noop(){}
  }
});
