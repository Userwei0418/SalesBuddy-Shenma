/* Local CRM source inspection. No API writes or inferred historical stages. */
(function (global) {
  'use strict';
  let dataset, openSource, openCollection, customerIndex, opportunityIndex;
  const PAGE_SIZE = 50;
  const records = () => global.SalesPreview.inspect?.() || dataset.state;
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const shown = value => value == null || value === '' ? '未填写' : String(value);
  function mount(data) {
    dataset = data;
    customerIndex = new Map(data.state.customers.map(c => [c.id, c]));
    opportunityIndex = new Map(data.state.opportunities.map(o => [o.id, o]));
    const button = el('button', '样本与时间线'); button.id = 'crm-samples-open';
    document.getElementById('preview-notice').append(button);
    const dialog = el('dialog', null, 'crm-samples'); dialog.id = 'crm-samples';
    const header = el('header'), heading = el('h2', 'CRM 真实样本'), close = el('button', '关闭');
    header.append(heading, close); close.onclick = () => dialog.close();
    const intro = el('p', '5 个典型商机 · 26 条跟进。商机字段为导出时快照；下方按原始日期逐条查看跟进。', 'crm-note');
    const select = el('select'); select.id = 'crm-sample-select'; select.setAttribute('aria-label', '选择真实商机');
    for (const op of (data.demo_opportunity_ids || data.state.opportunities.map(o => o.id)).map(id => data.state.opportunities.find(o => o.id === id))) {
      const option = el('option', op.name); option.value = op.id; select.append(option);
    }
    const overview = el('section'), controls = el('nav'), previous = el('button', '上一条'), next = el('button', '下一条'), progress = el('span');
    previous.id = 'crm-previous'; next.id = 'crm-next'; controls.append(previous, progress, next);
    const timeline = el('section'); timeline.id = 'crm-timeline-record';
    dialog.append(header, intro, select, overview, controls, timeline); document.body.append(dialog);
    let index = 0;
    function field(parent, label, value) {
      const row = el('div', null, 'crm-field'); row.append(el('dt', label), el('dd', shown(value))); parent.append(row);
    }
    function render() {
      const state = records(), op = state.opportunities.find(op => op.id === select.value), customer = state.customers.find(c => c.id === op.customer_id);
      const visits = state.visits.filter(v => v.opportunity_id === op.id || v.opportunity_ids?.includes(op.id)).sort((a, b) => a.source_sequence - b.source_sequence);
      const visit = visits[index]; overview.replaceChildren(); timeline.replaceChildren();
      const grid = el('dl', null, 'crm-grid');
      field(grid, '客户 / 客户负责人', customer.name + ' / ' + shown(customer.owner_name));
      field(grid, '商机负责人', op.owner_name);
      field(grid, 'ACV', op.amount == null ? null : Number(op.amount).toLocaleString('zh-CN') + ' 元');
      field(grid, '当前阶段（导出快照）', op.source_snapshot['商机状态']);
      field(grid, '预计关单', op.expected_close_date || (op.source_close_quarter ? op.source_close_quarter + '（原表未填年份及具体日期）' : null));
      field(grid, '来源', `客户列表第 ${customer.source_ref.row} 行 / 商机列表第 ${op.source_ref.row} 行，编号 ${op.source_ref.key}`);
      overview.append(grid);
      progress.textContent = `第 ${index + 1} / ${visits.length} 条原始跟进`;
      previous.disabled = index === 0; next.disabled = index === visits.length - 1;
      if (!visit) return;
      timeline.append(el('h3', `${visit.visit_date} · ${visit.customer_type ? visit.customer_type + '跟进' : '跟进类型未填写'}`));
      timeline.append(el('p', `跟进记录第 ${visit.source_ref.row} 行 · 序号 ${visit.source_ref.key} · 创建日期 ${shown(visit.created_date)}`, 'crm-note'));
      if (visit.source_duplicate_rows) timeline.append(el('p', `疑似重复：来源第 ${visit.source_duplicate_rows.join('、')} 行的日期及正文相同，序号不同，均已保留。`, 'crm-warning'));
      const details = el('dl', null, 'crm-grid');
      field(details, '跟进人（原文）', visit.recorder_name);
      field(details, '创建人', visit.creator_name);
      field(details, '当次伙伴', visit.partner_name_snapshot);
      field(details, '对接人', visit.contact_name_snapshot);
      field(details, '当次参与 FDE', visit.fde_participants.map(p => p.name).join('、'));
      field(details, '首次拜访 / 质量评分', '原表未记录');
      timeline.append(details);
      const narrative = el('dl'); field(narrative, '沟通内容', visit.follow_up_record); field(narrative, '下一步计划', visit.next_action);
      timeline.append(narrative);
      const native = el('button', '在当前页面查看这条跟进'); native.onclick = () => {
        dialog.close(); SalesRuntime.route(`/pages/visit-detail/index?customer_id=${encodeURIComponent(visit.customer_id)}&visit_id=${encodeURIComponent(visit.id)}`);
      }; timeline.append(native);
    }
    select.onchange = () => {index = 0; render();};
    previous.onclick = () => {index--; render();}; next.onclick = () => {index++; render();};
    button.onclick = () => {render(); dialog.showModal();};
    if (data.scope === 'full') mountFull();
  }
  function field(parent, label, value) {
    const row = el('div', null, 'crm-field'); row.append(el('dt', label), el('dd', shown(value))); parent.append(row);
  }
  function mountFull() {
    const button = el('button', '全量数据'); button.id = 'crm-all-open';
    document.getElementById('preview-notice').append(button);
    const dialog = el('dialog', null, 'crm-samples crm-all'); dialog.id = 'crm-all';
    const head = el('header'), close = el('button', '关闭');
    head.append(el('h2', 'CRM 全量数据'), close); close.onclick = () => dialog.close();
    const tabs = el('nav'), summary = el('p', null, 'crm-note'); summary.id = 'crm-all-summary';
    const search = el('input'); search.id = 'crm-all-search'; search.type = 'search'; search.placeholder = '搜索全量数据：名称、人员、原文或源表行号'; search.setAttribute('aria-label', search.placeholder);
    const filter = el('select'); filter.id = 'crm-all-filter'; filter.setAttribute('aria-label', '数据筛选');
    const list = el('section'); list.id = 'crm-all-list'; const detail = el('section'); detail.id = 'crm-source-detail';
    const pager = el('nav'), prev = el('button', '上一页'), position = el('span'), next = el('button', '下一页');
    prev.id = 'crm-all-prev'; next.id = 'crm-all-next'; pager.append(prev, position, next);
    dialog.append(head, summary, tabs, search, filter, pager, list, detail); document.body.append(dialog);
    let kind = 'customers', page = 0;
    const unresolved = row => kind === 'customers' ? !row.source_name : !row.customer_id || (kind === 'visits' && !row.opportunity_id);
    function show(row) {
      detail.replaceChildren(el('h3', row.name || row.opportunity_name || row.partner_name_snapshot || '跟进记录'));
      detail.append(el('p', `${row.source_ref.sheet}第 ${row.source_ref.row} 行 · 原始编号 ${row.source_ref.key}`, 'crm-note'));
      const grid = el('dl');
      if (kind !== 'customers') field(grid, '客户关联', row.customer_id ? '已唯一匹配' : `待确认 · ${row.customer_candidate_ids?.length || 0} 个候选客户`);
      if (kind === 'visits') field(grid, '商机关联', row.opportunity_id ? '已唯一匹配' : row.opportunity_ids?.length ? `关联 ${row.opportunity_ids.length} 个商机，保留原关系` : '原表无关联商机');
      for (const [label, value] of Object.entries(row.source_snapshot || {})) field(grid, label, value);
      detail.append(grid);
      detail.scrollIntoView({block: 'start'});
    }
    function render() {
      const s = records(); const q = search.value.trim().toLocaleLowerCase();
      const all = s[kind];
      const rows = all.filter(r => (!q || JSON.stringify([r.name, r.owner_name, r.recorder_name, r.source_ref, r.source_snapshot]).toLocaleLowerCase().includes(q)) &&
        (filter.value === 'all' || (filter.value === 'pending' ? unresolved(r) : filter.value === 'unknown' ? !r.customer_type : r.customer_type === filter.value)));
      page = Math.max(0, Math.min(page, Math.ceil(rows.length / PAGE_SIZE) - 1));
      const start = page * PAGE_SIZE; list.replaceChildren();
      summary.textContent = `${s.customers.length.toLocaleString()} 个客户 · ${s.opportunities.length} 个商机 · ${s.visits.length} 条跟进。来源空值与待确认关联均已保留。`;
      position.textContent = `${rows.length.toLocaleString()} 条 · 第 ${page + 1} / ${Math.max(1, Math.ceil(rows.length / PAGE_SIZE))} 页`;
      prev.disabled = page === 0; next.disabled = start + PAGE_SIZE >= rows.length;
      for (const row of rows.slice(start, start + PAGE_SIZE)) {
        const item = el('button', null, 'crm-data-row'); item.dataset.id = row.id;
        const title = row.name || row.opportunity_name || row.partner_name_snapshot || `跟进序号 ${row.source_ref.key}`;
        item.append(el('strong', title));
        const parts = [`${row.source_ref.sheet}第 ${row.source_ref.row} 行`];
        if (kind === 'customers') parts.push('负责人：' + shown(row.owner_name));
        if (kind === 'opportunities') parts.push(row.amount == null ? '金额未填写' : `${row.amount.toLocaleString()} 元`, row.source_snapshot?.['商机状态'] || '阶段未填写');
        if (kind === 'visits') parts.push(row.visit_date, row.customer_type ? row.customer_type + '跟进' : '类型未填写');
        if (unresolved(row)) parts.push(kind === 'customers' ? '名称待补充' : '关联待确认');
        item.append(el('span', parts.join(' · '))); item.onclick = () => show(row); list.append(item);
      }
      if (!rows.length) list.append(el('p', '没有匹配的记录'));
    }
    function selectKind(value) {
      kind = value; page = 0; search.value = ''; detail.replaceChildren(); filter.replaceChildren();
      const options = [['all','全部记录'], ['pending', kind === 'customers' ? '名称待补充' : '关联待确认'], ...(kind === 'visits' ? [['伙伴','伙伴跟进'], ['客户','客户跟进'], ['unknown','类型未填写']] : [])];
      for (const [value, label] of options) {const option = el('option', label); option.value = value; filter.append(option);}
      for (const tab of tabs.children) tab.setAttribute('aria-pressed', String(tab.dataset.kind === kind));
      render();
    }
    for (const [value,label] of [['customers','客户'], ['opportunities','商机'], ['visits','跟进记录']]) {
      const tab = el('button', label); tab.dataset.kind = value; tab.onclick = () => selectKind(value); tabs.append(tab);
    }
    search.oninput = () => {page = 0; detail.replaceChildren(); render();}; filter.onchange = search.oninput;
    prev.onclick = () => {page--; render();}; next.onclick = () => {page++; render();};
    button.onclick = () => {selectKind(kind); dialog.showModal();};
    openSource = (kind, row) => {selectKind(kind); dialog.showModal(); show(row);};
    openCollection = kind => {selectKind(kind); dialog.showModal();};
    // An unresolved customer cannot be opened through the native customer route.
    document.getElementById('page-root').addEventListener('click', event => {
      const card = event.target.closest('.workbench-opportunity-card');
      if (!card) return;
      const row = records().opportunities.find(o => o.id === card.dataset.opportunityId);
      if (row && !row.customer_id) {event.stopImmediatePropagation(); openSource('opportunities', row);}
    }, true);
  }
  function transformPatch(page, patch) {
    if (dataset?.scope !== 'full') return patch;
    if (page.route === 'pages/workbench/index' && Array.isArray(patch?.opportunityGroups)) {
      return {...patch, opportunityGroups:patch.opportunityGroups.map(group => ({...group, items:group.items.map(row => {
        // Shared native cards assume a known stage; source null must remain unknown.
        const source = row.source_kind === 'crm_export' ? row : opportunityIndex.get(row.id);
        if (!source) return row;
        return {...row, ...(!source.stage_code && !source.status ? {stageName:source.source_snapshot?.['商机状态'] || '阶段未填写', probabilityText:'未填写', progressPercent:0,
          signal:{tone:'unknown', label:'待确认', detail:'阶段未填写'}} : {}),
          ...(!source.customer_id ? {customer_name:(source.customer_name || '客户未填写') + ' · 关联待确认', canEdit:false} : {})};
      })}))};
    }
    if (page.route !== 'pages/customers/index' || !Array.isArray(patch?.customers) || page._crmPaging) return patch;
    // Keep the full filtered list outside reactive page data; only 50 cards render.
    page._crmCustomers = patch.customers; page._crmPage = 0;
    return {...patch, customers: patch.customers.slice(0, PAGE_SIZE),
      plotCustomers:(patch.plotCustomers || []).filter(c => {const source = customerIndex.get(c.id); return source?.potential_score != null && source?.relationship_score != null;})};
  }
  function decorateCustomers(root, page) {
    if (!page._crmCustomers) return;
    const count = root.querySelector('.asset-list-count'), total = page._crmCustomers.length;
    const countText = `${total.toLocaleString()} 家 · 全量筛选结果`;
    if (count && count.textContent !== countText) count.textContent = countText;
    const list = root.querySelector('.customer-list');
    if (!list) return;
    if (!root.querySelector('#crm-customer-pager')) {
      const nav = el('nav', null, 'crm-customer-pager'); nav.id = 'crm-customer-pager';
      const previous = el('button', '上一页'), text = el('span', `第 ${page._crmPage + 1} / ${Math.max(1, Math.ceil(total / PAGE_SIZE))} 页 · 每页 ${PAGE_SIZE} 家`), next = el('button', '下一页');
      previous.id = 'crm-customer-prev'; next.id = 'crm-customer-next';
      function change(delta) {page._crmPage += delta; page._crmPaging = true; page.setData({customers:page._crmCustomers.slice(page._crmPage * PAGE_SIZE, (page._crmPage + 1) * PAGE_SIZE)}); page._crmPaging = false;}
      previous.disabled = page._crmPage === 0; next.disabled = (page._crmPage + 1) * PAGE_SIZE >= total;
      previous.onclick = () => change(-1); next.onclick = () => change(1); nav.append(previous, text, next); list.before(nav);
    }
    const emptyMap = root.querySelector('.map-empty');
    if (emptyMap && total && emptyMap.textContent !== '原表未记录客户潜力和关系评分，可在客户列表中查看') emptyMap.textContent = '原表未记录客户潜力和关系评分，可在客户列表中查看';
  }
  function decorateQuarterSummary(root, page) {
    const c = page.data.opportunityBoard?.crm_date_coverage;
    if (!c || !page.data.opportunityDataReady || page.data.opportunityOverviewLoading) return;
    const filtered = page.data.summaryQuarter.quarters.length > 0;
    const unavailable = [c.won_count > 0 && !c.won_dated, c.source_count > 0 && !c.close_dated,
      c.active_count > 0 && !c.active_close_dated, c.source_count > 0 && !c.created_dated];
    const metrics = root.querySelectorAll('.opportunity-summary-value');
    metrics.forEach((node, index) => {
      if (!filtered || !unavailable[index]) {node.classList.remove('crm-stat-unavailable'); return;}
      // A native rerender can replace metric text while retaining our DOM class.
      // Check the displayed value as well so list changes cannot expose fake zeroes.
      if (!node.classList.contains('crm-stat-unavailable') || node.textContent !== '—日期不足，无法统计') {
        node.classList.add('crm-stat-unavailable'); node.replaceChildren(el('span', '—'), el('small', '日期不足，无法统计'));
      }
    });
    const board = root.querySelector('.opportunity-summary-card');
    if (!board || !c.source_count) return;
    // Replace the low-contrast generic warning with the precise source distinction.
    for (const note of board.querySelectorAll(':scope > .summary-data-note')) {
      if (note.textContent.includes('未计入')) note.hidden = true;
    }
    const text = filtered
      ? '季度筛选需要对应年份的日期。缺少日期的记录仍在全量数据中；“—”表示无法判断该季度数量。'
      : '全部时间展示当前状态快照；新增商机按所选年份的创建日期统计。季度统计还需补齐年份及对应日期。';
    const detail = `当前范围 ${c.source_count} 个商机：${c.close_dated} 个有完整预计关单日期；${c.quarter_only} 个只填季度、年份未填写；${c.source_count - c.close_dated - c.quarter_only} 个未填预计关单时间。${c.won_count - c.won_dated} 个已成单商机缺少实际成单日期。新增商机按创建日期单独统计。`;
    const signature = JSON.stringify([filtered, c]);
    let note = root.querySelector('#crm-quarter-explanation');
    if (note?.dataset.signature === signature) return;
    if (!note) {note = el('section', null, 'crm-date-explanation'); note.id = 'crm-quarter-explanation'; board.append(note);}
    note.dataset.signature = signature; note.replaceChildren(el('strong', text), el('p', detail));
    if (c.quarter_only) {
      const quarters = el('div', null, 'crm-source-quarters');
      quarters.append(el('span', '原表季度（年份待确认）'));
      for (const [q, count] of Object.entries(c.quarters)) quarters.append(el('span', `${q}：${count} 个`));
      note.append(quarters);
    }
    if (filtered) {
      const all = el('button', '查看全部时间'); all.id = 'crm-show-all-periods';
      all.onclick = () => page.toggleQuarter({currentTarget:{dataset:{scope:'summary', value:'all'}}}); note.append(all);
    }
  }
  function decorate(root, page) {
    if (dataset?.scope === 'full' && page?.route === 'pages/customers/index') decorateCustomers(root, page);
    if (dataset && page?.route === 'pages/workbench/index') decorateQuarterSummary(root, page);
    if (dataset?.scope === 'full' && page?.route === 'pages/visit-entry/index' && !root.querySelector('#crm-history-open')) {
      const button = el('button', `查看历史跟进（${dataset.counts.visits} 条）`, 'crm-history-open'); button.id = 'crm-history-open';
      button.onclick = () => openCollection('visits'); root.prepend(button);
    }
    if (!dataset || page?.route !== 'pages/visit-detail/index') return;
    const visit = dataset.state.visits.find(v => v.id === page.data.visitId);
    if (!visit) return;
    // The shared mini-program normalizer treats null first-visit as false.
    // Override only this local source's missing-value presentation, preserving source files.
    for (const field of root.querySelectorAll('.field-grid > *')) {
      const label = field.firstElementChild, value = field.lastElementChild;
      if (label?.textContent === '是否首次拜访' && visit.is_first_visit == null && value.textContent !== '未记录') value.textContent = '未记录';
      if (label?.textContent === '是否七天内' && value.textContent && !value.textContent.includes('导出时')) value.textContent += '（导出时）';
    }
    const target = root.querySelector('.hero-meta > :last-child');
    const text = visit.customer_type ? visit.customer_type + '跟进' : '跟进类型未填写';
    if (target && target.textContent !== text) target.textContent = text;
  }
  global.SalesCrmPreview = Object.freeze({mount, decorate, transformPatch});
})(window);
