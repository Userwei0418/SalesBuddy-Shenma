/* Standalone, synthetic-only catalog. No application runtime, storage or API client. */
'use strict';
(() => {
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const money = value => '¥ ' + value.toLocaleString('zh-CN');
  let toastTimer;
  function feedback(message) {
    clearTimeout(toastTimer); const node = $('#toast'); node.textContent = message; node.hidden = false;
    toastTimer = setTimeout(() => {node.hidden = true;}, 3500);
  }
  const dialog = $('#catalog-dialog');
  dialog.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const stops = [...dialog.querySelectorAll('button,input,textarea,select,a[href],[tabindex]')].filter(node => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length);
    if (!stops.length) return;
    const first = stops[0], last = stops.at(-1);
    if (event.shiftKey && document.activeElement === first) {event.preventDefault();last.focus();}
    else if (!event.shiftKey && document.activeElement === last) {event.preventDefault();first.focus();}
  });
  function showDialog(title, text) {
    $('#dialog-title').textContent = title;
    $('#dialog-content').replaceChildren(Object.assign(document.createElement('p'), {textContent: text}));
    dialog.showModal();
  }
  $$('[data-feedback]').forEach(button => button.addEventListener('click', () => feedback(button.dataset.feedback)));
  $('#demo-primary').onclick = () => feedback('已触发“记录客户拜访”示例操作');
  $('#open-dialog').onclick = () => showDialog('查看交互示例', '这是一条说明弹窗。按 Tab 移动焦点，按 Esc 关闭。\n本页不会写入任何业务数据。');

  const palette = [
    ['导航深蓝', '--ui-sidebar'], ['工作区背景', '--ui-background'], ['内容面板', '--ui-surface'],
    ['主要操作', '--ui-primary'], ['正文', '--ui-ink'], ['辅助信息', '--ui-muted']
  ];
  $('#palette').innerHTML = palette.map(([name, token]) => `<div class="swatch"><div class="swatch-color" style="background:var(${token})"></div><b>${name}</b><code>${css(token).toUpperCase()}</code><code>${token}</code></div>`).join('');
  $('#spacing').innerHTML = [1,2,3,4,6,8].map(n => `<div><i style="height:var(--ui-space-${n})"></i><code>${css('--ui-space-' + n)}</code></div>`).join('');

  let selectedMembers = [];
  const members = ['销售甲','销售乙','销售丙','销售丁','FDE 甲','FDE 乙'].map((name, index) => ({value: String(index), text: '示例' + name}));
  $('#member-picker').onclick = event => SalesSelect.open({
    anchor: event.currentTarget, title: '选择协作成员', description: '仅改变本页示例；多选后点击应用。', multiple: true,
    options: members, selected: selectedMembers,
    commit(values) {
      selectedMembers = values;
      const names = members.filter(m => values.includes(m.value)).map(m => m.text);
      $('#member-value').textContent = names.join('、') || '选择成员';
      $('#member-result').textContent = names.length ? '已选择 ' + names.length + ' 人：' + names.join('、') : '尚未选择成员';
    }
  });
  function bindDate(selector, output) {
    const anchor = $(selector);
    anchor.onclick = () => SalesDatePicker.open({anchor, mode: 'date', value: anchor.value, onCommit(value) {
      anchor.value = value; anchor.textContent = value + ' ▦';
      if (output) $(output).textContent = '已选：' + value;
    }});
  }
  bindDate('#date-demo', '#date-result'); bindDate('#visit-date');

  function resultState(state) {
    $$('[data-result-state]').forEach(button => {
      button.classList.toggle('active', button.dataset.resultState === state);
      button.setAttribute('aria-pressed', String(button.dataset.resultState === state));
    });
    const node = $('#result-demo'); node.setAttribute('aria-busy', String(state === 'loading'));
    const views = {
      normal: '<div><small>已登记回款</small><b>¥ 0</b><small>有记录，金额为零</small></div><div><small>已登记确收</small><b>未登记</b><small>缺少记录，不计作零</small></div>',
      loading: '<div class="state-message"><span class="spinner" aria-hidden="true"></span><p>正在加载经营数据…</p><small>当前示例保持加载状态，可通过上方按钮切换。</small></div>',
      empty: '<div class="state-message"><b>暂无跟进记录</b><p>记录一次拜访，开始积累客户信息。</p><a class="btn primary" href="#template-visit">记录客户拜访</a></div>',
      error: '<div class="state-message"><b>数据暂时无法加载</b><p>请重试；当前筛选条件会保留。</p><button class="btn" id="retry-demo">重试</button></div>'
    };
    node.innerHTML = views[state];
    if ($('#retry-demo')) $('#retry-demo').onclick = () => {resultState('normal'); feedback('示例重试成功，已恢复正常状态');};
  }
  $$('[data-result-state]').forEach(b => b.onclick = () => resultState(b.dataset.resultState));
  resultState('normal');
  $('#validation-form').onsubmit = event => {
    event.preventDefault();
    const field = $('#validation-title'), valid = !!field.value.trim();
    field.setAttribute('aria-invalid', String(!valid)); $('#validation-error').hidden = valid;
    $('#validation-result').textContent = valid ? '校验通过。本示例未提交任务。' : '';
    if (!valid) field.focus();
  };
  $('#validation-title').oninput = () => {if ($('#validation-title').value.trim()) {$('#validation-title').removeAttribute('aria-invalid'); $('#validation-error').hidden = true;}};

  function aiState(state) {
    const labels = {editing:'内容可编辑。核对后再发起质检。', analyzing:'AI 正在分析，暂时只读；可返回修改。', failed:'分析失败，输入已保留。可重试或返回修改。', review:'已生成建议，等待人工确认；尚未归档。'};
    $$('[data-ai]').forEach(button => {const active=button.dataset.ai===state;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
    $('#ai-draft').readOnly=state!=='editing';
    $('#ai-status').textContent=labels[state];$('#ai-status').setAttribute('aria-busy',String(state==='analyzing'));
    $('#ai-edit').hidden=state==='editing';$('#ai-retry').hidden=state!=='failed';
  }
  $$('[data-ai]').forEach(button=>button.onclick=()=>aiState(button.dataset.ai));
  $('#ai-edit').onclick=()=>{aiState('editing');$('#ai-draft').focus();};
  $('#ai-retry').onclick=()=>aiState('analyzing');aiState('editing');

  const activities = [
    {kind:'商', title:'智能质检试点', tone:'red', status:'转差', detail:'预计关单：9 月 30 日 → 10 月 30 日', reason:'合成依据：客户评审延期，原定本季度的关单计划顺延。', time:'10:40', action:'查看商机'},
    {kind:'客', title:'星河制造', tone:'yellow', status:'需关注', detail:'客户反馈：需补充试点验收指标', reason:'合成依据：本次沟通尚未确认验收范围，需要继续核对。', time:'10:30', action:'查看客户'},
    {kind:'商', title:'客服知识库', tone:'green', status:'向好', detail:'商机阶段：30% → 50%', reason:'合成依据：客户已同意开展方案评审，仍需跟踪后续采购安排。', time:'10:20', action:'查看商机'},
    {kind:'商', title:'门店助手', tone:'gray', status:'待评估', detail:'跟进记录已更新，变化判断尚未返回', reason:'暂无判断结果。记录更新成功不代表商机向好。', time:'10:10', action:'查看商机'},
    {kind:'任', title:'核对试点验收清单', tone:'green', status:'已完成', detail:'示例销售甲已提交核对结果', reason:'合成完成结果：客户确认首轮试点范围，补充指标另行安排。', time:'10:00', action:'查看任务', completed:true}
  ];
  let feedFilter = 'all';
  function renderFeed() {
    $('#feed').innerHTML = activities.filter(row => feedFilter !== 'attention' || ['red','yellow'].includes(row.tone)).map(row => `<article class="feed-row ${row.tone} ${row.completed ? 'completed' : ''}"><span class="feed-icon" aria-hidden="true">${row.kind}</span><div><b>${row.title}</b><p>${row.detail}</p><details><summary>展开依据</summary><p>${row.reason}</p></details></div><div class="feed-action"><span class="badge ${row.tone}">${row.completed ? '' : '● '}${row.status}</span><small>${row.time}</small><button class="btn text" data-feed-detail="${activities.indexOf(row)}">${row.action} →</button></div></article>`).join('');
    $$('[data-feed-detail]').forEach(button => button.onclick = () => {const row = activities[Number(button.dataset.feedDetail)]; showDialog(row.title, row.detail + '\n' + row.reason);});
    $$('[data-feed]').forEach(button => {const active = button.dataset.feed === feedFilter;button.classList.toggle('active', active);button.setAttribute('aria-pressed', String(active));});
  }
  $$('[data-feed]').forEach(button => button.onclick = () => {feedFilter = button.dataset.feed;renderFeed();}); renderFeed();

  const opportunities = [
    {name:'智能质检试点', customer:'星河制造', stage:'50%', owner:'销售甲', date:'2026-03-25', amount:180000, quarter:1},
    {name:'客服知识库', customer:'青禾零售', stage:'30%', owner:'销售乙', date:'2026-06-18', amount:160000, quarter:2},
    {name:'仓配异常工单助手一期与试点验收支持', customer:'远川物流', stage:'70%', owner:'销售甲', date:'2026-09-25', amount:200000, quarter:3},
    {name:'门店助手', customer:'青禾零售', stage:'10%', owner:'销售乙', date:'2026-09-30', amount:120000, quarter:3},
    {name:'设备知识检索', customer:'星河制造', stage:'90%', owner:'销售甲', date:'2026-11-20', amount:140000, quarter:4},
    {name:'经营分析平台', customer:'北辰科技', stage:'已成单', owner:'销售丙', date:'2026-12-10', amount:160000, quarter:4}
  ];
  const stageOptions = ['10%','30%','50%','70%','90%','已成单'].map(stage => ({value:stage,text:stage}));
  let selectedStages = [], overviewQuarters = [], analyticsQuarters = ['1','2','3'];
  function periodPicker(anchor, selected, title, description, commit) {
    SalesSelect.open({anchor,title,description,multiple:true,year:{value:2026,options:[2026]},
      options:[1,2,3,4].map(q => ({value:String(q),text:'Q' + q})),selected,commit:values => commit(values.sort())});
  }
  function renderOverview() {
    const selected = opportunities.filter(row => !overviewQuarters.length || overviewQuarters.includes(String(row.quarter)));
    const label = overviewQuarters.length ? overviewQuarters.map(q=>'Q'+q).join(' + ') : '全年';
    $('#overview-period').textContent = '统计时间：2026 ' + label + ' · 仅影响本区数字';
    $('#overview-picker').textContent = '统计季度：' + label + ' ⌄';
    $('#overview-count').textContent = selected.length;
    $('#overview-acv').textContent = money(selected.reduce((sum,row)=>sum+row.amount,0));
  }
  $('#overview-picker').onclick = event => periodPicker(event.currentTarget, overviewQuarters, '总览统计季度', '仅影响上方总览数字，不改变下方商机列表。', values => {overviewQuarters=values;renderOverview();});
  function renderList() {
    const query = $('#list-search').value.trim().toLowerCase();
    const rows = opportunities.filter(row => (!selectedStages.length || selectedStages.includes(row.stage)) && [row.name,row.customer,row.owner].join(' ').toLowerCase().includes(query));
    $('#list-count').textContent = `显示 ${rows.length} / ${opportunities.length} 条`;
    $('#stage-picker').textContent = '商机阶段：' + (selectedStages.join('、') || '全部') + ' ⌄';
    $('#list-conditions').innerHTML = (query ? `<button class="chip" data-remove-query>搜索：${esc($('#list-search').value.trim())} ×</button>` : '') + selectedStages.map(stage => `<button class="chip" data-remove-stage="${esc(stage)}">阶段：${esc(stage)} ×</button>`).join('') || '当前无筛选条件 · 显示全部商机';
    $('#opportunity-rows').innerHTML = rows.map(row => `<tr><td><b>${row.name}</b><small>${row.customer}</small></td><td><span class="badge ${row.stage === '已成单' ? 'green':'blue'}">${row.stage}</span></td><td>示例${row.owner}</td><td>${row.date}</td><td class="numeric">${money(row.amount)}</td><td><button class="btn text" data-opportunity="${opportunities.indexOf(row)}" aria-label="查看${row.name}">详情 →</button></td></tr>`).join('');
    $('#list-empty').hidden = rows.length > 0;
    $$('[data-remove-stage]').forEach(button => button.onclick = () => {selectedStages=selectedStages.filter(stage=>stage!==button.dataset.removeStage);renderList();$('#stage-picker').focus();});
    if ($('[data-remove-query]')) $('[data-remove-query]').onclick = () => {$('#list-search').value='';renderList();$('#list-search').focus();};
    $$('[data-opportunity]').forEach(button => button.onclick = () => {const row=opportunities[Number(button.dataset.opportunity)];showDialog(''+row.name, `客户：${row.customer}\n阶段：${row.stage}\n负责人：示例${row.owner}\n预计关单：${row.date}\nACV：${money(row.amount)}\n确收：未登记；回款：未登记。`);});
  }
  $('#stage-picker').onclick = event => SalesSelect.open({anchor:event.currentTarget,title:'商机列表 · 商机阶段',description:'仅筛选下方商机列表，总览统计不变。',multiple:true,options:stageOptions,selected:selectedStages,commit(values){selectedStages=values;renderList();}});
  $('#list-search').oninput = renderList;
  $('#list-clear').onclick = () => {selectedStages=[];$('#list-search').value='';renderList();};
  renderOverview();renderList();

  let detailObject = 'customer', detailTab = 'summary';
  function renderDetail() {
    const customer = detailObject === 'customer';
    $('#detail-kind').textContent = customer ? 'CUSTOMER / 客户' : 'OPPORTUNITY / 商机';
    $('#detail-title').textContent = customer ? '星河制造' : '智能质检试点';
    $('#detail-action').textContent = customer ? '记录客户拜访' : '更新商机';
    $('#detail-metrics').innerHTML = customer ? '<div><span>关联商机</span><b>2</b><small>同一客户下独立管理</small></div><div><span>拜访与跟进</span><b>5</b><small>已归档记录</small></div><div><span>待处理任务</span><b>1</b><small>已有任务记录</small></div>' : '<div><span>当前阶段</span><b>50%</b><small>方案推进中</small></div><div><span>商机 ACV</span><b>¥ 180,000</b><small>不等同于收入</small></div><div><span>已登记确收</span><b class="text-metric">未登记</b><small>尚无确收记录</small></div>';
    $$('[data-object]').forEach(button => {const active=button.dataset.object===detailObject;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
    $$('[data-detail-tab]').forEach(button => {const active=button.dataset.detailTab===detailTab;button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;});
    const contents = {
      summary: customer ? '<dl class="detail-facts"><div><dt>客户名称</dt><dd>星河制造</dd></div><div><dt>客户行业</dt><dd>制造业</dd></div><div><dt>最近跟进</dt><dd>2026-09-16</dd></div><div><dt>关系判断</dt><dd><span class="badge yellow">● 需关注</span></dd></div></dl><h4>经营摘要</h4><p>当前围绕质检场景推进试点，需要与客户核对验收指标和计划。</p>' : '<dl class="detail-facts"><div><dt>所属客户</dt><dd>星河制造</dd></div><div><dt>商机阶段</dt><dd>50%</dd></div><div><dt>预计关单</dt><dd>2026-09-30</dd></div><div><dt>实际关单</dt><dd>未填写</dd></div></dl><h4>推进摘要</h4><p>先核对试点验收指标，再确认采购评审安排。</p>',
      followups:'<h4>9 月 16 日 · 线上沟通</h4><p>示例销售甲与客户核对试点范围，客户提出补充验收指标。</p><details><summary>展开完整跟进记录</summary><p>沟通事实：客户愿意继续评估方案。\n客户反馈：补充准确率与验收周期。\n下一步：9 月 21 日由示例销售甲核对验收清单。</p></details><h4>9 月 10 日 · 客户拜访</h4><p>整理业务场景与现有处理流程。</p>',
      tasks:'<h4>核对试点验收清单 <span class="badge blue">已接受</span></h4><p>负责人：示例销售甲 · 截止：2026-09-21</p><p>接收方已确认接手，尚未提交完成结果。</p><details><summary>查看任务要求</summary><p>整理验收指标、负责人和时间计划，提交核对结果。本任务为合成示例。</p></details>'
    };
    $('#detail-content').innerHTML=contents[detailTab];$('#detail-content').setAttribute('aria-labelledby','tab-'+detailTab);
  }
  $$('[data-object]').forEach(button=>button.onclick=()=>{detailObject=button.dataset.object;renderDetail();});
  $$('[data-detail-tab]').forEach((button,index)=>{
    button.onclick=()=>{detailTab=button.dataset.detailTab;renderDetail();};
    button.onkeydown=event=>{const tabs=$$('[data-detail-tab]');let next;
      if(event.key==='ArrowRight')next=(index+1)%tabs.length;if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;
      if(event.key==='Home')next=0;if(event.key==='End')next=tabs.length-1;
      if(next!==undefined){event.preventDefault();tabs[next].click();tabs[next].focus();}};
  });
  $('#detail-action').onclick=()=>{if(detailObject==='customer')location.hash='template-visit';else showDialog('更新商机', '示例：进入商机编辑，并保留所属客户关系。\n本页不修改业务记录。');};
  renderDetail();

  $('#visit-form').onsubmit=event=>{
    event.preventDefault();const note=$('#visit-note'),valid=!!note.value.trim();
    $('#visit-error').hidden=valid;note.setAttribute('aria-invalid',String(!valid));
    $('#visit-result').textContent=valid?'内容已核对 · 未调用 AI，未归档':'请补齐必填内容';
    if(!valid)note.focus();else showDialog('核对拜访内容',`日期：${$('#visit-date').value}\n沟通内容：${note.value}\n下一步计划：${$('#visit-next').value||'未填写'}\n\n本示例未调用 AI，也未归档或创建任务。`);
  };
  $('#visit-note').oninput=()=>{if($('#visit-note').value.trim()){$('#visit-error').hidden=true;$('#visit-note').removeAttribute('aria-invalid');}$('#visit-result').textContent='内容已修改 · 待核对';};
  $('#visit-next').oninput=()=>{$('#visit-result').textContent='内容已修改 · 待核对';};

  let chart;
  function renderAnalytics() {
    const all=[{q:'1',value:6},{q:'2',value:8},{q:'3',value:12},{q:'4',value:4}];
    const rows=all.filter(row=>!analyticsQuarters.length||analyticsQuarters.includes(row.q));
    const label=analyticsQuarters.length?analyticsQuarters.map(q=>'Q'+q).join(' + '):'全年';
    $('#analytics-scope').textContent='示例本人 · 2026 年 '+label;
    $('#analytics-total').textContent=rows.reduce((sum,row)=>sum+row.value,0);
    $('#chart-data').innerHTML=rows.map(row=>`<tr><td>2026 Q${row.q}</td><td>${row.value}</td></tr>`).join('');
    if($('[data-template="analytics"]').hidden)return;
    chart ||= echarts.init($('#analytics-chart'),null,{renderer:'svg'});
    chart.setOption({animation:false,aria:{enabled:true},color:[css('--ui-primary')],tooltip:{trigger:'axis'},grid:{left:40,right:16,top:32,bottom:30},xAxis:{type:'category',data:rows.map(row=>'Q'+row.q),axisLine:{lineStyle:{color:css('--ui-line')}},axisTick:{show:false},axisLabel:{color:css('--ui-muted')}},yAxis:{type:'value',minInterval:1,name:'跟进次数',axisLabel:{color:css('--ui-muted')},nameTextStyle:{color:css('--ui-muted')},splitLine:{lineStyle:{color:css('--ui-line')}}},series:[{name:'跟进次数',type:'bar',barMaxWidth:56,data:rows.map(row=>row.value),itemStyle:{borderRadius:[4,4,0,0]},label:{show:true,position:'top',color:css('--ui-ink')}}]},true);chart.resize();
  }
  $('#analytics-period').onclick=event=>periodPicker(event.currentTarget,analyticsQuarters,'经营分析统计季度','仅影响本看板跟进数与图表；未登记金额继续保留为空。',values=>{analyticsQuarters=values;renderAnalytics();});
  new ResizeObserver(()=>chart?.resize()).observe($('#analytics-chart'));
  const captions={home:'T-01 · 待办、操作、动态明确分区；红黄业务判断进入“需关注”。',list:'T-02 · 总览与列表独立筛选，条件、数量与清除入口始终同区。',detail:'T-03 · 对象摘要 + 页签 + 主辅栏；客户与商机分别表达。',visit:'T-04 · 双栏录入、局部滚动、固定操作区；校验失败保留输入。',analytics:'T-05 · 范围与口径紧邻数字，图表有单位、数据表与缺失说明。'};
  function route() {
    SalesSelect.close();SalesDatePicker.close();if(dialog.open)dialog.close();
    const hash=location.hash.slice(1)||'foundations';
    const template=hash.startsWith('template-')&&captions[hash.slice(9)]?hash.slice(9):null;
    const section=template?'templates':hash==='components'?'components':'foundations';
    $$('[data-panel]').forEach(node=>node.hidden=node.dataset.panel!==section);
    $$('[data-view]').forEach(node=>{if(node.dataset.view===section)node.setAttribute('aria-current','page');else node.removeAttribute('aria-current');});
    $$('[data-template]').forEach(node=>node.hidden=node.dataset.template!==template);
    $$('.template-tabs a').forEach(node=>{if(node.hash===location.hash)node.setAttribute('aria-current','page');else node.removeAttribute('aria-current');});
    $('#section-name').textContent={foundations:'视觉基础',components:'组件与状态',templates:'页面模板'}[section];
    document.title=$('#section-name').textContent+' · SalesBuddy Web 设计规范';
    $('#pattern-caption').textContent=captions[template]||'';
    if(template==='analytics')requestAnimationFrame(renderAnalytics);
  }
  window.addEventListener('hashchange',route);route();
})();
