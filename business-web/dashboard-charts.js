/* ECharts presentation over the same page facts; no API calls or new business writes. */
(function(global){
  'use strict';
  const instances=new Map();
  // Both navigation themes use a light content area.
  const palette=()=>({bar:'#4d84dc',text:'#5d718c',label:'#456181',line:'#dce5ef',grid:'#edf1f7',track:'#f5f7fb',surface:'#fff'});
  const number=value=>value==null||value===''||!Number.isFinite(Number(value))?null:Number(value);
  const text=(value,key)=>({tag:'#text',key,text:String(value)});
  const node=(tag,key,cls,children=[])=>({tag,key,attrs:cls?{class:cls}:{},events:{},children});
  const has=(v,cls)=>String(v.attrs?.class||'').split(/\s+/).includes(cls);
  const currency=value=>typeof value==='string'&&/^[¥￥]-?[\d,]+(?:\.\d+)?$/.test(value);
  function wanText(value){
    const wan=Number(value.slice(1).replaceAll(',',''))/10000;
    return wan!==0&&Math.abs(wan)<0.01?(wan>0?'<0.01':'−<0.01'):wan.toLocaleString('zh-CN',{maximumFractionDigits:2});
  }
  const datesMissing=owner=>global.SALES_MODE==='preview' && owner?._raw?.source_label?.includes('CRM') && owner._raw.opportunities.length>0 && owner._raw.opportunities.every(o=>!o.expected_close_date);
  function presentation(owner,data){
    if(owner.route!=='pages/bi/index')return data;
    if(!datesMissing(owner))return {...data,totalAcv:currency(data.totalAcv)?wanText(data.totalAcv)+' 万元':data.totalAcv};
    return {...data,totalAcv:'—',activeOpportunityCount:null,
      rankingCards:data.rankingCards.map(card=>['acv','region'].includes(card.key)?{...card,rows:[],emptySummary:'缺少关单年份/日期，无法按季度排名'}:card),
      kpis:data.kpis.map(item=>['总商机 ACV','加权预测 ACV','高概率 ACV','预计关单数'].includes(item.label)
      ? {...item,value:'—',sub:'缺少关单年份/日期，无法按所选季度统计',compact:false}:item)};
  }
  function polishMetrics(v){
    const primary=['已登记确收','已登记回款','预测含税确收','预测回款'];
    const cards=v.children.filter(c=>has(c,'kpi-card'));
    const main=[],support=[];
    for(const card of cards){
      const label=card.children.find(c=>c.tag==='label')?.children?.map(c=>c.text||'').join('');
      const value=card.children.find(c=>c.tag==='text');
      const raw=value?.children?.map(c=>c.text||'').join('');
      // Shared BI already formats yuan. Convert only explicit currency values;
      // counts, percentages and missing facts retain their original presentation.
      if(currency(raw)){
        const compact=wanText(raw);
        value.attrs.class='web-kpi-value';
        value.attrs.tabindex='0';
        value.attrs['aria-label']=`${label}，${raw} 元`;
        value.attrs.title=`${raw} 元`;
        value.children=[text(compact,value.key+'.amount'),node('span',value.key+'.unit','web-kpi-unit',[text('万元',value.key+'.ut')]),node('span',value.key+'.exact','web-kpi-exact',[text(raw+' 元',value.key+'.et')])];
      }else if(value && /未登记|未填写|—/.test(raw||''))value.attrs.class='web-kpi-missing';
      (primary.includes(label)?main:support).push(card);
    }
    if(!main.length)return v;
    v.attrs.class+=' web-kpi-groups';
    v.children=[node('div',v.key+'.primary','web-kpi-primary',main),...(support.length?[node('div',v.key+'.secondary','web-kpi-secondary',support)]:[])];
    return v;
  }
  function bar(labels,values,{horizontal=false,unit='',count=false}={}){
    const color=palette();
    const category={type:'category',data:labels,axisTick:{show:false},axisLine:{lineStyle:{color:color.line}},axisLabel:{color:color.text,fontSize:12,interval:0,overflow:'truncate',width:horizontal?90:65},...(horizontal?{inverse:true}:{})};
    const value={type:'value',minInterval:count?1:undefined,splitNumber:4,axisLabel:{color:color.text,hideOverlap:true},splitLine:{lineStyle:{color:color.grid}}};
    const baseOption={animation:false,color:[color.bar],aria:{enabled:true},textStyle:{fontFamily:'-apple-system, PingFang SC, sans-serif'},
      grid:{left:horizontal?100:42,right:horizontal?42:20,top:24,bottom:36,containLabel:false},
      tooltip:{trigger:'axis',renderMode:'richText',confine:true,backgroundColor:color.surface,borderColor:color.line,textStyle:{color:color.label},axisPointer:{type:'shadow'}},
      xAxis:horizontal?value:category,yAxis:horizontal?category:value,
      series:[{type:'bar',showBackground:horizontal,backgroundStyle:{color:color.track,borderRadius:4},data:values.map(v=>({value:v,itemStyle:{color:color.bar,borderRadius:horizontal?[0,4,4,0]:[4,4,0,0]}})),barMaxWidth:horizontal?20:28,
        label:{show:true,position:horizontal?'right':'top',fontSize:11,color:color.label,formatter:p=>p.value==null?'—':Number(p.value).toLocaleString('zh-CN',{maximumFractionDigits:2})}}]};
    return {baseOption,media:[{query:{maxWidth:380},option:{
      grid:{left:horizontal?74:30,right:horizontal?40:12,top:24,bottom:32},
      xAxis:horizontal?{splitNumber:2,axisLabel:{fontSize:10,hideOverlap:true}}:{axisLabel:{fontSize:11,width:30,interval:'auto',hideOverlap:true}},
      yAxis:horizontal?{axisLabel:{fontSize:11,width:64}}:{splitNumber:3,axisLabel:{fontSize:10,hideOverlap:true}}
    }}]};
  }
  function chart(key,title,labels,values,settings={},onClick){
    const host=node('div',key+'.plot','web-echart');host.attrs.role='img';host.attrs['aria-label']=title;
    host.chart={signature:JSON.stringify([title,labels,values,settings]),option:bar(labels,values,settings),makeOption:()=>bar(labels,values,settings),onClick};
    const detail=node('details',key+'.values','web-chart-data',[node('summary',key+'.summary','',[text('查看图表数据',key+'.s')]),
      node('table',key+'.table','',[node('thead',key+'.thead','',[node('tr',key+'.hrow','',[node('th',key+'.hlabel','',[text('项目',key+'.hl')]),node('th',key+'.hvalue','',[text(settings.unit||'数量',key+'.hv')])])]),
        node('tbody',key+'.body','',labels.map((label,i)=>node('tr',key+'.r'+i,'',[node('td',key+'.l'+i,'',[text(label,key+'.lt'+i)]),node('td',key+'.v'+i,'',[text(values[i]==null?'未填写':values[i],key+'.vt'+i)])])))])]);
    return node('div',key,'web-chart-wrap',[node('div',key+'.unit','web-chart-unit',[text(settings.unit||'数量',key+'.unittext')]),host,detail]);
  }
  function adapt(tree,page){
    if(page.route!=='pages/bi/index')return tree;
    const walk=(v,owner)=>{
      if(v.tag==='#text')return v;
      owner=v.component||owner;
      const d=owner.data||{},fde=owner._tag==='fde-dashboard',sales=owner.route==='pages/bi/index';
      if((sales||fde)&&has(v,'funnel-list')){
        const rows=fde?d.stages:d.funnel;
        return chart(v.key,'商机阶段分布',rows.map(r=>r.name||r.label),rows.map(r=>fde?number(r.count):(number(r.value)==null?null:Number(r.value)/10000)),
          {horizontal:true,unit:fde?'商机数':'ACV（万元）',count:fde},fde?()=>owner.openProjects():null);
      }
      if((sales||fde)&&has(v,'visit-bars')){
        const rows=fde?d.rhythm:d.visitDays;
        return chart(v.key,'跟进节奏',rows.map(r=>r.dateLabel||r.weekday),rows.map(r=>number(fde?r.visits:r.count)),{unit:'跟进数',count:true},fde?()=>owner.openActivity():null);
      }
      if(sales&&has(v,'timeline-scroll'))return chart(v.key,'商机季度时间线',d.timeline.map(r=>r.month),d.timeline.map(r=>number(r.value)==null?null:Number(r.value)/10000),{unit:'ACV（万元）'});
      if(fde&&has(v,'task-metrics'))return chart(v.key,'任务进展',['已完成','待完成','已逾期'],[d.summary.completed_tasks,d.summary.pending_tasks,d.summary.overdue_tasks],{unit:'任务数',count:true},event=>owner.openTasks({currentTarget:{dataset:{status:event.dataIndex===0?'completed':'pending'}}}));
      v.children=(v.children||[]).map(child=>walk(child,owner));
      if(sales&&has(v,'kpi-grid'))polishMetrics(v);
      if(has(v,'rank-value')&&v.children.length===1&&currency(v.children[0].text)){
        const raw=v.children[0].text;
        v.attrs.class+=' web-kpi-value';v.attrs.title=raw+' 元';v.attrs.tabindex='0';v.attrs['aria-label']=raw+' 元';
        v.children=[text(wanText(raw)+' 万元',v.key+'.amount'),node('span',v.key+'.exact','web-kpi-exact',[text(raw+' 元',v.key+'.exacttext')])];
      }
      if(sales&&has(v,'metrics-section')&&datesMissing(owner)&&!d.loading&&!d.loadError){
        const raw=owner._raw.opportunities,quarters=raw.filter(r=>r.source_close_quarter).length;
        const reason=node('section',v.key+'.coverage','web-chart-coverage',[node('strong',v.key+'.ct','',[text('数据待完善',v.key+'.ctt')]),
          node('p',v.key+'.cp','',[text(`当前范围 ${raw.length} 个在推商机缺少完整关单年份/日期，其中 ${quarters} 个只填季度。确收、回款和季度预测尚未导入；下方展示当前范围的阶段数量快照。`,v.key+'.cpt')])]);
        const catalog=global.SalesRuntime.businessOptions();
        const stages=catalog.isReady()?catalog.stages.filter(row=>row.status==='open').slice().sort((a,b)=>b.probability-a.probability):[];
        const stageOf=row=>catalog.stages.find(stage=>['won','lost'].includes(row.status)?stage.status===row.status:stage.probability===Number(row.probability))||catalog.stages.find(stage=>stage.code===row.stage_code);
        const card=node('section',v.key+'.snapshot','chart-card web-snapshot-chart',[node('div',v.key+'.sh','web-snapshot-head',[node('h3',v.key+'.st','',[text('在推商机阶段',v.key+'.stt')]),node('span',v.key+'.total','section-total',[text(raw.length+' 个',v.key+'.totalt')])]),
          node('p',v.key+'.sn','web-chart-note',[text('按当前成员/团队范围统计全部在推商机，不用于所选季度的业绩统计。',v.key+'.snt')]),
          chart(v.key+'.sc','在推商机阶段快照',stages.map(stage=>stage.label),stages.map(stage=>raw.filter(row=>stageOf(row)?.code===stage.code).length),{horizontal:true,unit:'商机数',count:true})]);
        const at=v.children.findIndex(child=>has(child,'countdown-card'));v.children.splice(at<0?0:at,0,reason);v.children.push(card);
      }
      if(sales&&has(v,'metrics-section')){
        const charts=v.children.filter(child=>has(child,'chart-card'));
        if(charts.length){
          v.children=v.children.filter(child=>!has(child,'chart-card'));charts.sort((a,b)=>Number(has(b,'web-snapshot-chart'))-Number(has(a,'web-snapshot-chart')));
          const grid=node('div',v.key+'.charts','web-dashboard-charts',charts),kpi=v.children.findIndex(child=>has(child,'kpi-grid'));
          if(datesMissing(owner)&&kpi>=0)v.children.splice(kpi,0,grid,node('div',v.key+'.pendingheading','web-metrics-group-heading',[text('季度业绩指标',v.key+'.pendingtitle'),node('span',v.key+'.pendingsub','',[text('待补充日期与金额信息',v.key+'.pendingtxt')])]));
          else v.children.push(grid);
        }
      }
      if(sales&&has(v,'bi-page')){
        const ranks=v.children.filter(child=>child.tag==='dashboard-ranking');
        if(ranks.length){const at=v.children.indexOf(ranks[0]);v.children=v.children.filter(child=>!ranks.includes(child));v.children.splice(at,0,node('div',v.key+'.rankings','web-ranking-grid',ranks));}
      }
      return v;
    };
    return tree.map(v=>walk(v,page));
  }
  function update(element,spec){
    let record=instances.get(element);
    if(!record){const instance=echarts.init(element,null,{renderer:'svg'});const observer=new ResizeObserver(()=>{if(element.isConnected)instance.resize();});observer.observe(element);record={instance,observer};instances.set(element,record);}
    if(record.signature!==spec.signature){record.instance.setOption(spec.option,{notMerge:true});record.signature=spec.signature;}
    record.spec=spec;
    record.instance.off('click');if(spec.onClick)record.instance.on('click',spec.onClick);
  }
  function reconcile(){for(const [element,record] of instances)if(!element.isConnected){record.observer.disconnect();record.instance.dispose();instances.delete(element);}}
  global.addEventListener('sales-theme-change',()=>{for(const [element,record] of instances)if(element.isConnected)record.instance.setOption(record.spec.makeOption(),{notMerge:true});});
  global.SalesDashboard=Object.freeze({presentation,adapt,update,reconcile,count:()=>instances.size});
})(window);
