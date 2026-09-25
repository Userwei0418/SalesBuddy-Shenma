Component({
  properties: {
    title: String, subtitle: String, label: String, options: Array, selected: Array,
    multiple: Boolean, loading: Boolean, error: String, selectionKind: {type:String,value:'member'},
  },
  data: {open:false, query:'', draft:[], groups:[], canApply:false},
  observers: {'options, selected':function(){if(this.data.open)this.updateOptions();}},
  methods: {
    open() {
      this.setData({open:true, query:'', draft:(this.properties.selected || []).slice()});
      this.updateOptions();
      if(this.properties.error)this.triggerEvent('retry');
    },
    close(){this.setData({open:false,query:''});},
    updateOptions() {
      const options=this.properties.options || [], valid=new Set(options.map(row=>row.id));
      const draft=this.data.draft.filter(id=>valid.has(id)), query=this.data.query.trim().toLowerCase();
      const groups=[];
      options.filter(row=>!query || [row.name,row.account_code,row.group].join(' ').toLowerCase().includes(query)).forEach(row=>{
        const name=row.group || '成员';
        let group=groups.find(item=>item.name===name);
        if(!group){group={name,rows:[]};groups.push(group);}
        group.rows.push({...row,checked:draft.includes(row.id)});
      });
      this.setData({groups,draft,canApply:draft.length>0});
    },
    search(event){this.setData({query:event.detail.value});this.updateOptions();},
    select(event){
      const id=event.currentTarget.dataset.id;
      if(!(this.properties.options || []).some(row=>row.id===id))return;
      const draft=this.properties.multiple
        ? this.data.draft.includes(id)?this.data.draft.filter(value=>value!==id):this.data.draft.concat(id)
        : [id];
      this.setData({draft});this.updateOptions();
    },
    apply(){
      this.updateOptions();
      if(!this.data.canApply || this.properties.loading || this.properties.error)return;
      this.triggerEvent('change',{ids:this.data.draft.slice()});this.close();
    },
    retry(){this.triggerEvent('retry');},
    touchStart(e){this._touch=e.touches && e.touches[0];},
    touchEnd(e){const end=e.changedTouches && e.changedTouches[0],start=this._touch;this._touch=null;if(start&&end&&end.clientY-start.clientY>45&&end.clientY-start.clientY>Math.abs(end.clientX-start.clientX))this.close();},
    noop(){},
  },
});
