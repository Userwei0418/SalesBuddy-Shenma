const {auth,lifecycle,present}=require('../view');
Page({
  data:{loading:true,ready:false,error:'',companyName:'',modules:[],counts:[],sections:[],navItems:[],activeKey:'home',greeting:'你好'},...lifecycle,
  onShow(){const hour=new Date().getHours();this.setData({greeting:hour<12?'上午好':hour<18?'下午好':'晚上好'});return this.load();},
  load(){return this.checked(async current=>{
    const result=await auth.request('/overview');if(!current())return;
    const enabled=new Set(this.data.modules.map(item=>item.key));
    const sections=[{key:'follow-ups',title:'最近跟进'},{key:'tasks',title:'任务动态'}].filter(item=>enabled.has(item.key)).map(item=>({...item,loading:true,error:'',items:[]}));
    this.setData({ready:true,loading:false,sections,counts:[{label:'客户',value:result.customer_count,module:'customers'},{label:'商机',value:result.opportunity_count,module:'opportunities'},
      {label:'跟进记录',value:result.follow_up_count,module:'follow-ups'},{label:'全部任务',value:result.task_count,module:'tasks'}].filter(item=>enabled.has(item.module))});
    // Independent sections keep the summary usable if one list is slow or unavailable.
    await Promise.all(sections.map(async(section,index)=>{
      try{const page=await auth.request('/'+section.key,{limit:3,offset:0});if(current())this.setData({['sections['+index+']']:{...section,loading:false,items:(page.items||[]).map(item=>present(section.key,item))}});}
      catch(error){if(current())this.setData({['sections['+index+']']:{...section,loading:false,error:error.message||'暂时无法加载，请重试'}});}
    }));
  });}
});
