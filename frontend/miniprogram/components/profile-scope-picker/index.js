Component({
 properties:{modes:Array,members:Array,teams:Array,mode:String,memberId:String,teamId:String,label:String},
 data:{open:false,search:'',filterTeam:'',visibleMembers:[],selecting:'person',active:false},
 observers:{'members,teams,memberId,teamId,mode':function(){this.close();this.filter();this.markActive();}},
 lifetimes:{attached(){this.markActive();}},
 pageLifetimes:{hide(){this.close();}},
 methods:{noop(){},
  // 触发片选中态（只影响样式）：每份成员／团队名单到达后，第一个落在名单里的选中值记为默认；之后选了别的才算 active
  markActive(){const p=this.properties,d=this._defaults||(this._defaults={}),sig=rows=>(rows||[]).map(row=>row.id).join(',');
   for(const [kind,rows,id] of [['member',p.members,p.memberId],['team',p.teams,p.teamId]]){const s=sig(rows);if(d[kind+'Sig']!==s){d[kind+'Sig']=s;d[kind]=null;}if(d[kind]===null&&id&&(rows||[]).some(row=>row.id===id))d[kind]=id;}
   const active=p.mode==='person'?d.member!==null&&p.memberId!==d.member:p.mode==='team'?d.team!==null&&p.teamId!==d.team:false;
   if(active!==this.data.active)this.setData({active});},onModeSegment(e){return this.switchMode({currentTarget:{dataset:{mode:e.detail.value}}});},onTeamTab(e){return this.teamFilter({currentTarget:{dataset:{id:e.detail.key}}});},switchMode(e){const mode=e.currentTarget.dataset.mode;if(!(this.properties.modes||[]).some(row=>row.value===mode))return;this.triggerEvent('modechange',{mode});},show(){this.triggerEvent('visibilitychange',{open:true});this.setData({open:true,search:'',filterTeam:'',selecting:this.properties.mode==='team'?'team':'person'});this.filter();},close(){if(!this.data.open)return;this.setData({open:false});this.triggerEvent('visibilitychange',{open:false});},search(e){this.setData({search:e.detail.value});this.filter();},teamFilter(e){this.setData({filterTeam:e.currentTarget.dataset.id||''});this.filter();},filter(){const query=String(this.data.search||'').trim().toLowerCase(),team=this.data.filterTeam;this.setData({visibleMembers:(this.properties.members||[]).filter(row=>(!team||(row.team_ids||[]).includes(team))&&(!query||String(row.name||'').toLowerCase().includes(query)))});},select(e){const id=e.currentTarget.dataset.id,kind=this.data.selecting,rows=kind==='team'?this.properties.teams:this.properties.members;if(!(rows||[]).some(row=>row.id===id))return;this.close();this.triggerEvent('subjectchange',{kind,id});}}
});
