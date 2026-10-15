/* Tom Select owns its portal DOM; the imported page keeps every business callback. */
(function(global){
  'use strict';
  let active;
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  const live=owner=>!owner || !owner._destroyed && (owner._page || owner)===global.SalesRuntime?.current;
  function close(restore=true){
    if(!active)return;const previous=active;active=null;
    previous.cleanup?.();previous.instances.forEach(instance=>instance.destroy());
    previous.dialog.hidePopover();previous.dialog.remove();previous.onClose?.();
    if(restore!==false&&previous.anchor?.isConnected)previous.anchor.focus({preventScroll:true});
  }
  function open(config){
    close();if(!live(config.owner))return;
    const period=!!config.year,single=!config.multiple;
    const dialog=el('div',null,'web-select-dialog'+(period?' web-select-period':'')+(single?' web-select-single':''));
    dialog.id='web-select-dialog';dialog.popover='manual';dialog.setAttribute('role','dialog');dialog.setAttribute('aria-label',config.title||'请选择');
    const head=el('header'),cancel=el('button','×','web-select-close');cancel.type='button';cancel.setAttribute('aria-label','关闭选择');cancel.onclick=()=>close();
    head.append(el('h2',config.title||'请选择'),cancel);dialog.append(head);
    if(config.description)dialog.append(el('p',config.description,'web-select-description'));
    const state={dialog,anchor:config.anchor,owner:config.owner,instances:[],onClose:config.onClose,guard:config.guard};active=state;
    const valid=()=>active===state&&live(config.owner)&&(!config.anchor||config.anchor.isConnected)&&(!config.guard||config.guard());
    let yearValue=config.year?.value;
    if(period){
      const extra=el('div',null,'web-select-year'),previous=el('button','‹'),next=el('button','›'),input=el('input');
      previous.setAttribute('aria-label','上一年');next.setAttribute('aria-label','下一年');extra.append(previous,input,next);dialog.append(extra);
      const years=config.year.options.map(Number);
      const year=new TomSelect(input,{options:years.map(value=>({value:String(value),text:value+' 年'})),items:[String(yearValue)],maxItems:1,create:false,controlInput:null,
        onChange(value){if(value)yearValue=Number(value);previous.disabled=years.indexOf(yearValue)<=0;next.disabled=years.indexOf(yearValue)>=years.length-1;}});
      previous.onclick=()=>year.setValue(String(years[years.indexOf(yearValue)-1]??yearValue));next.onclick=()=>year.setValue(String(years[years.indexOf(yearValue)+1]??yearValue));
      state.instances.push(year);
    }
    const input=el('input');input.id='web-select-input';dialog.append(input);
    const footer=el('footer'),clear=el('button',config.emptyLabel||(period?'重置':'清空'),'web-select-clear'),apply=el('button','应用','web-select-apply');apply.id='web-select-apply';
    const status=el('span','','web-select-status');status.setAttribute('role','status');
    footer.append(clear,status,apply);dialog.append(footer);(config.anchor?.closest('dialog[open]')||document.body).append(dialog);dialog.showPopover();
    const normalize=rows=>rows.map(row=>({...row,value:String(row.value),text:String(row.text??row.value)}));
    let picker,initialized=false;
    function commit(){
      if(!valid()){if(active===state)close();return;}if(apply.disabled)return;
      const values=picker.items.slice(),available=new Set(Object.keys(picker.options));
      if(values.some(value=>!available.has(value)||(picker.options[value].disabled&&!(config.selected||[]).map(String).includes(value))))return;
      if((config.selected||[]).map(String).some(value=>picker.options[value]?.locked&&!values.includes(value)))return;
      const selected=values.map(value=>picker.options[value]);close();config.commit?.(values,{year:yearValue,selected});
    }
    picker=new TomSelect(input,{options:normalize(config.options||[]),items:(config.selected||[]).map(String),maxItems:config.multiple?(config.maxItems||null):1,
      maxOptions:200,create:false,allowEmptyOption:true,hideSelected:false,closeAfterSelect:false,
      placeholder:single?'搜索选项':'搜索并选择',plugins:config.multiple?['remove_button','checkbox_options']:[],searchField:['text','meta'],loadThrottle:250,preload:!!config.load,
      render:{option:(row,escape)=>'<div><span>'+escape(row.text)+'</span>'+(row.meta?'<small class="web-select-option-meta">'+escape(row.meta)+'</small>':'')+'</div>',no_results:()=>'<div class="no-results">暂无匹配结果</div>',loading:()=>'<div class="spinner">正在加载…</div>',
        ...(period?{option:(row,escape)=>'<div class="web-quarter-option"><b>'+escape(row.text)+'</b><small>'+escape(['1 — 3 月','4 — 6 月','7 — 9 月','10 — 12 月'][Number(row.value)-1]||'')+'</small></div>'}:{})},
      ...(config.load?{load(query,callback){Promise.resolve(config.load(query)).then(rows=>{if(valid())callback(normalize(rows));}).catch(error=>{if(valid()){status.textContent=error.message||'加载失败';callback();}});}}:{}),
      onDelete(values){return !values.some(value=>picker?.options[value]?.locked);},
      onChange(){update();if(initialized&&single&&picker.items.length)queueMicrotask(commit);}});
    picker.wrapper.classList.add('web-select-main');state.instances.push(picker);initialized=true;
    // A picker cursor is not a business confirmation. Tom Select omits `change`
    // for its current item, so handle that explicit click/Enter intent as well.
    picker.hook('before','onOptionSelect',(event,option)=>{
      const value=option?.dataset.value,row=picker.options[value];
      if(single&&!picker.isLocked&&picker.canSelect(option)&&option.hasAttribute('data-selectable')&&!option.closest('[data-disabled]')&&row&&!row.disabled&&!row.locked&&picker.items.length===1&&picker.items[0]===value)queueMicrotask(commit);
    });
    function update(){if(active!==state)return;const count=picker?.items.length||0;apply.disabled=count<(config.minItems??(config.multiple?0:1));status.textContent=count?'已选 '+count+' 项':period?'全部时间':'未选择';}
    clear.onclick=()=>{picker.setValue(period&&config.minItems?config.selected:(config.selected||[]).map(String).filter(value=>picker.options[value]?.locked));picker.focus();picker.open();};apply.onclick=commit;
    if(config.shortcuts){const shortcuts=el('div',null,'web-select-shortcuts');for(const item of config.shortcuts){const b=el('button',item.label);b.onclick=()=>{if(valid()){close();item.run();}};shortcuts.append(b);}head.after(shortcuts);}
    if(config.more){const more=el('button','加载更多','web-select-more');more.onclick=async()=>{more.disabled=true;try{const rows=await config.more();if(valid()){picker.addOptions(normalize(rows));picker.refreshOptions(false);}}catch(error){if(valid())status.textContent=error.message;}finally{more.disabled=false;}};footer.before(more);}
    function position(){
      if(!valid())return;
      const anchor=config.anchor?.getBoundingClientRect(),w=dialog.offsetWidth,h=dialog.offsetHeight,gap=8;
      const x=anchor?Math.min(Math.max(12,anchor.right-w),innerWidth-w-12):Math.max(12,(innerWidth-w)/2);
      const below=anchor?anchor.bottom+gap:80;
      const y=anchor&&below+h>innerHeight-12&&anchor.top-h-gap>=12?anchor.top-h-gap:Math.min(below,Math.max(12,innerHeight-h-12));
      dialog.style.left=x+'px';dialog.style.top=y+'px';
    }
    const observer=new ResizeObserver(position);observer.observe(dialog);
    const scroll=event=>{if(!dialog.contains(event.target))position();};
    const outside=event=>{if(active===state&&!dialog.contains(event.target)&&!config.anchor?.contains(event.target))close(false);};
    const focus=event=>{if(active===state&&!dialog.contains(event.target)&&event.target!==config.anchor)close(false);};
    // Escape must also work before the first animation-frame focus enters the popup.
    const escape=event=>{if(active===state&&event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();close();}};
    document.addEventListener('keydown',escape,true);
    window.addEventListener('resize',position);document.addEventListener('scroll',scroll,true);document.addEventListener('pointerdown',outside,true);document.addEventListener('focusin',focus);
    state.cleanup=()=>{document.removeEventListener('keydown',escape,true);observer.disconnect();window.removeEventListener('resize',position);document.removeEventListener('scroll',scroll,true);document.removeEventListener('pointerdown',outside,true);document.removeEventListener('focusin',focus);};
    dialog.addEventListener('pointerdown',event=>{if(event.target.closest('button'))event.preventDefault();});
    // Populate options before opening so the first visible frame uses the full popup height.
    update();position();config.onOpen?.();requestAnimationFrame(()=>{if(valid()){picker.refreshOptions(false);picker.open();position();}});
  }
  function nativeSelect(select){
    if(select.disabled)return;
    const listFilter=select.closest('.web-opportunity-results .workbench-filter-item');
    const summaryYear=select.closest('.quarter-filter-summary');
    const title=listFilter?'商机列表 · '+listFilter.querySelector('label').textContent:summaryYear?'总览统计年份':select.getAttribute('aria-label') || '请选择';
    const optionKey=()=>JSON.stringify([...select.options].map(option=>[option.value,option.textContent,option.disabled,option.hidden]));
    const initialOptions=optionKey();
    open({anchor:select,title,guard:()=>!select.disabled&&optionKey()===initialOptions,description:listFilter?'仅筛选商机列表':summaryYear?'切换统计季度的年份，仅影响总览数字':'',multiple:select.multiple,
      options:[...select.options].filter(o=>!o.hidden).map(o=>({value:o.value,text:o.textContent,disabled:o.disabled})),
      selected:[...select.selectedOptions].filter(o=>!o.disabled).map(o=>o.value),commit(values){
        if(!select.isConnected||select.disabled)return;
        if(select.multiple)for(const option of select.options)option.selected=values.includes(option.value);else select.value=values[0] ?? '';
        select.dispatchEvent(new Event('change',{bubbles:true}));
      }});
  }
  // Keep the select element as the source contract, replace its user-facing popup.
  document.addEventListener('pointerdown',event=>{const select=event.target.closest('select');if(!select||select.closest('#web-select-dialog,#web-date-dialog'))return;event.preventDefault();event.stopImmediatePropagation();nativeSelect(select);},true);
  document.addEventListener('keydown',event=>{if(event.target.tagName==='SELECT'&&['Enter',' ','ArrowDown','ArrowUp'].includes(event.key)&&!event.target.closest('#web-select-dialog,#web-date-dialog')){event.preventDefault();event.stopImmediatePropagation();nativeSelect(event.target);}},true);
  function partnerPicker(owner,anchor){
    const d=owner.data,selected=d.form.partner_id?[{id:d.form.partner_id,name:d.form.partner_name}]:[];
    const rows=new Map(selected.map(row=>[row.id,row]));
    const options=items=>items.map(row=>{rows.set(row.id,row);return {value:row.id,text:row.name};});
    owner.setData({showPartnerSearch:false});
    open({owner,anchor,title:'选择合作伙伴',options:options(selected),selected:selected.map(row=>row.id),
      load:async query=>{owner.setData({partnerQuery:query});await owner.loadPartners();if(owner.data.partnersError)throw Error(owner.data.partnersError);return options(owner.data.partners);},
      more:async()=>{await owner.loadPartners({currentTarget:{dataset:{more:true}}});if(owner.data.partnersError)throw Error(owner.data.partnersError);return options(owner.data.partners);},
      onClose:()=>{owner.partnerSeq=(owner.partnerSeq||0)+1;clearTimeout(owner.partnerTimer);owner.setData({partnersLoading:false});},
      commit:ids=>{const row=rows.get(ids[0]);if(!row||owner.properties.disabled)return;owner.setData({partners:[row]});owner.choosePartner({currentTarget:{dataset:{id:row.id}}});}});
  }
  function intercept(owner,name,data,native,detail={},type='tap'){
    const tag=owner?._tag,route=owner?.route,p=owner?.properties||{},d=owner?.data||{};
    const config={owner,anchor:native?.currentTarget};
    if(type==='change'&&tag==='opportunity-form'&&name==='partnerMode'){
      owner.partnerMode({detail});if(!p.disabled&&d.form.partner_mode==='partner')partnerPicker(owner,native?.currentTarget);return true;
    }
    if(type!=='tap')return false;
    const opts=rows=>(rows||[]).map(row=>({value:row.id??row.value??row.key,text:row.name??row.label??row.value,meta:[...new Set([row.account_code,row.group,row.teamLabel||row.team].filter(Boolean))].join(' · ')}));
    const trigger=(event,detail)=>owner.triggerEvent(event,detail);
    if(tag==='opportunity-form'&&name==='openPartners'){if(!p.disabled)partnerPicker(owner,native?.currentTarget);return true;}
    if(tag==='dashboard-picker'&&name==='open'){
      if(p.loading||p.error){if(p.error)trigger('retry',{});global.wx?.showToast({title:p.error?'正在重试加载选项':'正在加载选项',icon:'none'});return true;}
      const optionsKey=JSON.stringify(p.options||[]);
      open({...config,title:p.title,description:p.subtitle,multiple:p.multiple,minItems:1,options:opts(p.options),selected:p.selected,
        guard:()=>!owner.properties.loading&&!owner.properties.error&&JSON.stringify(owner.properties.options||[])===optionsKey,
        commit:ids=>{owner.setData({draft:ids});owner.apply();}});return true;
    }
    if(tag==='member-scope-filter'&&name==='toggle'){
      if(p.disabled)return true;
      open({...config,title:'选择成员',multiple:true,emptyLabel:'全部成员',options:opts((p.members||[]).filter(r=>r.id)),selected:p.selected,commit:ids=>trigger('change',{ids})});return true;
    }
    if(tag==='profile-scope-picker'&&name==='show'){
      const kind=p.mode==='team'?'team':'person';
      const scopeKey=()=>JSON.stringify(['mode','teamId','memberId','teams','members'].map(key=>owner.properties[key]));
      const initialScope=scopeKey();
      open({...config,title:kind==='team'?'选择团队':'选择成员',options:opts(kind==='team'?p.teams:p.members),selected:[kind==='team'?p.teamId:p.memberId].filter(Boolean),
        guard:()=>scopeKey()===initialScope,onOpen:()=>trigger('visibilitychange',{open:true}),onClose:()=>trigger('visibilitychange',{open:false}),
        commit:ids=>{const rows=kind==='team'?owner.properties.teams:owner.properties.members;if((rows||[]).some(row=>String(row.id)===ids[0]))trigger('subjectchange',{kind,id:ids[0]});}});return true;
    }
    if(tag==='fde-picker'&&name==='open'){
      if(p.disabled)return true;
      const rows=new Map((p.selected||[]).map(r=>[String(r.id),r]));
      const optionRows=items=>items.map(row=>{rows.set(String(row.id),row);return {value:row.id,text:row.name||row.display_name,meta:row.team_name||row.team,locked:owner.locked(row),disabled:owner.locked(row)||(p.excludedIds||[]).includes(row.id)};});
      open({...config,title:p.title||'选择 FDE',description:p.hint,multiple:true,maxItems:30,
        options:optionRows(p.selected||[]),selected:(p.selected||[]).map(r=>r.id),
        load:async query=>{owner.setData({query});await owner.load();if(owner.data.error)throw Error(owner.data.error);return optionRows(owner.data.items);},
        more:async()=>{await owner.load({currentTarget:{dataset:{more:true}}});if(owner.data.error)throw Error(owner.data.error);return optionRows(owner.data.items);},
        onClose:()=>owner.cancel(),commit:ids=>{if(!p.disabled){const members=ids.map(id=>rows.get(id)).filter(Boolean);trigger('change',{members,memberIds:members.map(r=>r.id)});}}});return true;
    }
    if(route==='pages/workbench/index'&&name==='toggleOpportunityStageFilter'){
      open({...config,title:'商机列表 · 商机阶段',description:'仅筛选商机列表，可多选阶段',multiple:true,emptyLabel:'全部阶段',options:opts(d.opportunityStageOptions.filter(o=>o.value!=='all')),selected:d.opportunitySelectedStages,
        commit:ids=>{owner.setData({opportunitySelectedStages:ids,opportunityStageOptions:d.opportunityStageOptions.map(o=>({...o,selected:ids.includes(o.value)})),opportunityStageLabel:ids.length?d.opportunityStageOptions.filter(o=>ids.includes(o.value)).map(o=>o.label).join(' / '):'全部阶段'},()=>owner.applyOpportunityFilters());}});return true;
    }
    if(route==='pages/opportunities/index'&&name==='toggleStageFilter'){
      open({...config,title:'商机阶段',multiple:true,emptyLabel:'全部阶段',options:opts(d.stageOptions.filter(o=>o.value!=='all')),selected:d.selectedStages,
        commit:ids=>{const previous=d.selectedStages.slice();for(const value of new Set([...previous,...ids]))if(previous.includes(value)!==ids.includes(value))owner.toggleStage({currentTarget:{dataset:{value}}});}});return true;
    }
    if(route==='pages/customers/index'&&name==='toggleMapLevelFilter'){
      open({...config,title:'客户优先级',multiple:true,emptyLabel:'全部优先级',options:opts(d.mapLevelOptions),selected:d.mapSelectedLevels,
        commit:ids=>{owner.setData({mapSelectedLevels:ids,mapLevelOptions:d.mapLevelOptions.map(o=>({...o,selected:ids.includes(o.value)})),mapLevelLabel:ids.length?ids.join('/'):'全部优先级'},()=>owner.applyFilters());}});return true;
    }
    if(route==='pages/visit-confirm/index'&&name==='toggleOpportunityPicker'){
      if(d.busy)return true;
      const options=()=>owner.data.opportunityOptions.map(row=>({value:row.id,text:row.name,disabled:row.unverified}));
      open({...config,title:'关联商机',options:options(),selected:[d.opportunityId||''],
        load:async query=>{owner.invalidateOpportunityReads();owner.setData({opportunityQuery:query});await owner.loadOpportunities({search:true});if(owner.data.opportunityError)throw Error(owner.data.opportunityError);return options();},
        more:async()=>{await owner.moreOpportunities();if(owner.data.opportunityError)throw Error(owner.data.opportunityError);return options();},
        onClose:()=>owner.invalidateOpportunityReads(),commit:ids=>{if(!owner.data.busy)owner.chooseOpportunity({currentTarget:{dataset:{id:ids[0]}}});}});return true;
    }
    if(route==='pages/bi/index'&&name==='toggleQuarterFilter'){
      open({...config,title:'经营分析统计季度',multiple:true,minItems:1,year:{value:d.selectedQuarter.year,options:d.quarterYears},options:[1,2,3,4].map(q=>({value:q,text:'Q'+q})),selected:d.selectedQuarter.quarters,
        commit:(ids,{year})=>{owner.setData({selectedQuarterKeys:ids.map(q=>`${year}-Q${q}`),quarterFilterDirty:true,showQuarterFilter:false});owner.syncQuarterFilter();owner.rebuild();}});return true;
    }
    if(route==='pages/workbench/index'&&name==='toggleOpportunityQuarterFilter'){
      open({...config,title:'商机列表 · 预计关单时间',description:'按预计关单日期筛选列表；全部时间包含未填日期的商机',multiple:true,emptyLabel:'全部时间',year:{value:d.listQuarter.year,options:d.quarterYearOptions.map(o=>o.value)},options:[1,2,3,4].map(q=>({value:q,text:'Q'+q})),selected:d.listQuarter.quarters,
        shortcuts:d.opportunityCloseOptions.map((o,index)=>({label:o.label,run:()=>owner.selectOpportunityClosePeriod({currentTarget:{dataset:{index}}})})),
        commit:(ids,{year})=>{const quarters=ids.map(Number).sort();owner.setData({opportunityCloseIndex:0,listQuarter:{year,quarters,options:[1,2,3,4].map(q=>({value:q,label:'Q'+q,selected:quarters.includes(q)})),label:quarters.length?`${year}年 `+quarters.map(q=>'Q'+q).join(' + '):'全部时间'},showOpportunityQuarterFilter:false},()=>owner.applyOpportunityFilters());}});return true;
    }
    if(tag==='fde-dashboard'&&name==='toggleFilter'){
      open({...config,title:'协作看板统计周期',multiple:true,emptyLabel:'全年',year:{value:d.year,options:d.yearOptions},options:[1,2,3,4].map(q=>({value:q,text:'Q'+q})),selected:d.quarters,
        commit:(ids,{year})=>{owner.setData({draftYearIndex:d.yearOptions.indexOf(year),draftQuarters:ids.map(Number).sort()});owner.applyPeriod();}});return true;
    }
    return false;
  }
  function reconcile(){if(active&&(!live(active.owner)||(active.anchor&&!active.anchor.isConnected)||(active.guard&&!active.guard())))close();}
  global.SalesSelect=Object.freeze({open,close,intercept,reconcile});
})(window);
