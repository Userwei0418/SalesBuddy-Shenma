/* Explicit, local-only example workspace for the Web compatibility client.
 * Business calls stay offline. Optional CRM samples load from a loopback-only
 * endpoint, only in preview mode; this is never a fallback for a live failure.
 * `data_source: database` is the imported page contract discriminator only;
 * every response carries demo/source labels, and the Web shell labels preview.
 */
(function (global) {
  'use strict';
  let KEY = 'sales-web:preview-workspace:v1';
  const VERSION = 9;
  // Isolated synthetic metadata; live reads always use the authenticated backend.
  const BUSINESS_OPTIONS = {"contract_version": 1, "data_source": "synthetic_preview", "customer": {"industry": ["智能制造", "企业软件", "新能源", "医药健康", "交通物流", "人工智能", "其他"], "customer_type": ["潜在客户", "商机客户", "已成单客户", "新拓客户", "存量客户"], "level_code": ["Tier-1", "Tier-2", "Tier-3"], "source": ["销售自拓", "客户转介绍", "市场活动", "销售线索", "合作伙伴", "公司分配", "自主拓展", "其他"], "contact_role": ["使用者", "影响者", "决策者"]}, "opportunity": {"stages": [{"code": "identified", "label": "意向沟通", "probability": 10, "status": "open", "text": "意向沟通－10%"}, {"code": "qualified", "label": "商机确认", "probability": 30, "status": "open", "text": "商机确认－30%"}, {"code": "solution", "label": "方案沟通", "probability": 50, "status": "open", "text": "方案沟通－50%"}, {"code": "proposal", "label": "商务谈判", "probability": 70, "status": "open", "text": "商务谈判－70%"}, {"code": "negotiation", "label": "客户签约", "probability": 90, "status": "open", "text": "客户签约－90%"}, {"code": "won", "label": "赢单 Won", "probability": 100, "status": "won", "text": "赢单 Won－100%"}, {"code": "lost", "label": "丢单 Lost", "probability": null, "status": "lost", "text": "丢单 Lost"}], "grades": [{"code": "A", "label": "A｜100万以上", "amountLabel": "100万以上", "min": 1000000, "max": null}, {"code": "B", "label": "B｜50万–100万", "amountLabel": "50万–100万", "min": 500000, "max": 1000000}, {"code": "C", "label": "C｜10万–50万", "amountLabel": "10万–50万", "min": 100000, "max": 500000}, {"code": "D", "label": "D｜10万以下", "amountLabel": "10万以下", "min": 0, "max": 100000}]}, "map_amount_ranges": [{"value": "0-50", "label": "0–50 万", "min": 0, "max": 499999.99}, {"value": "50-100", "label": "50–100 万", "min": 500000, "max": 999999.99}, {"value": "100-300", "label": "100–300 万", "min": 1000000, "max": 2999999.99}, {"value": "300-500", "label": "300–500 万", "min": 3000000, "max": 4999999.99}, {"value": "500+", "label": "500 万以上", "min": 5000000, "max": null}]};
  let LABEL = '渠道销售工作区';
  let localDataset = null;
  const ROLES = ['sales', 'supervisor', 'manager', 'fde', 'fde_lead'];
  const NAMES = ['王新源', '李明哲', '陈国栋', '周思远', '赵文博'];
  const TITLES = ['一线销售', '销售主管', '销售总经理', 'FDE', 'FDE主管'];
  const STAGES = ['identified', 'qualified', 'solution', 'proposal', 'negotiation', 'won', 'lost'];
  const PROBABILITY = [10, 30, 50, 70, 90, 100, null];
  const STAGE_NAMES = ['意向沟通', '商机确认', '方案沟通', '商务谈判', '客户签约', '赢单', '丢单'];
  const CAPS = ['customer.read', 'opportunity.read', 'customer.create', 'customer.edit', 'customer.claim', 'opportunity.edit', 'fde.members.manage', 'visit.create', 'visit.supplement', 'task.create', 'task.respond', 'task.coordinate', 'risk.resolve', 'advice.decide', 'actual.manage', 'team.view', 'console.access'];
  const uid = (kind, index) => `${String(kind).padStart(8, '0')}-0000-4000-8000-${String(index).padStart(12, '0')}`;
  const now = () => new Date().toISOString();
  const today = () => new Date(Date.now() + 28800000).toISOString().slice(0, 10);
  const yearNow = () => Number(today().slice(0, 4));
  const date = (days = 0, hour = '10:00:00') => new Date(Date.now() + days * 86400000 + 28800000).toISOString().slice(0, 10) + 'T' + hour + '+08:00';
  const copy = value => JSON.parse(JSON.stringify(value));
  const sum = (rows, key) => rows.reduce((n, row) => n + Number(row[key] || 0), 0);
  const uniq = values => [...new Set(values.filter(Boolean))];
  const isFde = actor => actor.role === 'fde' || actor.role === 'fde_lead';
  function previewPermissions(role) {
    const sales=!role.startsWith('fde'),lead=['supervisor','manager','fde_lead'].includes(role);
    const common='access.mini_program overview.read customer.read battle_map.read opportunity.read actual.read visit.read visit.create visit.supplement visit.upload visit.transcribe visit.structure visit.quality_review visit.retry_import visit.attendance_manage task.read task.create_daily task.create_customer task.accept task.decline task.complete task.review risk.read advice.read advice.request advice.visit advice.opportunity advice.customer advice.decide target.read target.submit agent.operating_report'.split(' ');
    const specialized=(sales?'customer.create customer.update customer.claim opportunity.create opportunity.update opportunity.fde_members visit.first_visit dashboard.read dashboard.ranking profile.sales_read profile.sales_review agent.opportunity_draft actual.create actual.void risk.resolve':'profile.fde_read profile.fde_review profile.fde_activity dashboard.ranking demo_scene.read demo_scene.create demo_scene.update demo_scene.delete').split(' ');
    const granted=[...common,...specialized,...(lead?['task.coordinate','task.cancel']:[]),...(role==='fde_lead'?['opportunity.fde_members']:[])];
    return {permissions:Object.fromEntries(granted.map(code=>[code,true])),permission_grants:granted.map(permission_code=>({permission_code,effect:'allow',scope_code:permission_code==='visit.create'&&!sales?'assigned':lead?'teams':'self'}))};
  }
  function actorFor(role) {
    const index = ROLES.indexOf(role);
    const sales = !role.startsWith('fde');
    const open = sales ? CAPS.filter(key => key !== 'console.access' && (role !== 'sales' || !['team.view', 'task.coordinate'].includes(key))) :
      ['customer.read', 'opportunity.read', 'task.create', 'task.respond', 'visit.create', 'visit.supplement', ...(role === 'fde_lead' ? ['team.view', 'fde.members.manage', 'task.coordinate'] : [])];
    const bound = localDataset?.actors.find(person => person.user_id === localDataset.role_bindings[role]);
    return { user_id: bound?.user_id || uid(1, index + 1), workspace_id: uid(2, 1), account_code: 'PREVIEW_' + role.toUpperCase(), display_name: localDataset ? '本地' + TITLES[index] + '视角' + (bound ? ' · ' + bound.display_name : '') : NAMES[index], role,
      role_name: TITLES[index], scope_name: sales ? role === 'manager' ? '全部团队' : role === 'supervisor' ? '直属团队' : '仅本人' : role === 'fde' ? '本人协助项目' : 'FDE 团队协助项目',
      team_ids: [uid(3, 1)], team_names: [localDataset ? '本地样本组（非实际组织）' : '渠道销售-南区'], ...previewPermissions(role), permission_version: 'preview-v109', capabilities: Object.fromEntries(CAPS.map(key => [key, open.includes(key)])), demo: true };
  }
  let actors = ROLES.map(actorFor);
  function member(actor) { return { id: actor.user_id, user_id: actor.user_id, account_code: actor.account_code, name: actor.display_name, display_name: actor.display_name, role: actor.role, team_id: actor.team_ids[0], team_ids: actor.team_ids.slice(), team_name: actor.team_names[0], team: actor.team_names[0], active: true }; }
  function directoryTeams(actor) {
    const first = {id: uid(3, 1), parent_id: null, code: 'preview_team_1', name: localDataset ? '本地样本组（非实际组织）' : '渠道销售-南区', active: true, member_count: actors.length};
    return [first, ...(actor.capabilities['team.view'] ? [{id: uid(3, 2), parent_id: null, code: 'preview_empty', name: '渠道销售-北区', active: true, member_count: 0}] : [])];
  }
  function seed() {
    if (localDataset) return {...copy(localDataset.state), version: VERSION};
    const year = yearNow();
    const customerNames = ['星河智能制造（广州）有限公司', '远山科技（深圳）有限公司', '青禾零售集团有限公司', '云帆物流股份有限公司', '晨光新能源科技有限公司', '长桥智造（东莞）有限公司', '湖畔教育科技有限公司', '蓝田企业服务有限公司'];
    const productLines = ['智能质检', '知识助理', '客户服务', '经营分析'];
    const quadrants = ['main_attack', 'customer_asset', 'order_driven', 'customer_resource'];
    const customerRows = customerNames.map((name, i) => ({ id: uid(10, i + 1), name,
      industry_code: ['制造业', '科技服务', '零售', '物流'][i % 4], level_code: ['A', 'A', 'B', 'C'][i % 4], source_code: ['销售自拓', '客户转介绍', '市场活动', '渠道推荐'][i % 4],
      customer_type_code: i % 3 === 1 ? 'won' : 'opportunity', lifecycle_status: 'active', owner_user_ref_id: actors[i < 6 ? 0 : 1].user_id,
      owner_id: actors[i < 6 ? 0 : 1].user_id, owner_name: actors[i < 6 ? 0 : 1].display_name, owner_team_id: uid(3, 1), team_name: '渠道销售-南区',
      potential_score: [87, 92, 42, 55, 76, null, 0, 33, 94][i % 9], relationship_score: [48, 83, 38, 85, 32, null, 0, 76, 63][i % 9], quadrant_code: quadrants[i % 4],
      quadrant_policy: {definition: {potential_threshold: 70, relationship_threshold: 70, inclusive: true}},
      latest_visit_at: date(-[1, 3, 9, 2, 16, 5, 12, 4][i]), weekly_follow_up_count: [3, 2, 0, 1, 0, 2, 0, 1][i], cooperation_years: i % 4,
      address: ['广州市天河区', '深圳市南山区', '上海市浦东新区', '杭州市滨江区'][i % 4], primary_partner_name: i % 2 ? '华南数码渠道' : '直销', attributes: {next_action: '确认下一次方案沟通时间'},
      version_no: 1, created_at: date(-90), updated_at: date(-i), data_kind: 'demo', sales_members: [member(actors[i < 6 ? 0 : 1])] }));
    const opportunities = customerRows.flatMap((customer, ci) => Array.from({length: 4}, (_, oi) => {
      const index = ci * 4 + oi, stageIndex = [2, 1, 3, 4, 0, 5, 2, 6][index % 8];
      const quarter = 1 + index % 4, amount = [1280000, 480000, 960000, 260000, 1750000, 680000][index % 6];
      return {id: uid(11, index + 1), customer_id: customer.id, customer_name: customer.name, name: `${productLines[oi]}${oi === 0 ? '试点' : '项目'}`,
        amount, stage_code: STAGES[stageIndex], probability: PROBABILITY[stageIndex], status: stageIndex === 5 ? 'won' : stageIndex === 6 ? 'lost' : 'open',
        expected_close_date: `${year}-${String(quarter * 3).padStart(2, '0')}-25`, won_at: stageIndex === 5 ? `${year}-${String(quarter * 3).padStart(2, '0')}-25` : null, product_line: productLines[oi], partner_name: ci % 2 ? '华南数码渠道' : '直销',
        sales_channel: ci % 2 ? 'partner' : 'direct', partner_id: ci % 2 ? uid(14, 1) : null,
        owner_id: customer.owner_id, owner_user_ref_id: customer.owner_id, owner_name: customer.owner_name, team_name: customer.team_name, team_id: customer.owner_team_id,
        quarterly_forecasts: [{year, quarter, recognized_amount: Math.round(amount * 0.7), collection_amount: Math.round(amount * 0.5)}],
        fde_members: [member(actors[3]), ...(index % 3 === 0 ? [member(actors[4])] : [])], fde_member_ids: [actors[3].user_id, ...(index % 3 === 0 ? [actors[4].user_id] : [])],
        version_no: 1, created_at: date(-30 - index), updated_at: date(-ci), data_kind: 'demo', risk_summary: {open_count: 0} };
    }));
    const contacts = customerRows.flatMap(customer => [1, 2].map(n => ({id: uid(12, Number(customer.id.slice(-12)) * 10 + n), customer_id: customer.id,
      name: n === 1 ? '张晓静' : '刘志强', title: n === 1 ? '信息化部负责人' : '业务部负责人', relationship_role_code: n === 1 ? 'decision_maker' : 'user', is_primary: n === 1})));
    const visits = opportunities.filter((_, i) => i % 2 === 0).map((op, i) => ({id: uid(13, i + 1), customer_id: op.customer_id, customer_name: op.customer_name,
      opportunity_id: op.id, opportunity_name: op.name, recorder_id: i % 3 === 0 ? actors[i % 6 === 0 ? 4 : 3].user_id : op.owner_id, recorder_name: i % 3 === 0 ? actors[i % 6 === 0 ? 4 : 3].display_name : op.owner_name, creator_name: i % 3 === 0 ? actors[i % 6 === 0 ? 4 : 3].display_name : op.owner_name,
      interaction_at: date(-i), visit_date: date(-i).slice(0, 10), created_at: date(-i), created_date: date(-i), customer_type: 'opportunity',
      follow_up_record: '已介绍试点方案，下一次会议将确认验收范围和参与人员。', next_action: '整理试点清单，预约下一次方案沟通。', visit_goal: '确认试点边界',
      interaction_mode_code: 'online_meeting', expectation_code: 'met', duration_minutes: 45, contact_name_snapshot: '张晓静',
      partner_name_snapshot: op.partner_name, fde_participants: op.fde_members, fde_participant_ids: op.fde_member_ids, collaborators: [], within_seven_days: i < 7,
      can_read_detail: true, is_first_visit: false, status: 'archived', quality_score: null, follow_up_score: null, data_kind: 'demo', version_no: 1 }));
    const tasks = Array.from({length: 27}, (_, i) => {
      const owner = actors[i < 22 ? 0 : 3], op = opportunities[i % opportunities.length];
      const status = ['pending_confirm', 'pending_execution', 'in_progress', 'pending_execution', 'completed'][i % 5];
      return {id: uid(15, i + 1), description: `${['确认试点范围与验收标准', '补充客户需求清单', '整理方案会议材料', '跟进试点反馈', '更新商机推进计划'][i % 5]}`,
        status, priority_code: i % 3 ? 'medium' : 'high', due_at: date(i % 5 - 1, '17:00:00'), created_at: date(-i - 1), completed_at: status === 'completed' ? date(0) : null,
        assignee_name: owner.display_name, owner_name: owner.display_name, assignees: [{user_id: owner.user_id, name: owner.display_name, responsibility: 'owner'}],
        creator_user_ref_id: actors[1].user_id, creator_name: actors[1].display_name, customer_id: op.customer_id, customer_name: op.customer_name,
        opportunity_id: op.id, opportunity_name: op.name, association_kind: 'customer', team_name: '渠道销售-南区', task_type: 'management', requires_action: status === 'pending_confirm',
        version_no: 1, can_coordinate: true, handover_required: false, events: [], data_kind: 'demo'};
    });
    const actuals = opportunities.filter((_, i) => i % 4 === 0).flatMap((op, i) => ['recognized', 'collection'].map((kind, k) => ({
      id: uid(16, i * 2 + k + 1), customer_id: op.customer_id, customer_name: op.customer_name, opportunity_id: op.id, opportunity_name: op.name,
      owner_id: op.owner_id, kind, amount: [180000, 120000][k] + i * 20000, occurred_on: `${year}-0${1 + i % 8}-15`, confirmed: true,
      source_ref: 'FIN-' + year + '-' + String(i + 1).padStart(3, '0'), note: '财务已确认', created_at: date(-5), created_by_name: NAMES[0], status: 'confirmed', data_kind: 'demo' })));
    customerRows.push({id: uid(10, 9), name: '南海精工机械有限公司', industry_code: '企业软件', customer_type_code: '潜在客户', level_code: 'Tier-2', source_code: '市场活动',
      owner_id: null, owner_user_ref_id: null, owner_name: '', owner_team_id: uid(3, 1), team_name: '渠道销售-南区', ownership_state: 'unassigned', sales_members: [],
      primary_partner_name: '无', potential_score: null, relationship_score: null, attributes: {}, version_no: 1, created_at: date(-2), updated_at: date(-2), data_kind: 'demo'});
    // 业务动态里的红黄绿：三条已完成评估的业务变化，给总览的「需关注」用
    const changed = (n, days, opIndex, color, title, summary, changes) => ({id: uid(25, n), recipient_user_ref_id: actors[0].user_id, template_code: 'business_changed', object_type: 'opportunity', object_id: opportunities[opIndex].id,
      title, body: `${opportunities[opIndex].customer_name} · ${opportunities[opIndex].name}`, created_at: date(-days, '09:30:00'), read_at: null, data_kind: 'demo',
      payload: {customer_id: opportunities[opIndex].customer_id, opportunity_id: opportunities[opIndex].id, actor_name: NAMES[1], event_type: 'updated', changes,
        change_review: {status: 'completed', color, title, summary, source: 'rules'}}});
    const forRoles = (row, n, roles) => roles.map((r, k) => ({...row, id: uid(25, n * 10 + k), recipient_user_ref_id: actors[r].user_id}));
    const simple = (n, recipient, days, template, objectType, row, title, body, payload) => ({id: uid(25, n), recipient_user_ref_id: actors[recipient].user_id, template_code: template, object_type: objectType, object_id: row.id, title, body, payload, created_at: date(-days, '08:40:00'), read_at: null, data_kind: 'demo'});
    const notifications = [
      ...forRoles(changed(0, 0, 2, 'red', '商机推进停滞', '预计关单已过期 12 天，近两周没有跟进记录', [{label: '预计关单', before: '2026-09-09', after: '2026-09-30'}]), 1, [0, 1, 2]),
      ...forRoles(changed(0, 1, 5, 'yellow', '关系深度下降', '关键联系人一个月未沟通，关系评分从 83 降到 71', [{label: '关系评分', before: '83', after: '71'}]), 2, [0, 1, 2]),
      ...forRoles(changed(0, 2, 1, 'green', '阶段推进', '商机阶段从方案沟通进入商务谈判', [{label: '商机阶段', before: '方案沟通', after: '商务谈判'}]), 3, [0, 1, 2]),
      ...forRoles(changed(0, 1, 0, 'yellow', '试点验收延期', '客户要求补充验收标准，交付节点后移两周', [{label: '预计签约', before: '2026-03-25', after: '2026-04-08'}]), 4, [3, 4]),
      simple(51, 0, 1, 'task_assigned', 'task', tasks[0], '收到新任务', tasks[0].description, {task_id: tasks[0].id, customer_id: tasks[0].customer_id}),
      simple(52, 3, 0, 'task_assigned', 'task', tasks[22], '收到新任务', tasks[22].description, {task_id: tasks[22].id, customer_id: tasks[22].customer_id}),
      simple(53, 1, 1, 'task_completed', 'task', tasks[4], '任务已完成', tasks[4].description, {task_id: tasks[4].id, customer_id: tasks[4].customer_id}),
      simple(54, 0, 2, 'customer_assigned', 'customer', customerRows[5], '客户已下发', customerRows[5].name, {customer_id: customerRows[5].id, first_action: '本周内完成首访并确认试点边界'}),
      simple(55, 2, 2, 'customer_assigned', 'customer', customerRows[6], '客户已下发', customerRows[6].name, {customer_id: customerRows[6].id, first_action: '安排首访'}),
    ];
    // 风险：由 Agent 依据已确认事实识别，这里预置三条，两条待解除、一条已解除
    const riskRows = [
      {opportunity: opportunities[2], severity_code: 'high', title: '验收范围尚未书面确认', description: '试点已交付两周，客户仍未书面确认验收范围，存在延期确收的可能。', evidence: ['最近一次拜访记录：客户提出补充交付清单。', '预计关单日期在 30 天内，但未登记验收里程碑。'], next_action: '本周内约信息化部负责人确认验收清单和时间，并登记为拜访下一步。', opened: -6},
      {opportunity: opportunities[5], severity_code: 'medium', title: '两周无高层动作', description: '核心客户连续两周没有高层或技术层面的接触记录，关系深度可能回落。', evidence: ['近 14 天仅有 1 条线上沟通记录，对接人为业务部负责人。', '客户处于主攻区，按规则两周内需有高层或技术动作。'], next_action: '安排一次高层拜访或技术交流，并在拜访确认中登记对接人角色。', opened: -3},
      {opportunity: opportunities[8], severity_code: 'low', title: '预算口径待核实', description: '客户预算来自口头沟通，未见 IT 计划或采购文件，潜力评估可能偏高。', evidence: ['首访记录中的客户预算字段为口述金额。', '尚无预算文件或立项通知。'], next_action: '', opened: -20, resolved: -4, resolution_note: '客户已提供 2026 年 IT 采购计划，预算 180 万元与口述一致，潜力评估维持不变。'},
    ].map((r, i) => {const op = r.opportunity, owner = actors.find(a => a.user_id === op.owner_id); return {id: uid(14, i + 1), customer_id: op.customer_id, customer_name: op.customer_name, opportunity_id: op.id, opportunity_name: op.name,
      owner_id: op.owner_id, owner_name: op.owner_name, team_name: owner?.team_names?.[0] || '渠道销售-南区', title: r.title, description: r.description, evidence: r.evidence, next_action: r.next_action || undefined,
      severity_code: r.severity_code, risk_type_code: 'follow_up', status: r.resolved ? 'resolved' : 'open', opened_at: date(r.opened), resolved_at: r.resolved ? date(r.resolved) : null,
      resolved_by_name: r.resolved ? op.owner_name : null, resolution_note: r.resolved ? r.resolution_note : null, data_kind: 'demo'};});
    return {version: VERSION, customers: customerRows, opportunities, contacts, visits, tasks, actuals, notifications, claims: [], assignments: [], opportunityEvents: [], risks: riskRows, targets: {}, conversations: {}, runs: {}, advice: {}, idempotency: {}, serial: 100};
  }
  let state, baseline;
  async function loadLocal() {
    if (global.SALES_MODE !== 'preview') return null;
    if (localDataset) return localDataset;
    const response = await global.fetch('/local-preview-data', {cache: 'no-store', credentials: 'same-origin'});
    if (response.status === 404) return null;
    if (!response.ok) throw new Error('CRM 真实样本加载失败，请检查本地样本文件');
    const data = await response.json(), s = data.state;
    const full = data.scope === 'full';
    const customers = new Map((s?.customers || []).map(c => [c.id, c]));
    const opportunities = new Map((s?.opportunities || []).map(o => [o.id, o]));
    const validRows = rows => Array.isArray(rows) && rows.every(row => typeof row.id === 'string' && row.source_kind === 'crm_export' && row.source_ref?.row > 1) && new Set(rows.map(row => row.id)).size === rows.length;
    if (data.format !== 'sales-crm-preview-v1' || data.source_kind !== 'crm_export' || !/^[a-zA-Z0-9-]+$/.test(data.dataset_id || '') ||
        !s || !validRows(s.customers) || !validRows(s.opportunities) || !validRows(s.visits) || (!full && (s.opportunities.length < 3 || s.opportunities.length > 5)) ||
        (full && ['customers', 'opportunities', 'visits'].some(k => s[k].length !== data.counts?.[k])) ||
        !Array.isArray(data.actors) || !data.role_bindings ||
        !['contacts', 'tasks', 'actuals', 'notifications', 'claims', 'assignments', 'opportunityEvents', 'risks'].every(key => Array.isArray(s[key]) && s[key].length === 0) ||
        s.opportunities.some(op => !(full && op.customer_id == null) && !customers.has(op.customer_id)) ||
        s.visits.some(v => {
          if (!full) return !opportunities.has(v.opportunity_id) || opportunities.get(v.opportunity_id).customer_id !== v.customer_id;
          if (v.customer_id != null && !customers.has(v.customer_id)) return true;
          if (v.opportunity_id != null && (!opportunities.has(v.opportunity_id) || opportunities.get(v.opportunity_id).customer_id !== v.customer_id)) return true;
          return !Array.isArray(v.opportunity_ids) || v.opportunity_ids.some(id => !opportunities.has(id));
        })) throw new Error('CRM 样本格式或关联校验失败');
    localDataset = data; KEY = 'sales-web:crm-preview:' + data.dataset_id; LABEL = full ? 'CRM 全量数据 · 本地预览' : 'CRM 真实样本 · 本地预览'; state = undefined;
    baseline = full ? Object.fromEntries(Object.entries(s).filter(([,v]) => Array.isArray(v)).map(([k,rows]) => [k, new Map(rows.map(r => [r.id, JSON.stringify(r)]))])) : null;
    actors = ROLES.map(actorFor);
    const known = new Set(actors.map(actor => actor.user_id));
    for (const person of data.actors) if (!known.has(person.user_id)) actors.push({...actorFor(person.role), ...person});
    return localDataset;
  }
  function getState() {
    if (!state) {
      try {
        const raw = global.localStorage.getItem(KEY), saved = JSON.parse(raw || 'null');
        if (localDataset?.scope === 'full') {
          state = seed();
          if (saved?.version === VERSION && saved.format === 'crm-local-delta-v1') {
            for (const [key, value] of Object.entries(saved.values || {})) if (!Array.isArray(state[key])) state[key] = value;
            for (const [key, delta] of Object.entries(saved.arrays || {})) {
              if (!baseline[key]) continue;
              const removed = new Set(delta.removed), changed = new Map(delta.upserts.map(r => [r.id,r]));
              state[key] = state[key].filter(r => !removed.has(r.id)).map(r => {const row = changed.get(r.id) || r; changed.delete(r.id); return row;}).concat([...changed.values()]);
            }
          }
        }
        else if (saved && saved.version === VERSION) state = saved;
        else if (!localDataset && saved && [5, 6, 7].includes(saved.version)) {
          // v7：内置演示数据换了名字。重新生成内置数据，只保留使用者自己录入的记录，并把旧名字换成新名字。
          const fresh = seed(), legacy = JSON.parse(JSON.stringify(saved).replace(/【示例】/g, '').replace(/示例协作一组/g, '渠道销售-南区').replace(/示例销售主管/g, NAMES[1]).replace(/示例总经理/g, NAMES[2]).replace(/示例FDE主管/g, NAMES[4]).replace(/示例FDE/g, NAMES[3]).replace(/示例销售/g, NAMES[0]).replace(/示例联系人甲/g, '张晓静').replace(/示例联系人乙/g, '刘志强').replace(/示例协作伙伴/g, '华南数码渠道'));
          state = {...legacy, ...fresh, version: VERSION};
          for (const key of ['customers', 'opportunities', 'contacts', 'visits', 'tasks', 'actuals', 'notifications', 'claims', 'assignments', 'opportunityEvents', 'risks']) {
            // 内置行的 id 是固定的，新旧一致；不在内置行里的就是使用者自己录入的，保留
            const own = (Array.isArray(legacy[key]) ? legacy[key] : []).filter(row => row && !(fresh[key] || []).some(r => r.id === row.id));
            state[key] = [...(fresh[key] || []), ...own];
          }
          if (saved.version === 5) {
            state.migration_notice = '已保留旧版输入并升级字段；原数据备份在 sales-web:preview-backup:v5。既有客户归属未自动改变。';
            try {global.localStorage.setItem('sales-web:preview-backup:v5', raw);} catch (_) {state.migration_notice = '已在当前页面保留旧版输入；浏览器存储空间不足，未能写入备份，请导出数据后清理空间。';}
          }
          state.claims ||= []; state.assignments ||= []; state.opportunityEvents ||= [];
          for (const row of state.customers) {
            for (const [flat, canonical] of [['industry', 'industry_code'], ['customer_type', 'customer_type_code'], ['source', 'source_code'], ['partner_name', 'primary_partner_name']]) if (Object.hasOwn(row, flat)) row[canonical] = row[flat];
            if (row.contact_name || row.contact_title) {
              let contact = state.contacts.find(c => c.customer_id === row.id && c.is_primary);
              if (!contact) {contact = {id: uid(12, ++state.serial), customer_id: row.id, is_primary: true}; state.contacts.push(contact);}
              Object.assign(contact, {name: row.contact_name || '', title: row.contact_title || '', relationship_role_code: ({决策者: 'decision_maker', 影响者: 'influencer', 使用者: 'user'})[row.contact_role] || row.contact_role || 'user'});
            }
          }
          if (!state.customers.some(c => c.id === uid(10, 9))) state.customers.push(fresh.customers.find(c => c.id === uid(10, 9)));
          for (const row of state.tasks) if (row.target_position) {
            row.candidate_user_ids ||= actors.filter(a => a.role === row.target_position).map(a => a.user_id); row.declined_user_ids ||= [];
            if (row.status === 'pending_confirm') Object.assign(row, {assignees: [], assignee_name: '', owner_name: ''});
          }
          global.localStorage.setItem(KEY, JSON.stringify(state));
        }
      } catch (_) {}
      if (!state) state = seed();
    }
    return state;
  }
  function save() {
    if (localDataset?.scope !== 'full') {try {global.localStorage.setItem(KEY, JSON.stringify(getState()));} catch (_) {} return;}
    // The source snapshot is loaded from disk, never duplicated into browser storage.
    // Persist only local edits, including new/deleted rows, under this dataset's key.
    const arrays = {}, values = {};
    for (const [key, value] of Object.entries(getState())) {
      if (!Array.isArray(value)) {values[key] = value; continue;}
      const original = baseline[key] || new Map(), present = new Set(value.map(r => r.id));
      const upserts = value.filter(r => original.get(r.id) !== JSON.stringify(r));
      const removed = [...original.keys()].filter(id => !present.has(id));
      if (upserts.length || removed.length) arrays[key] = {upserts, removed};
    }
    try {global.localStorage.setItem(KEY, JSON.stringify({format:'crm-local-delta-v1', version:VERSION, arrays, values}));}
    catch (_) {error(507, '浏览器空间不足，本次操作仅留在当前页面，刷新前请释放浏览器存储空间并重试');}
  }
  function reset() { state = seed(); save(); return {demo: true, source_label: LABEL}; }
  function error(status, message) { throw Object.assign(new Error(message), {status}); }
  function requireCap(actor, cap) { if (!actor.capabilities[cap]) error(403, '当前身份未开放该操作'); }
  function session(options) {
    const header = options.header || {}, token = header.Authorization || header.authorization || '';
    const role = token.replace(/^Bearer preview-access-/, '');
    if (!ROLES.includes(role) || !String(token).startsWith('Bearer preview-access-')) error(401, '请先登录');
    return actorFor(role);
  }
  function auth(role) { return {access_token: 'preview-access-' + role, refresh_token: 'preview-refresh-' + role, token_type: 'bearer', expires_in: 86400, auth_method: 'password', must_change_password: false, actor: actorFor(role)}; }
  function pagination(items, params, extra = {}) {
    const offset = Math.max(0, Number(params.get('offset') || 0)), size = Math.max(1, Math.min(100, Number(params.get('page_size') || params.get('limit') || 20)));
    const page = items.slice(offset, offset + size), hasMore = offset + size < items.length;
    return {items: page, total: items.length, total_count: items.length, offset, page_size: size, has_more: hasMore, next_offset: hasMore ? offset + size : null, next_cursor: null, ...extra};
  }
  function directoryPeople(actor) {
    return actors.filter(person => person.user_id === actor.user_id || actor.capabilities['team.view'] &&
      (actor.role === 'manager' || isFde(person) === isFde(actor) && person.team_ids.some(id => actor.team_ids.includes(id))));
  }
  function scopeSelection(actor, params) {
    const teamId = params.get('team_id'), memberId = params.get('member_id'), memberIds = params.getAll('member_ids');
    const allowed = directoryPeople(actor);
    if (teamId && !directoryTeams(actor).some(team => team.id === teamId)) error(403, '无权查看该团队');
    if (teamId && !actor.capabilities['team.view']) error(403, '当前身份不能选择团队');
    const ids = memberId ? [memberId] : memberIds;
    if (ids.some(id => !allowed.some(person => person.user_id === id))) error(403, '无权查看该成员');
    if (teamId && ids.some(id => !allowed.find(person => person.user_id === id).team_ids.includes(teamId))) error(422, '所选成员不属于该团队');
    return {teamId, ids, people: allowed.filter(person => (!teamId || person.team_ids.includes(teamId)) && (!ids.length || ids.includes(person.user_id))), active: Boolean(teamId || ids.length)};
  }
  function visibleOpportunities(actor, params = new URLSearchParams()) {
    const selection = scopeSelection(actor, params);
    let rows = getState().opportunities;
    if (actor.role === 'sales') rows = rows.filter(o => o.owner_id === actor.user_id);
    else if (isFde(actor) && (actor.role === 'fde' || params.get('scope') === 'self')) rows = rows.filter(o => (o.fde_member_ids || []).includes(actor.user_id));
    const memberId = params.get('member_id');
    if (memberId) rows = rows.filter(o => isFde(actor) ? (o.fde_member_ids || []).includes(memberId) : o.owner_id === memberId);
    const memberIds = params.getAll('member_ids');
    if (memberIds.length) rows = rows.filter(o => isFde(actor) ? (o.fde_member_ids || []).some(id => memberIds.includes(id)) : memberIds.includes(o.owner_id));
    const teamGroups = params.getAll('team_groups');
    if (teamGroups.length) rows = rows.filter(o => teamGroups.includes('team:' + o.team_id));
    if (selection.teamId) rows = rows.filter(o => isFde(actor) ? (o.fde_member_ids || []).some(id => selection.people.some(person => person.user_id === id)) : o.team_id === selection.teamId);
    if (params.get('personal') === 'true') rows = rows.filter(o => o.owner_id === actor.user_id);
    if (!isFde(actor) && params.get('scope') === 'self') rows = rows.filter(o => o.owner_id === actor.user_id);
    if (!isFde(actor) && params.get('scope') === 'person' && params.get('account_code')) {const selected = actors.find(a => a.account_code === params.get('account_code')); rows = rows.filter(o => selected && o.owner_id === selected.user_id);}
    return rows;
  }
  function filteredOpportunities(actor, params) {
    let rows = visibleOpportunities(actor, params);
    const stages = params.getAll('stages'), quarters = params.getAll('quarters').map(Number), q = String(params.get('q') || '').toLowerCase();
    if (q) rows = rows.filter(o => (o.name + o.customer_name).toLowerCase().includes(q));
    for (const [param, field] of [['customer_id', 'customer_id'], ['product_line', 'product_line'], ['stage', 'stage_code'], ['owner', 'owner_name'], ['team', 'team_name']]) {
      const value = params.get(param); if (value && value !== 'all') rows = rows.filter(o => o[field] === value || (param === 'owner' && o.owner_id === value));
    }
    if (stages.length) rows = rows.filter(o => stages.includes(o.stage_code));
    if (params.get('include_closed') !== 'true' && !stages.some(s => ['won', 'lost'].includes(s)) && !params.get('stage')) rows = rows.filter(o => o.status === 'open');
    // The source page sends a year even when its quarter picker says 全部时间.
    // Both local datasets use no quarters to mean all time. Clearing a selected
    // future year must also restore older/undated records in the synthetic demo.
    if (params.get('year') && quarters.length) rows = rows.filter(o => Number(String(o.expected_close_date || '').slice(0, 4)) === Number(params.get('year')));
    if (quarters.length) rows = rows.filter(o => quarters.includes(Math.ceil(Number(String(o.expected_close_date || '').slice(5, 7)) / 3)));
    if (params.get('probability')) rows = rows.filter(o => Number(o.probability) >= Number(params.get('probability')));
    if (params.get('close_from')) rows = rows.filter(o => !!o.expected_close_date && o.expected_close_date >= params.get('close_from'));
    if (params.get('close_to')) rows = rows.filter(o => !!o.expected_close_date && o.expected_close_date <= params.get('close_to'));
    const closePeriod = params.get('close_period');
    if (closePeriod && closePeriod !== 'all') rows = rows.filter(o => closePeriod === 'year' ? String(o.expected_close_date || '').slice(0, 4) === today().slice(0, 4) : closePeriod === 'month' ? String(o.expected_close_date || '').slice(0, 7) === today().slice(0, 7) : String(o.expected_close_date || '').slice(0, 4) === today().slice(0, 4) && Math.ceil(Number(String(o.expected_close_date || '').slice(5, 7)) / 3) === Math.ceil(Number(today().slice(5, 7)) / 3));
    const grade = params.get('grade'); if (grade && grade !== 'all') rows = rows.filter(o => (o.amount >= 1000000 ? 'A' : o.amount >= 500000 ? 'B' : o.amount >= 100000 ? 'C' : 'D') === grade);
    return rows.map(o=>enrichOpportunity(o,actor));
  }
  function visibleCustomers(actor) {
    const visible = new Set(visibleOpportunities(actor).map(o => o.customer_id));
    return getState().customers.filter(c => actor.role === 'manager' || actor.role === 'supervisor' || c.owner_id === actor.user_id || visible.has(c.id));
  }
  function scopedCustomers(actor, params) {
    const selection = scopeSelection(actor, params);
    let rows = visibleCustomers(actor);
    if (isFde(actor) && selection.active) {
      const customerIds = new Set(visibleOpportunities(actor, params).map(op => op.customer_id));
      return rows.filter(row => customerIds.has(row.id));
    }
    if (selection.teamId) rows = rows.filter(row => row.owner_team_id === selection.teamId || selection.people.some(person => person.user_id === row.owner_id));
    if (selection.ids.length) rows = rows.filter(row => selection.ids.includes(row.owner_id) || (row.sales_members || []).some(person => selection.ids.includes(person.user_id || person.id)));
    if (!isFde(actor) && params.get('scope') === 'self') rows = rows.filter(row => row.owner_id === actor.user_id);
    return rows;
  }
  function customer(actor, id) { const row = visibleCustomers(actor).find(c => c.id === id); if (!row) error(404, '客户不存在或当前身份不可见'); return row; }
  function opportunity(actor, id) { const row = visibleOpportunities(actor).find(o => o.id === id); if (!row) error(404, '商机不存在或当前身份不可见'); return row; }
  function scenePermission(actor, op) { return isFde(actor) ? (op.fde_member_ids || []).includes(actor.user_id) : actor.capabilities['opportunity.edit'] === true; }
  function demoRows() { return getState().demoScenes || (getState().demoScenes = []); }
  function sceneView(row, actor) { const op = opportunity(actor, row.opportunity_id); return {...row, can_edit: row.creator_id === actor.user_id && scenePermission(actor, op)}; }
  function targetContext(actor, input) {
    const scope = input.scope || 'self', period = input.period_type || 'quarter', anchor = input.anchor_date;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(anchor || '') || !Number.isFinite(Date.parse(anchor)) || new Date(anchor).toISOString().slice(0, 10) !== anchor) error(422, '请指定有效目标日期');
    if (!['self', 'person', 'team', 'department'].includes(scope) || !['week', 'month', 'quarter', 'year'].includes(period)) error(501, '此目标范围或周期尚未提供');
    const selected = scope === 'person' ? actors.find(person => person.user_id === input.user_id) : actor;
    if (!selected || !directoryPeople(actor).some(person => person.user_id === selected.user_id)) error(403, '无权读取该人员目标');
    if (scope === 'department' && actor.role !== 'manager') error(403, '当前身份不能读取部门目标');
    if (scope === 'team' && (!actor.capabilities['team.view'] || !directoryTeams(actor).some(team => team.id === input.team_id))) error(403, '无权读取该团队目标');
    const day = new Date(anchor + 'T00:00:00Z');
    if (period === 'year') day.setUTCMonth(0, 1);
    else if (period === 'quarter') day.setUTCMonth(Math.floor(day.getUTCMonth() / 3) * 3, 1);
    else if (period === 'month') day.setUTCDate(1);
    else day.setUTCDate(day.getUTCDate() - (day.getUTCDay() + 6) % 7);
    const subject = scope === 'department' ? actor.workspace_id : scope === 'team' ? input.team_id : selected.user_id;
    const key = [scope === 'department' ? 'department' : scope === 'team' ? 'team' : 'person', subject, period, day.toISOString().slice(0, 10)].join(':');
    return {key, scope, period_type: period, anchor_date: day.toISOString().slice(0, 10), user_id: ['team','department'].includes(scope) ? null : selected.user_id, team_id: scope === 'team' ? input.team_id : null,
      editable: !['team','department'].includes(scope) && selected.user_id === actor.user_id};
  }
  function targetState(context) { const state = getState(); state.periodTargets ||= {}; return state.periodTargets[context.key] || (state.periodTargets[context.key] = {items: [], pending_batches: [], recent_batches: []}); }
  function dayKey(value) { return /^\d{4}-\d{2}-\d{2}$/.test(String(value || '')) ? value : Number.isFinite(Date.parse(value)) ? new Date(Date.parse(value) + 28800000).toISOString().slice(0, 10) : ''; }
  function inPeriod(value, params) {
    const day = dayKey(value); if (!day) return false;
    if (params.get('date_from') || params.get('date_to')) return !!params.get('date_from') && !!params.get('date_to') && day >= params.get('date_from') && day <= params.get('date_to');
    const year = Number(params.get('year') || yearNow()), quarters = params.getAll('quarters').map(Number);
    if (Number(day.slice(0, 4)) !== year || quarters.length && !quarters.includes(Math.ceil(Number(day.slice(5, 7)) / 3))) return false;
    const period = params.get('period');
    if (period === 'month') return day.slice(0, 7) === today().slice(0, 7);
    if (period === 'quarter') return Math.ceil(Number(day.slice(5, 7)) / 3) === Math.ceil(Number(today().slice(5, 7)) / 3);
    if (period === 'week') {const start = new Date(today() + 'T00:00:00Z'); start.setUTCDate(start.getUTCDate() - (start.getUTCDay() + 6) % 7); const end = new Date(start); end.setUTCDate(end.getUTCDate() + 6); return day >= start.toISOString().slice(0, 10) && day <= end.toISOString().slice(0, 10);}
    return true;
  }
  function fdeCompanyRankings(actor, params) {
    scopeSelection(actor, params);
    const state = getState(), personal = !!params.get('member_id') || params.get('scope') !== 'team';
    const selected = params.get('member_id') ? actors.find(person => person.user_id === params.get('member_id') && isFde(person)) : actor;
    if (!selected || !isFde(actor) || selected.user_id !== actor.user_id && !actor.capabilities['team.view']) error(403, '无权选择该 FDE 排名对象');
    const cohort = actors.filter(person => person.role === selected.role);
    const stats = people => {
      const ids = new Set(people.map(person => person.user_id));
      const ops = state.opportunities.filter(op => (op.fde_member_ids || []).some(id => ids.has(id))), opIds = new Set(ops.map(op => op.id));
      const visits = state.visits.filter(visit => ids.has(visit.recorder_id) && inPeriod(visit.visit_date || visit.interaction_at, params));
      return {followup_count: visits.length, opportunity_count: uniq(visits.map(visit => visit.opportunity_id)).length,
        demo_scene_count: demoRows().filter(scene => !scene.deleted_at && ids.has(scene.creator_id) && inPeriod(scene.created_at, params)).length,
        recognized_amount: sum(state.actuals.filter(entry => opIds.has(entry.opportunity_id) && entry.kind === 'recognized' && entry.status !== 'void' && inPeriod(entry.occurred_on, params)), 'amount')};
    };
    const items = personal ? cohort.map(person => ({user_id: person.user_id, name: person.display_name, role: person.role, team_name: person.team_names[0], ...stats([person])})) : directoryTeams(actor).map(team => ({user_id: team.id, name: team.name, role: 'fde_team', team_name: team.name, ...stats(actors.filter(person => isFde(person) && person.team_ids.includes(team.id)))}));
    return {contract_version: 2, data_source: 'database', scope: personal ? selected.role === 'fde_lead' ? 'all_fde_leads' : 'all_fde' : 'company_fde_teams', complete: true,
      year: Number(params.get('year') || yearNow()), quarters: params.getAll('quarters').map(Number), selection: {personal, member_id: personal ? selected.user_id : null, cohort_role: selected.role, team_ids: personal ? [] : [params.get('team_id') || uid(3, 1)]}, total: items.length, items};
  }
  function enrichOpportunity(o, actor) {
    const entries = getState().actuals.filter(e => e.opportunity_id === o.id && e.status !== 'void');
    const total = kind => entries.some(e => e.kind === kind) ? sum(entries.filter(e => e.kind === kind), 'amount') : null;
    return {...o, actuals: {recognized_amount: total('recognized'), collection_amount: total('collection')}, can_edit: actor?.permissions?.['opportunity.update']===true && (actor.role!=='sales'||o.owner_id===actor.user_id), can_manage_fde_members: actor?.permissions?.['opportunity.fde_members']===true};
  }
  function enrichCustomer(c, actor) {
    const ops = visibleOpportunities(actor).filter(o => o.customer_id === c.id && o.status === 'open');
    return {...c, opportunity_amount: sum(ops, 'amount'), acv_amount: sum(ops, 'amount'), opportunity_name: (ops[0] || {}).name || '', opportunity_stage: (ops[0] || {}).stage_code || '',
      plan_close_periods:ops.map(o=>({date:o.expected_close_date||null,year:o.expected_close_year||null,quarter:o.expected_close_quarter||null})),
      agent_plan: localDataset ? null : {year: yearNow(), segment: c.quadrant_code, source: 'agent'}, analysis_summary: localDataset ? 'CRM 原始资料，暂无评分' : '按客户资料与象限位置给出'};
  }
  // Match utils/taskOverview: Beijing-day grouping, missing dates last, stable
  // tie-breaks. Sort the entire authorized scope before applying pagination.
  function sortTaskRows(rows, order) {
    const key = ['today_first', 'due_desc', 'due_asc', 'created_desc'].includes(order) ? order : 'today_first';
    const stamp = value => {
      if (value == null || value === '') return null;
      const parsed = typeof value === 'number' || /^\d+$/.test(String(value)) ? Number(value) : Date.parse(value);
      return Number.isFinite(parsed) ? parsed : null;
    };
    const beijingDay = value => {const t = stamp(value); return t === null ? null : Math.floor((t + 28800000) / 86400000);};
    const currentDay = beijingDay(Date.now());
    const compare = (left, right, ascending = false) => {const a = stamp(left), b = stamp(right); return a === null || b === null ? a === b ? 0 : a === null ? 1 : -1 : ascending ? a - b : b - a;};
    return rows.slice().sort((a, b) => {
      if (key === 'today_first') {
        const aToday = beijingDay(a.due_at || a.dueAt) === currentDay, bToday = beijingDay(b.due_at || b.dueAt) === currentDay;
        if (aToday !== bToday) return aToday ? -1 : 1;
      }
      const primary = key === 'created_desc' ? compare(a.created_at, b.created_at) : compare(a.due_at || a.dueAt, b.due_at || b.dueAt, key === 'due_asc');
      return primary || compare(a.created_at, b.created_at) || String(a.id || '').localeCompare(String(b.id || ''));
    });
  }
  function taskRows(actor, params = new URLSearchParams()) {
    const selection = scopeSelection(actor, params);
    let rows = getState().tasks;
    if (actor.role === 'fde_lead') rows = rows.filter(t => t.assignees.some(person => actors.some(a => isFde(a) && a.user_id === person.user_id)) || t.target_position === 'fde' || t.creator_user_ref_id === actor.user_id);
    if (!['manager', 'supervisor', 'fde_lead'].includes(actor.role) || params.get('view') === 'self') rows = rows.filter(t => t.assignees.some(a => a.user_id === actor.user_id) || (t.candidate_user_ids || []).includes(actor.user_id) || t.creator_user_ref_id === actor.user_id);
    const memberId = params.get('member_id'); if (memberId) rows = rows.filter(t => t.assignees.some(a => a.user_id === memberId));
    if (selection.teamId || params.getAll('member_ids').length) {
      const ids = new Set(selection.people.map(person => person.user_id));
      rows = rows.filter(t => ids.has(t.creator_user_ref_id) || t.assignees.some(person => ids.has(person.user_id)) || (t.candidate_user_ids || []).some(id => ids.has(id)));
    }
    for (const key of ['customer_id', 'opportunity_id']) if (params.get(key)) rows = rows.filter(t => t[key] === params.get(key));
    return rows;
  }
  function taskResponse(row, actor) { return {...row, title: row.title || row.description, requires_action: row.status === 'pending_confirm' && (row.target_position ? (row.candidate_user_ids || []).includes(actor.user_id) && !(row.declined_user_ids || []).includes(actor.user_id) : row.assignees.some(p => p.user_id === actor.user_id && p.responsibility === 'owner')), can_coordinate: actor.capabilities['task.coordinate'] === true && !['completed', 'cancelled'].includes(row.status), action_permissions:Object.fromEntries(['task.accept','task.decline','task.complete','task.review','task.coordinate','task.cancel'].map(code=>[code,actor.permissions?.[code]===true]))}; }
  function customerDetail(actor, id, opId, mode = 'overview') {
    const c = customer(actor, id), ops = visibleOpportunities(actor).filter(o => o.customer_id === id && (!opId || o.id === opId)).map(o=>enrichOpportunity(o,actor));
    const contacts = getState().contacts.filter(c => c.customer_id === id), visits = getState().visits.filter(v => v.customer_id === id && (!opId || v.opportunity_id === opId));
    const tasks = taskRows(actor).filter(t => t.customer_id === id && (!opId || t.opportunity_id === opId)).map(t => taskResponse(t, actor));
    const raw = {...enrichCustomer(c, actor), read_model: mode === 'header' ? 'detail_header_v1' : 'detail_overview_v1', primary_contact: contacts.find(c => c.is_primary) || null, primary_opportunity: ops[0] || null, opportunities: opId ? ops : [], contacts: [], visits: [], tasks: [], risks: []};
    if (mode !== 'header') {
      raw.summary = {open_amount: sum(ops.filter(o => o.status === 'open'), 'amount'), opportunity_count: ops.length, contact_count: contacts.length, visit_count: visits.length, task_count: tasks.length,
        task_status_counts: Object.fromEntries(['pending_confirm', 'pending_execution', 'in_progress', 'pending_review', 'completed', 'cancelled'].map(status => [status, tasks.filter(t => t.status === status).length])), latest_visit: visits[0] || null, status_risk: null, open_risk: null};
      raw.profile = {dimensions: ['客户潜力', '关系深度', '商机成熟', '拜访活跃', '决策链', '风险健康'].map(label => ({label, value: null})), note: '待 Agent 复盘后生成'};
    }
    if (mode === 'detail') Object.assign(raw, {opportunities: ops, contacts, visits, tasks});
    return raw;
  }
  function entriesFor(actor, params) {
    const ids = new Set(scopedCustomers(actor, params).map(c => c.id));
    let rows = getState().actuals.filter(e => ids.has(e.customer_id) && e.status !== 'void');
    if (!isFde(actor) && params.get('scope') === 'self') rows = rows.filter(e => e.owner_id === actor.user_id);
    if (!isFde(actor) && params.get('scope') === 'person' && params.get('account_code')) {const selected = actors.find(a => a.account_code === params.get('account_code')); rows = rows.filter(e => selected && e.owner_id === selected.user_id);}
    for (const key of ['customer_id', 'opportunity_id', 'kind']) if (params.get(key)) rows = rows.filter(e => e[key] === params.get(key));
    const asOf = params.get('as_of') || today(); rows = rows.filter(e => e.occurred_on <= asOf);
    if (params.get('period') === 'year') rows = rows.filter(e => e.occurred_on.slice(0, 4) === asOf.slice(0, 4));
    return rows;
  }
  function actualSummary(rows) { const total = kind => localDataset && !rows.some(e => e.kind === kind) ? null : sum(rows.filter(e => e.kind === kind), 'amount'); return {recognized_amount: total('recognized'), collection_amount: total('collection'), entry_count: rows.length}; }
  function actualQuarters(rows) {
    const groups = {};
    for (const e of rows) { const year = Number(e.occurred_on.slice(0, 4)), quarter = Math.ceil(Number(e.occurred_on.slice(5, 7)) / 3), key = `${year}-${quarter}`;
      const row = groups[key] || (groups[key] = {id: key, year, quarter, entry_count: 0, recognized_count: 0, collection_count: 0, recognized_amount: null, collection_amount: null});
      row.entry_count++; row[e.kind + '_count']++; row[e.kind + '_amount'] = Number(row[e.kind + '_amount'] || 0) + Number(e.amount); }
    return Object.values(groups);
  }
  function growth(actor) {
    const dimensions = ['需求理解', '客户关系', '方案推进', '协作执行', '风险识别', '复盘成长'].map((name, i) => ({code: 'preview_' + i, key: 'preview_' + i, name, label: name, short_name: name}));
    const sampleCount = actor ? getState().visits.filter(v => v.recorder_id === actor.user_id).length : 0;
    return {data_source: 'database', today_status: 'not_reviewed', review_status: 'not_reviewed', as_of: now(), sample_count: sampleCount, framework: {dimensions},
      subject: actor ? {name: actor.display_name, team: actor.team_names[0], account_code: actor.account_code, role_code: actor.role} : null,
      latest: {summary: '画像待 Agent 复盘后生成。', score_summary: {status: 'missing', score: null, reason: '暂未评分'},
        dimension_scores: Object.fromEntries(dimensions.map(d => [d.code, {score: null, assessment: '暂未评分'}])),
        dimensions: dimensions.map(d => ({...d, score: null, assessment: '暂未评分', evidence_count: 0, numerator: null, denominator: null})), overall_score: null, advice: [],
        review_date: today(), improvements: ['补全业务记录后运行 Agent 复盘。'], input_snapshot: {visit_count: sampleCount}}, history: []};
  }
  function scopedDashboard(actor, params) {
    if (isFde(actor)) error(403, '请使用 FDE 看板');
    if (params.has('personal') && !['true', 'false'].includes(params.get('personal'))) error(422, '个人范围参数无效');
    const personal = params.has('personal') ? params.get('personal') === 'true' : ['self','person'].includes(params.get('scope')) || (!['team','department'].includes(params.get('scope')) && actor.role === 'sales');
    const selected = params.get('member_id') ? directoryPeople(actor).find(person => person.user_id === params.get('member_id')) : actor;
    scopeSelection(actor, params);
    const groupCodes = directoryTeams(actor).map(team => 'team:' + team.id), requestedGroups = params.get('team_id') ? ['team:' + params.get('team_id')] : params.getAll('team_groups');
    // The sole synthetic team is explicitly assigned to the first display group; the second has no members.
    const groupsFor = person => person && person.team_ids.includes(uid(3, 1)) ? ['team:' + uid(3, 1)] : [];
    const canView = person => person.user_id === actor.user_id || actor.capabilities['team.view'] === true &&
      (actor.role === 'manager' || person.team_ids.some(id => actor.team_ids.includes(id)));
    if (!selected || !canView(selected)) error(403, '无权选择该成员');
    if (!personal && actor.capabilities['team.view'] !== true) error(403, '无权查看团队看板');
    if (!personal && params.get('member_id') || personal && requestedGroups.length) error(422, '成员与团队范围不能混用');
    if (requestedGroups.length > 100 || requestedGroups.some(code => !groupCodes.includes(code)) || new Set(requestedGroups).size !== requestedGroups.length) error(422, '团队范围无效');
    const teamGroups = personal ? groupsFor(selected) : requestedGroups.length ? requestedGroups.slice().sort() : actor.role === 'manager' ? groupCodes.slice() : groupsFor(actor);
    const owners = new Set(actors.filter(person => !isFde(person) && canView(person) && (personal ? person.user_id === selected.user_id : groupsFor(person).some(code => teamGroups.includes(code)))).map(person => person.user_id));
    const state = getState(), ownerOf = row => row.owner_id || row.owner_user_ref_id;
    // The local manager's full-company snapshot includes rows with unresolved
    // accounts, just like the full CRM lists; person/team slices stay scoped.
    const allLocal = localDataset?.scope === 'full' && actor.role === 'manager' && !personal && teamGroups.length === 2;
    const ops = state.opportunities.filter(op => allLocal || owners.has(ownerOf(op))).map(o=>enrichOpportunity(o,actor)), customerIds = new Set(ops.map(op => op.customer_id));
    const entries = state.actuals.filter(entry => {
      const owner = ownerOf(entry) || ownerOf(state.opportunities.find(op => op.id === entry.opportunity_id) || {}) || ownerOf(state.customers.find(customer => customer.id === entry.customer_id) || {});
      return owners.has(owner) && entry.status !== 'void' && (entry.confirmed === true || entry.status === 'confirmed') && ['recognized', 'collection'].includes(entry.kind) && dayKey(entry.occurred_on) && dayKey(entry.occurred_on) <= today();
    });
    const customers = state.customers.filter(customer => allLocal || owners.has(ownerOf(customer)) || customerIds.has(customer.id)).map(customer => {
      const own = ops.filter(op => op.customer_id === customer.id && op.status === 'open');
      return {...customer, opportunity_amount: sum(own, 'amount'), acv_amount: sum(own, 'amount'), opportunity_name: (own[0] || {}).name || '', opportunity_stage: (own[0] || {}).stage_code || '',
        agent_plan: localDataset ? null : {year: yearNow(), segment: customer.quadrant_code, source: 'agent'}, analysis_summary: localDataset ? 'CRM 原始资料，暂无评分' : '按客户资料与象限位置给出'};
    });
    return {data_source: 'database', scope: personal ? selected.user_id === actor.user_id ? 'self' : 'member' : actor.role === 'manager' && teamGroups.length === 2 ? 'department' : 'team',
      selection: {personal, member_id: personal ? selected.user_id : null, cohort_role: personal ? selected.role : null, team_groups: teamGroups}, generated_at: now(),
      opportunities: ops.filter(o => o.status === 'open'), customers, quarter_forecasts: ops.filter(o => o.status === 'open').flatMap(o => (o.quarterly_forecasts || []).map(q => ({...q, opportunity_id: o.id, customer_id: o.customer_id, owner_id: o.owner_id,
        weighted_recognized_amount: q.recognized_amount == null ? null : Math.round(q.recognized_amount * Number(o.probability || 0) / 100), weighted_collection_amount: q.collection_amount == null ? null : Math.round(q.collection_amount * Number(o.probability || 0) / 100)}))),
      quarter_actuals: actualQuarters(entries), recent_visits: state.visits.filter(visit => (allLocal || owners.has(visit.recorder_id)) && ['archived', 'confirmed'].includes(visit.status)), summary: {source_date: now()}, targets: {recognized_amount: localDataset ? null : 5000000, collection_amount: localDataset ? null : 4000000}};
  }
  function rankings(actor, params) {
    const dash = scopedDashboard(actor, params), selection = dash.selection, year = Number(params.get('year')), quarters = params.getAll('quarters').map(Number);
    if (!Number.isInteger(year) || year < 2000 || year > 2100 || !quarters.length || quarters.length > 4 || quarters.some(quarter => !Number.isInteger(quarter) || quarter < 1 || quarter > 4) || new Set(quarters).size !== quarters.length) error(422, '请选择有效年份与季度');
    const people = actors.filter(person => !isFde(person)), byId = new Map(people.map(person => [person.user_id, person]));
    const cohort = people.filter(person => person.role === selection.cohort_role);
    const groupDefinitions = directoryTeams(actor).map(team => ({code: 'team:' + team.id, name: team.name}));
    const groupFor = id => byId.has(id) && byId.get(id).team_ids.includes(uid(3, 1)) ? 'team:' + uid(3, 1) : null;
    const state = getState(), ownerOf = row => row.owner_id || row.owner_user_ref_id;
    const ops = state.opportunities.filter(op => byId.has(ownerOf(op)) && op.status === 'open' &&
      Number(String(op.expected_close_date || '').slice(0, 4)) === year && quarters.includes(Math.ceil(Number(String(op.expected_close_date || '').slice(5, 7)) / 3)));
    const lastDay = today(), firstDay = new Date(Date.parse(lastDay + 'T00:00:00Z') - 6 * 86400000).toISOString().slice(0, 10);
    // Follow-up is the page's fixed seven calendar days, independently of the ACV quarter selection.
    const visits = state.visits.filter(visit => byId.has(visit.recorder_id) && ['archived', 'confirmed'].includes(visit.status) &&
      dayKey(visit.visit_date || visit.interaction_at) >= firstDay && dayKey(visit.visit_date || visit.interaction_at) <= lastDay);
    const ranked = rows => {
      rows.sort((a, b) => b.value - a.value || String(a.user_id || a.code).localeCompare(String(b.user_id || b.code)));
      rows.forEach((row, index) => {row.rank = index && row.value === rows[index - 1].value ? rows[index - 1].rank : index + 1; row.population = rows.length;});
      return rows;
    };
    const stats = (rows, value) => ({value: value(rows), record_count: rows.length, customer_count: uniq(rows.map(row => row.customer_id)).length});
    const groups = (rows, value, owner) => ranked(groupDefinitions.map(group => ({...group, team_name: group.name, team: group.name, ...stats(rows.filter(row => groupFor(owner(row)) === group.code), value)})));
    const bucket = (rows, value, owner) => {
      if (!selection.personal) {const result = groups(rows, value, owner); return {rows: result, groups: result.map(row => ({...row}))};}
      const result = ranked(cohort.map(person => ({user_id: person.user_id, code: person.account_code, name: person.display_name, role: person.role,
        team_name: person.team_names[0], team: person.team_names[0], ...stats(rows.filter(row => owner(row) === person.user_id), value)})));
      const cohortIds = new Set(cohort.map(person => person.user_id));
      return {rows: result, groups: groups(rows.filter(row => cohortIds.has(owner(row))), value, owner)};
    };
    const partnerOps = ops.filter(op => op.partner_id && (!selection.personal || cohort.some(person => person.user_id === ownerOf(op))));
    const partners = {rows: ranked([{code: 'preview-partner', name: '华南数码渠道', ...stats(partnerOps, rows => sum(rows, 'amount'))}]), groups: []};
    const regions = groups(ops, rows => sum(rows, 'amount'), ownerOf);
    const followup=bucket(visits,rows=>rows.length,visit=>visit.recorder_id);
    if(!selection.personal){
      const rows=groupDefinitions.map(group=>{const teamId=group.code.slice(5),members=people.filter(person=>person.team_ids.includes(teamId)).map(person=>{const own=visits.filter(v=>v.recorder_id===person.user_id);return {user_id:person.user_id,name:person.display_name,account_code:person.account_code,followup_count:own.length,customer_count:uniq(own.map(v=>v.customer_id)).length,current_member:true};});const own=visits.filter(v=>members.some(m=>m.user_id===v.recorder_id)),average=members.length?own.length/members.length:null;return {...group,team_name:group.name,team:group.name,member_count:members.length,members,average,value:average,record_count:own.length,customer_count:uniq(own.map(v=>v.customer_id)).length,rank:null};});
      followup.rows=[...ranked(rows.filter(r=>r.member_count>0)),...rows.filter(r=>!r.member_count)];followup.groups=copy(followup.rows);followup.calculation='team_followup_per_capita_v1';
    }
    return {contract_version: 2, data_source: 'database', scope: selection.personal ? 'peer' : 'company_teams', selection, complete: true, year, quarters: quarters.slice().sort((a, b) => a - b),
      opportunity_acv: bucket(ops, rows => sum(rows, 'amount'), ownerOf), active_opportunities: bucket(ops, rows => rows.length, ownerOf),
      followup, partner: partners, partners, partner_acv: partners, region: {rows: regions, groups: regions.map(row => ({...row}))}, generated_at: now()};
  }
  function dispatch(options) {
    if (global.SALES_MODE !== 'preview') error(403, '工作区尚未启用');
    const url = new URL(options.url, 'http://preview.invalid'), path = decodeURIComponent(url.pathname.replace(/^.*\/api\/v1(?=\/|$)/, '') || '/'), p = url.searchParams;
    const method = String(options.method || 'GET').toUpperCase(), body = options.data && typeof options.data === 'object' ? options.data : {};
    if (method === 'GET' && options.data) Object.entries(body).forEach(([key, value]) => p.set(key, String(value)));
    const s = getState();
    if (path === '/auth/password/login' && method === 'POST') { const role = String(body.account_code || '').replace(/^PREVIEW_/i, '').toLowerCase(); if (!ROLES.includes(role)) error(401, '账号或密码不正确'); return auth(role); }
    if (path === '/auth/refresh' && method === 'POST') {const token = String(body.refresh_token || ''), role = token.replace(/^preview-refresh-/, ''); if (!token.startsWith('preview-refresh-') || !ROLES.includes(role)) error(401, '会话已失效，请重新登录'); return auth(role);}
    const a = session(options);
    if (path === '/auth/me' && method === 'GET') return {actor: a};
    if (path === '/auth/logout' && method === 'POST') return {ok: true};
    if (path === '/auth/password' && method === 'POST') error(501, '当前环境不支持修改密码');
    if (method !== 'GET') {
      const idem = options.header && options.header['Idempotency-Key'];
      const key = idem && [a.user_id, method, path, idem].join(':');
      if (key && s.idempotency[key]) {if (s.idempotency[key].body !== JSON.stringify(body)) error(409, '提交标识已用于其他内容'); return s.idempotency[key].response;}
      const result = mutate(a, path, method, body, p);
      if (key) s.idempotency[key] = {body: JSON.stringify(body), response: copy(result)};
      save(); return result;
    }
    if (path === '/assistant/home') {
      const customerIds = new Set(visibleCustomers(a).map(c => c.id)), tasks = taskRows(a);
      const records = s.visits.filter(v => customerIds.has(v.customer_id)).slice(0, 3).map(v => localDataset ? {...v, archived_at: null, completed_count: ['customer_name', 'customer_type', 'opportunity_name', 'recorder_name', 'visit_date', 'created_date', 'contact_name_snapshot', 'follow_up_record', 'next_action'].filter(key => v[key] != null && v[key] !== '').length, total_count: 9, score: null, grade: '未评分'} : {...v, archived_at: v.created_at, interaction_mode: '线上会议', completed_count: 8, total_count: 8, score: null, grade: '暂未评分'});
      return {archived_visits: records, display_policy: {definition: {message_order: 'desc'}}, team_summary: {overdue: tasks.filter(t => !['completed', 'cancelled'].includes(t.status) && t.due_at < now()).length, claim: tasks.filter(t => t.status === 'pending_confirm').length, handover: tasks.filter(t => t.handover_required).length}, message: LABEL};
    }
    if (path === '/metadata/business-options') return copy(BUSINESS_OPTIONS);
    if (path === '/directory/teams') {if (p.get('purpose') && !['browse','dashboard','profile','fde','assignment'].includes(p.get('purpose'))) error(422, '团队目录用途无效'); return {data_source: 'database', teams: directoryTeams(a)};}
    if (path === '/directory/members' || path === '/directory/task-assignees' || path === '/directory/colleagues') return {items: directoryPeople(a).filter(person => path !== '/directory/colleagues' || !isFde(person)).map(member), teams: directoryTeams(a)};
    if (path === '/tasks/recipients') {requireCap(a, 'task.create'); const q = (p.get('q') || '').toLowerCase(); return pagination(actors.map(member).filter(person => (person.name + person.account_code).toLowerCase().includes(q)), p);}
    if (path === '/tasks/customers' || path === '/tasks/opportunities') {
      requireCap(a, 'task.create'); const q = (p.get('q') || '').toLowerCase();
      const ops = visibleOpportunities(a);
      if (path === '/tasks/customers') {const ids = new Set(ops.map(op => op.customer_id)); return pagination(visibleCustomers(a).filter(c => ids.has(c.id) && c.name.toLowerCase().includes(q)).map(c => ({id: c.id, name: c.name, customer_id: null, customer_name: null})), p);}
      if (!p.get('customer_id')) error(422, '任务商机选择必须指定客户');
      return pagination(ops.filter(op => op.customer_id === p.get('customer_id') && (!p.get('opportunity_id') || op.id === p.get('opportunity_id')) && op.name.toLowerCase().includes(q)).map(op => ({id: op.id, name: op.name, customer_id: op.customer_id, customer_name: op.customer_name})), p);
    }
    if (path === '/dashboard/options') return {members: directoryPeople(a).map(member), team_groups: a.capabilities['team.view'] ? directoryTeams(a).map(team => ({...team, code: 'team:' + team.id, kind: 'team'})) : [], teams: directoryTeams(a)};
    if (path === '/profile/scope-options' || path === '/fde/scope-options') {
      if (path.startsWith('/fde/') && !isFde(a)) error(403, '当前身份没有 FDE 范围');
      const people = directoryPeople(a);
      return {data_source: 'database', members: people.map(person => ({...member(person), team_ids: person.team_ids})), teams: a.capabilities['team.view'] ? directoryTeams(a) : [],
        allowed_scopes: a.role === 'manager' ? ['self', 'person', 'team', 'department'] : a.capabilities['team.view'] ? ['self', 'person', 'team'] : ['self'], defaults: {scope: a.role === 'manager' ? 'department' : 'self', member_id: a.user_id, team_id: null}};
    }
    if (path === '/directory/fde-members') return pagination(actors.filter(isFde).map(member), p);
    if (path === '/directory/task-positions') return {items: [{code: 'self', label: '本人', candidate_count: 1, available: true}, {code: 'fde', label: 'FDE 岗位', candidate_count: 1, available: true}]};
    if (path === '/directory/partners') return pagination((localDataset ? [] : [{id: uid(14, 1), name: '华南数码渠道', partner_name: '华南数码渠道', active: true}]).filter(r => r.name.includes(p.get('q') || '')), p);
    if(path==='/customers/claim-pool/options'){requireCap(a,'customer.claim');return {industries:[{value:'',label:'全部行业'},...uniq(s.customers.map(c=>c.industry_code)).map(value=>({value,label:value}))],claim_statuses:[{value:'',label:'全部状态'},{value:'unclaimed',label:'可申请认领'},{value:'pending',label:'申请待审批'},{value:'claimed',label:'已被认领'},{value:'rejected',label:'可重新申请'}]};}
    if(path==='/opportunities/create-options'){requireCap(a,'opportunity.edit');return {teams:directoryTeams(a),default_team_id:a.team_ids[0]};}
    if (path === '/customers' || path === '/customer-assets/map' || path === '/customers/claim-pool') {
      let rows = path === '/customers/claim-pool' ? s.customers : scopedCustomers(a, p);
      if (p.get('q')) rows = rows.filter(c => [c.name,c.name_pinyin,c.name_initials].filter(Boolean).some(value=>value.toLowerCase().includes(p.get('q').toLowerCase()))); if (p.get('unassigned') === 'true') rows = rows.filter(c => !c.owner_id); if (p.get('level')) rows = rows.filter(c => c.level_code === p.get('level'));
      rows = rows.map(c => {const claim = s.claims.find(claim => claim.customer_id === c.id && claim.applicant_user_id === a.user_id); return {...enrichCustomer(c, a), claim_status: claim ? claim.status : c.owner_id ? 'claimed' : 'unclaimed', claimed: c.owner_id === a.user_id, can_claim: !c.owner_id && c.ownership_state !== 'legacy_review' && (!claim || claim.status === 'rejected'), already_claimed: Boolean(c.owner_id)};});
      if(p.get('industry'))rows=rows.filter(c=>c.industry_code===p.get('industry'));
      if(p.get('claim_status'))rows=rows.filter(c=>c.claim_status===p.get('claim_status'));
      if(path==='/customer-assets/map'){
        const asOf=today(),d=new Date(asOf+'T00:00:00Z'),day=d.getUTCDate();d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()-6);d.setUTCDate(Math.min(day,new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)).getUTCDate()));
        const since=d.toISOString().slice(0,10),active=new Set(s.visits.filter(v=>['archived','confirmed'].includes(v.status)&&dayKey(v.visit_date||v.interaction_at)>=since&&dayKey(v.visit_date||v.interaction_at)<=asOf).map(v=>v.customer_id));
        rows=rows.filter(c=>active.has(c.id));
        return {items:rows,total:rows.length,summary:{customer_count:rows.length,acv_amount:sum(rows,'acv_amount')},activity_since:since,as_of:asOf};
      }
      return pagination(rows,p);
    }
    let match;
    if (path === '/targets') {const context = targetContext(a, Object.fromEntries(p)), values = targetState(context); return {...context, ...values, pending_requests: [], data_source: 'database'};}
    if ((match = path.match(/^\/opportunities\/([^/]+)\/demo-scenes$/))) {requireCap(a, 'opportunity.read'); const op = opportunity(a, match[1]); return pagination(demoRows().filter(row => row.opportunity_id === op.id && !row.deleted_at).map(row => sceneView(row, a)), p, {editable: scenePermission(a, op), can_create: scenePermission(a, op), data_source: 'database'});}
    if ((match = path.match(/^\/demo-scenes\/([^/]+)(?:\/(history))?$/))) {
      const row = demoRows().find(row => row.id === match[1] && !row.deleted_at); if (!row) error(404, '场景不存在'); requireCap(a, 'opportunity.read');
      const view = sceneView(row, a); return match[2] ? pagination(row.history || [], p) : view;
    }
    if ((match = path.match(/^\/customers\/([^/]+)\/opportunities\/check-name$/))) {customer(a, match[1]); const duplicate = s.opportunities.some(o => o.customer_id === match[1] && o.name === String(p.get('name') || '').trim() && o.id !== p.get('exclude_id')); return {available: !duplicate, duplicate, exists: duplicate, message: duplicate ? '当前客户下已存在同名商机' : '名称可用'};}
    if ((match = path.match(/^\/customers\/([^/]+)\/opportunities\/([^/]+)\/(header|overview)$/))) {const op = opportunity(a, match[2]); if (op.customer_id !== match[1]) error(404, '商机不属于该客户'); return customerDetail(a, match[1], match[2], match[3]);}
    if ((match = path.match(/^\/customers\/([^/]+)\/(header|overview|reference)$/))) return match[2] === 'reference' ? customer(a, match[1]) : customerDetail(a, match[1], null, match[2]);
    if ((match = path.match(/^\/customers\/([^/]+)\/(opportunities|contacts)$/))) {customer(a, match[1]); return pagination(match[2] === 'contacts' ? s.contacts.filter(c => c.customer_id === match[1]) : visibleOpportunities(a).filter(o => o.customer_id === match[1] && (!p.get('q') || o.name.includes(p.get('q')))).map(o=>enrichOpportunity(o,a)), p);}
    if ((match = path.match(/^\/customers\/([^/]+)$/))) return customerDetail(a, match[1], null, 'detail');
    if (path === '/opportunities') {const rows = filteredOpportunities(a, p); return pagination(rows, p, {summary: {total: rows.length, open_amount: sum(rows.filter(o => o.status === 'open'), 'amount')}, team_options: directoryTeams(a), facets: {teams: uniq(rows.map(o => o.team_name)), owners: uniq(rows.map(o => o.owner_name)), product_lines: uniq(rows.map(o => o.product_line))}});}
    if (path === '/opportunities/overview') {
      const all = visibleOpportunities(a, p), quarters = p.getAll('quarters').map(Number), year = Number(p.get('year') || yearNow());
      const selected = value => Boolean(value) && (!quarters.length || Number(String(value).slice(0, 4)) === year && quarters.includes(Math.ceil(Number(String(value).slice(5, 7)) / 3)));
      const unfilteredCrm = localDataset && !quarters.length;
      const rows = unfilteredCrm ? all : all.filter(o => selected(o.expected_close_date));
      const activeIds=new Set(s.visits.filter(v=>['archived','confirmed'].includes(v.status)&&selected(v.visit_date||v.interaction_at)).flatMap(v=>[v.opportunity_id,...(v.opportunity_ids||[]),...(v.linked_opportunities||[]).map(link=>link.id)]).filter(Boolean));
      const metrics = {total: all.length, active: all.filter(o=>o.status!=='lost'&&activeIds.has(o.id)).length,
        won: all.filter(o => o.status === 'won' && (unfilteredCrm || selected(o.won_at || o.actual_close_date))).length,
        newCount: all.filter(o => selected(o.created_at)).length, missingCloseDates: all.filter(o => !o.expected_close_date).length,
        missingWonDates: all.filter(o => o.status === 'won' && !o.won_at && !o.actual_close_date).length, missingCreatedDates: all.filter(o => !o.created_at).length};
      metrics.demo_scene_count = demoRows().filter(scene => !scene.deleted_at && rows.some(op => op.id === scene.opportunity_id)).length;
      if (localDataset) {
        const hasDate = value => /^\d{4}-\d{2}-\d{2}/.test(String(value || ''));
        const quarterOnly = all.filter(o => !hasDate(o.expected_close_date) && /^Q[1-4]$/.test(o.source_close_quarter || ''));
        metrics.crm_date_coverage = {
          source_count: all.length, active_count: all.filter(o => o.status === 'open').length,
          won_count: all.filter(o => o.status === 'won').length,
          close_dated: all.filter(o => hasDate(o.expected_close_date)).length,
          active_close_dated: all.filter(o => o.status === 'open' && hasDate(o.expected_close_date)).length,
          won_dated: all.filter(o => o.status === 'won' && hasDate(o.won_at || o.actual_close_date)).length,
          created_dated: all.filter(o => hasDate(o.created_at)).length,
          quarter_only: quarterOnly.length,
          quarters: Object.fromEntries([1,2,3,4].map(q => ['Q'+q, quarterOnly.filter(o => o.source_close_quarter === 'Q'+q).length]))
        };
      }
      return {metrics, total: all.length, summary: {total: all.length, open_amount: sum(rows.filter(o => o.status === 'open'), 'amount')}, product_lines: uniq(rows.map(o => o.product_line))};
    }
    if ((match = path.match(/^\/opportunities\/([^/]+)\/(header|overview|detail)$/))) {const op = opportunity(a, match[1]); return customerDetail(a, op.customer_id, op.id, match[2]);}
    if ((match = path.match(/^\/opportunities\/([^/]+)\/timeline$/))) {const op = opportunity(a, match[1]), events = s.opportunityEvents.filter(e => e.opportunity_id === op.id); return pagination(events.length ? events : [{id: uid(17, 1), key: uid(17, 1), at: op.created_at, type: 'created', title: '商机已创建', summary: '商机推进时间线起点', actor_name: op.owner_name, detail: '商机建档', description: '商机建档'}], p);}
    if (path === '/tasks/overview') {const rows = taskRows(a), ids = p.getAll('task_ids'); return {items: (ids.length ? rows.filter(t => ids.includes(t.id)) : rows.slice(0, 20)).map(t => taskResponse(t, a)), metrics: {today_completed: rows.filter(t => t.status === 'completed' && (t.completed_at || '').slice(0, 10) === today()).length, today_pending: rows.filter(t => !['completed', 'cancelled'].includes(t.status) && t.due_at.slice(0, 10) === today()).length, all_pending: rows.filter(t => !['completed', 'cancelled'].includes(t.status)).length}};}
    if (path === '/tasks') {
      const all = taskRows(a, p); let rows = all; const tab = p.get('tab'), status = p.get('status');
      if (tab === 'pending') rows = rows.filter(t => !['completed', 'cancelled'].includes(t.status)); if (tab === 'completed') rows = rows.filter(t => t.status === 'completed'); if (tab === 'rejected') rows = rows.filter(t => t.status === 'cancelled'); if (status) rows = rows.filter(t => t.status === status);
      const overview = p.get('overview'); if (overview === 'today_completed') rows = rows.filter(t => t.status === 'completed' && (t.completed_at || '').slice(0, 10) === today()); if (overview === 'today_pending') rows = rows.filter(t => !['completed', 'cancelled'].includes(t.status) && t.due_at.slice(0, 10) === today()); if (overview === 'all_pending') rows = rows.filter(t => !['completed', 'cancelled'].includes(t.status));
      if (p.get('opportunity_only') === 'true') rows = rows.filter(t => t.opportunity_id); if (p.get('completed_year')) rows = rows.filter(t => (t.completed_at || '').startsWith(p.get('completed_year')));
      const quarters = p.getAll('completed_quarters').map(Number); if (quarters.length) rows = rows.filter(t => quarters.includes(Math.ceil(Number((t.completed_at || '').slice(5, 7)) / 3)));
      rows = sortTaskRows(rows, p.get('order'));
      return pagination(rows.map(row => taskResponse(row, a)), p, {summary: {total: all.length, pending_count: all.filter(t => !['completed', 'cancelled'].includes(t.status)).length, completed_count: all.filter(t => t.status === 'completed').length, filtered_total: rows.length}});
    }
    if ((match = path.match(/^\/tasks\/([^/]+)$/))) {const row = taskRows(a).find(t => t.id === match[1]); if (!row) error(404, '任务不存在'); return taskResponse(row, a);}
    if (path === '/visits' || path === '/fde/activity') {const customerIds = new Set(visibleCustomers(a).map(c => c.id)); let rows = s.visits.filter(v => (localDataset && ['manager','supervisor'].includes(a.role)) || customerIds.has(v.customer_id)); for (const key of ['customer_id', 'opportunity_id']) if (p.get(key)) rows = rows.filter(v => v[key] === p.get(key) || (key === 'opportunity_id' && v.opportunity_ids?.includes(p.get(key)))); if (path === '/fde/activity') {if (!isFde(a)) error(403, '当前身份没有 FDE 活动范围'); const selection = scopeSelection(a, p); rows = rows.filter(v => selection.people.some(person => isFde(person) && person.user_id === v.recorder_id) && inPeriod(v.visit_date || v.interaction_at, p)); if (a.role === 'fde' || p.get('scope') === 'self') rows = rows.filter(v => v.recorder_id === a.user_id); if (p.get('member_id')) rows = rows.filter(v => v.recorder_id === p.get('member_id')); const selected = p.getAll('member_ids'); if (selected.length) rows = rows.filter(v => selected.includes(v.recorder_id));} if (p.get('sort') === 'created_desc') rows = rows.slice().sort((a,b) => Date.parse(b.created_at || 0) - Date.parse(a.created_at || 0) || String(b.id).localeCompare(String(a.id))); return pagination(rows, p, p.get('sort') === 'created_desc' ? {sort: 'created_desc'} : {});}
    if ((match = path.match(/^\/visits\/([^/]+)$/))) {const row = s.visits.find(v => v.id === match[1]); if (!row) error(404, '拜访不存在'); if (!(localDataset && ['manager','supervisor'].includes(a.role))) {const visible=visibleCustomers(a).some(c=>c.id===row.customer_id)||row.recorder_id===a.user_id||(row.linked_opportunities||[]).some(link=>visibleOpportunities(a).some(o=>o.id===link.id));if(!visible)error(403,'无权查看该跟进');} return row;}
    if (path === '/customer-assets/quarters') {const rows = entriesFor(a, p); return {items: actualQuarters(rows), years: uniq(rows.map(e => Number(e.occurred_on.slice(0, 4)))), as_of: p.get('as_of') || today()};}
    if (path === '/customer-assets') {const entries = p.get('basis')==='historical'?[]:entriesFor(a, p), view = p.get('customer_id') ? 'entries' : 'customers'; const rows = view === 'entries' ? entries.map(r=>({...r,can_void:a.permissions?.['actual.void']===true,tax_basis:r.tax_basis||'unknown'})) : scopedCustomers(a, p).map(c => ({customer_id: c.id, customer_name: c.name, owner_name: c.owner_name || actors.find(x => x.user_id === c.owner_user_ref_id)?.display_name || '', team_name: c.team_name || actors.find(x => x.user_id === c.owner_user_ref_id)?.team_names?.[0] || '', data_kind: 'demo', ...actualSummary(entries.filter(e => e.customer_id === c.id))})); return pagination(rows, p, {view, summary: {...actualSummary(entries),portfolio_customer_count:scopedCustomers(a,p).length,acv_amount:sum(scopedCustomers(a,p).map(c=>enrichCustomer(c,a)),'acv_amount'),unknown_acv_count:0}, basis:p.get('basis')==='historical'?'historical':'entries', historical_count:0, as_of: p.get('as_of') || today(), can_manage: p.get('basis')!=='historical'&&a.capabilities['actual.manage']});}
    if (path === '/dashboard') return scopedDashboard(a, p);
    if (path === '/dashboard/rankings') return rankings(a, p);
    if (path === '/profile/performance') {
      const year = Number(p.get('year') || yearNow()), quarter = Number(p.get('quarter') || Math.ceil(Number(today().slice(5, 7)) / 3));
      const context = targetContext(a, {scope: p.get('scope') || 'self', user_id: p.get('member_id') || a.user_id, team_id: p.get('team_id'), period_type: 'quarter', anchor_date: `${year}-${String((quarter - 1) * 3 + 1).padStart(2, '0')}-01`});
      const ops = visibleOpportunities(a, p), ids = new Set(ops.map(op => op.id)), actualRows = s.actuals.filter(entry => ids.has(entry.opportunity_id) && entry.status !== 'void' && Number(entry.occurred_on.slice(0,4)) === year && Math.ceil(Number(entry.occurred_on.slice(5,7))/3) === quarter), totals = actualSummary(actualRows);
      const targets = Object.fromEntries(['collection', 'recognized'].map(kind => {const row = targetState(context).items.find(row => row.kind === kind); return [kind, row ? row.amount : null];}));
      return {data_source: 'database', year, target_period: {year, quarter}, actuals: {...totals, recognized: totals.recognized_amount, collection: totals.collection_amount}, targets, supplementals: {followup: null, customers: null, opportunities: null}, retention: {rate: null}, scores: {}, active_opportunity_amount: sum(ops.filter(o => o.status === 'open'), 'amount'), won_amount: sum(ops.filter(o => o.status === 'won'), 'amount'), editable: !isFde(a) && context.editable};
    }
    if (path === '/profile/evaluation') {const dash = scopedDashboard(a, p); return {data_source: 'database', maturity: {active_opportunity_amount: sum(dash.opportunities.filter(o => o.status === 'open'), 'amount'), won_amount: sum(dash.opportunities.filter(o => o.status === 'won'), 'amount'), teams: [], members: []}, efficiency: {followup: {rows: [], periods: {}}, customers: {rows: [], periods: {}}, opportunities: {rows: [], periods: {}}}};}
    if (path === '/profile/sales-growth' || path === '/profile/sales-growth/scoped') return growth(a);
    if ((match = path.match(/^\/profile\/team-members\/([^/]+)\/sales-growth$/))) {const person = actors.find(x => x.account_code === decodeURIComponent(match[1])); if (!person) error(404, '成员不存在'); return growth(person);}
    if (path === '/fde/profile') {
      if (!isFde(a)) error(403, '当前身份没有 FDE 画像');
      const scope = p.get('scope') || 'self', selected = p.get('member_id') ? actors.find(person => person.user_id === p.get('member_id') && isFde(person)) : a;
      if (!selected || (scope === 'team' || selected.user_id !== a.user_id) && !a.capabilities['team.view']) error(403, '无权读取该画像范围');
      if (!['self', 'team'].includes(scope)) error(422, '画像范围无效');
      scopeSelection(a, p);
      const response = growth(scope === 'team' ? null : selected);
      const days = Number(p.get('days') || 30); if (!Number.isInteger(days) || days < 7 || days > 90) error(422, '画像观察窗口须为 7 至 90 天');
      response.sample_count = s.visits.filter(visit => (scope === 'team' ? actors.some(person => isFde(person) && person.user_id === visit.recorder_id && (!p.get('team_id') || person.team_ids.includes(p.get('team_id')))) : visit.recorder_id === selected.user_id) && Date.parse(visit.visit_date || visit.interaction_at) >= Date.now() - days * 86400000 && Date.parse(visit.visit_date || visit.interaction_at) <= Date.now()).length;
      response.latest.input_snapshot.visit_count = response.sample_count;
      return {...response, scope, member_id: scope === 'team' ? null : selected.user_id, member_name: scope === 'team' ? 'FDE 团队' : selected.display_name, can_review: false, overall_score: null};
    }
    if (path === '/fde/visit-opportunities') return pagination(visibleOpportunities(a).filter(o => (o.fde_member_ids || []).includes(a.user_id) && (!p.get('customer_id') || o.customer_id === p.get('customer_id')) && (!p.get('opportunity_id') || o.id === p.get('opportunity_id'))), p);
    if (path === '/fde/dashboard') {
      if (!isFde(a)) error(403, '当前身份没有 FDE 看板范围');
      const ops = visibleOpportunities(a, p), ids = new Set(ops.map(o => o.id)), tasks = taskRows(a, p);
      const visits = s.visits.filter(v => ids.has(v.opportunity_id) && actors.some(person => isFde(person) && person.user_id === v.recorder_id) && (p.get('scope') !== 'self' && a.role !== 'fde' || v.recorder_id === a.user_id) && (!p.get('member_id') || v.recorder_id === p.get('member_id')) && (!p.getAll('member_ids').length || p.getAll('member_ids').includes(v.recorder_id)) && inPeriod(v.visit_date || v.interaction_at, p));
      const entries = entriesFor(a, p).filter(e => ids.has(e.opportunity_id) && inPeriod(e.occurred_on, p));
      const stats = {opportunities: ops.length, open_opportunities: ops.filter(o => o.status === 'open').length, customers: uniq(ops.map(o => o.customer_id)).length, open_acv: sum(ops.filter(o => o.status === 'open'), 'amount'), visits: visits.length, period_visits: visits.length, active_recorders: uniq(visits.map(v => v.recorder_id)).length, period_customers: uniq(visits.map(v => v.customer_id)).length, completed_tasks: tasks.filter(t => t.status === 'completed').length, pending_tasks: tasks.filter(t => !['completed', 'cancelled'].includes(t.status)).length, overdue_tasks: tasks.filter(t => !['completed', 'cancelled'].includes(t.status) && t.due_at < now()).length, ...actualSummary(entries)};
      stats.period_opportunities = uniq(visits.map(v => v.opportunity_id)).length;
      stats.demo_scene_count = demoRows().filter(scene => ids.has(scene.opportunity_id) && !scene.deleted_at).length;
      const selectedPeople = actors.filter(person => (!p.get('team_id') || person.team_ids.includes(p.get('team_id'))) && isFde(person) && (p.get('scope') === 'team' && !p.get('member_id') && a.role === 'fde_lead' || person.user_id === (p.get('member_id') || a.user_id)));
      stats.own_demo_scene_count = demoRows().filter(scene => !scene.deleted_at && selectedPeople.some(person => person.user_id === scene.creator_id) && inPeriod(scene.created_at, p)).length;
      const distribution = actors.filter(person => (!p.get('team_id') || person.team_ids.includes(p.get('team_id'))) && isFde(person) && (a.role === 'fde_lead' || person.user_id === a.user_id)).map(person => {const own = visits.filter(visit => visit.recorder_id === person.user_id), projects = ops.filter(op => (op.fde_member_ids || []).includes(person.user_id)); return {...member(person), opportunities: projects.length, open_acv: sum(projects.filter(op => op.status === 'open'), 'amount'), period_visits: own.length, visits: own.length, period_opportunities: uniq(own.map(visit => visit.opportunity_id)).length};});
      return {data_source: 'database', summary: stats, company_rankings: fdeCompanyRankings(a, p), scope_label: p.get('scope') === 'team' ? 'FDE 团队' : '本人协作', members: actors.filter(person => isFde(person) && (a.capabilities['team.view'] || person.user_id === a.user_id)).map(member), ranking: distribution, recent_visits: visits.slice(0, 8), stages: STAGES.map((code, i) => ({code, label: STAGE_NAMES[i], name: STAGE_NAMES[i], count: ops.filter(o => o.stage_code === code).length, amount: sum(ops.filter(o => o.stage_code === code), 'amount')})), rhythm: [6, 5, 4, 3, 2, 1, 0].map(i => ({date: date(-i).slice(0, 10), visits: visits.filter(v => v.visit_date === date(-i).slice(0, 10)).length})), as_of: now()};
    }
    if (path === '/notifications') return {items: s.notifications.filter(n => n.recipient_user_ref_id === a.user_id || n.recipient_user_id === a.user_id)};
    if (path === '/risks') {const ids = new Set(visibleCustomers(a).map(c => c.id)); const items = s.risks.filter(r => ids.has(r.customer_id)); return {items, total: items.length};}
    if ((match = path.match(/^\/risks\/([^/]+)$/))) {const ids = new Set(visibleCustomers(a).map(c => c.id)); const row = s.risks.find(r => r.id === match[1] && ids.has(r.customer_id)); if (!row) error(404, '风险不存在或当前身份不可见'); return row;}
    if (path === '/workbench') return {items: [], customers: visibleCustomers(a).map(c => enrichCustomer(c, a)), tasks: taskRows(a), summary: {customer_count: visibleCustomers(a).length, task_count: taskRows(a).length}, source_label: LABEL};
    if ((match = path.match(/^\/agent\/runs\/([^/]+)$/))) {if (!s.runs[match[1]] || s.runs[match[1]].actor_id !== a.user_id) error(404, '运行记录不存在或当前身份不可见'); return s.runs[match[1]];}
    if (path === '/advice/statistics') return {items: [], total: 0};
    if ((match = path.match(/^\/advice\/([^/]+)$/))) {if (!s.advice[match[1]]) error(404, '建议不存在'); return s.advice[match[1]];}
    error(501, `尚未实现此功能（${method} ${path}）`);
  }
  function mutate(a, path, method, body, p) {
    const s = getState(), nextId = kind => uid(kind, ++s.serial); let match;
    if (path === '/targets' || path === '/targets/batch') {
      if (method !== 'POST') error(405, '目标仅支持 POST 保存');
      const context = targetContext(a, body); if (!context.editable) error(403, '只支持本人填写目标');
      if (!String(body.reason || '').trim() || body.reason.length > 2000) error(422, '请填写目标依据或调整原因');
      const items = path === '/targets' ? [{kind: body.kind, amount: body.amount, version_no: body.version_no ?? null}] : body.items;
      if (!Array.isArray(items) || !items.length || items.length > 6 || new Set(items.map(item => item.kind)).size !== items.length) error(422, '请填写不重复的目标指标');
      const current = targetState(context); if (current.pending_batches.length) error(409, '已有目标申请待运营审批，不能重复提交');
      for (const item of items) {
        const parts = String(item.amount).split('.'), cents = Number(parts[0] + (parts[1] || '').padEnd(2, '0'));
        if (!['recognized', 'collection', 'acv', 'opportunity_count', 'visit_count', 'demo_count'].includes(item.kind) || !/^\d+(?:\.\d{1,2})?$/.test(String(item.amount)) || !(Number(item.amount) > 0) || !Number.isSafeInteger(cents)) error(422, '请填写有效正数目标，金额最多两位小数');
        if (item.kind.endsWith('_count') && !Number.isInteger(Number(item.amount))) error(422, '数量目标必须为正整数');
        const old = current.items.find(row => row.kind === item.kind);
        if (old ? item.version_no !== old.version_no : item.version_no != null) error(409, '目标版本已变化，请刷新');
      }
      const changed = items.filter(item => {const old = current.items.find(row => row.kind === item.kind); return !old || Number(old.amount) !== Number(item.amount);});
      if (!changed.length) return {status: 'unchanged', items: current.items};
      if (items.some(item => current.items.some(row => row.kind === item.kind))) {
        const batch = {id: nextId(29), status: 'pending', reason: body.reason.trim(), items: items.map(item => ({kind: item.kind, proposed_amount: String(item.amount), version_no: item.version_no})), created_at: now(), applicant_id: a.user_id};
        current.pending_batches.push(batch); return batch;
      }
      for (const item of changed) current.items.push({kind: item.kind, amount: Number(item.amount), amount_text: String(item.amount), version_no: 1});
      return {status: 'saved', items: current.items};
    }
    if ((match = path.match(/^\/opportunities\/([^/]+)\/demo-scenes$/)) && method === 'POST') {
      requireCap(a, 'opportunity.read'); const op = opportunity(a, match[1]); if (!scenePermission(a, op)) error(403, '当前身份不能登记该商机的场景');
      if (!Array.isArray(body.scenes) || !body.scenes.length || body.scenes.length > 20 || body.scenes.some(row => !String(row.name || '').trim() || row.name.length > 200 || typeof row.description !== 'string' || row.description.length > 5000)) error(422, '每次登记 1 至 20 个场景，名称最多 200 字、说明最多 5000 字');
      const items = body.scenes.map(scene => ({id: nextId(28), opportunity_id: op.id, name: scene.name.trim(), description: scene.description, creator_id: a.user_id, creator_name: a.display_name, created_at: now(), updated_at: now(), version_no: 1, history: [{event_type: 'created', actor_name: a.display_name, created_at: now()}], data_kind: 'demo'}));
      demoRows().push(...items); return {items: items.map(row => sceneView(row, a))};
    }
    if ((match = path.match(/^\/demo-scenes\/([^/]+)$/)) && ['PATCH', 'DELETE'].includes(method)) {
      const row = demoRows().find(row => row.id === match[1] && !row.deleted_at); if (!row) error(404, '场景不存在');
      if (!sceneView(row, a).can_edit) error(403, '仅允许创建人在仍有商机权限时维护自己的场景');
      if (!Number.isInteger(body.version_no) || body.version_no !== row.version_no) error(409, '场景版本已变化，请刷新');
      if (method === 'PATCH' && (!String(body.name || '').trim() || body.name.length > 200 || typeof body.description !== 'string' || body.description.length > 5000)) error(422, '请填写有效场景名称和说明');
      row.history.push({event_type: method === 'DELETE' ? 'deleted' : 'updated', actor_name: a.display_name, created_at: now(), previous: {name: row.name, description: row.description, version_no: row.version_no}});
      Object.assign(row, method === 'DELETE' ? {deleted_at: now()} : {name: body.name.trim(), description: body.description}); row.version_no++; row.updated_at = now();
      return method === 'DELETE' ? {id: row.id, deleted: true, version_no: row.version_no} : sceneView(row, a);
    }
    if (global.SalesPreviewWorkflow) {
      const result = global.SalesPreviewWorkflow.handle({method, path, body, query: p, actor: a, state: s, now, nextId, requireCap, actors, error,
        customer: id => customer(a, id), opportunity: id => opportunity(a, id), saveOpportunity: (customerId, payload) => mutate(a, `/customers/${customerId}/opportunities`, 'POST', payload, p)});
      if (result && result.handled) return result.value;
    }
    const notify = (recipients, template, objectType, row, payload = {}) => {
      for (const recipient of uniq(recipients)) s.notifications.unshift({id: nextId(25), recipient_user_ref_id: recipient, template_code: template, object_type: objectType, object_id: row.id,
        title: `${({task_assigned: '收到新任务', task_accepted: '任务已接受', task_claimed: '岗位任务已领取', task_candidate_declined: '岗位候选人已拒绝', task_completed: '任务已完成', task_rejected: '任务已拒绝', task_reassigned: '任务已转交', task_cancelled: '任务已取消', customer_assigned: '客户已下发'})[template] || '业务更新'}`,
        body: row.description || row.name, payload: {task_id: objectType === 'task' ? row.id : undefined, customer_id: row.customer_id || (objectType === 'customer' ? row.id : undefined), ...payload}, created_at: now(), read_at: null, data_kind: 'demo'});
    };
    const assignOwner = (row, owner) => Object.assign(row, {owner_id: owner ? owner.user_id : null, owner_user_ref_id: owner ? owner.user_id : null, owner_name: owner ? owner.display_name : '',
      owner_team_id: owner ? owner.team_ids[0] : a.team_ids[0], team_name: owner ? owner.team_names[0] : a.team_names[0], ownership_state: owner ? 'assigned' : 'unassigned', sales_members: owner ? [member(owner)] : []});
    const customerFields = (row, fields) => {
      for (const [input, canonical] of [['industry', 'industry_code'], ['customer_type', 'customer_type_code'], ['level_code', 'level_code'], ['source', 'source_code'], ['partner_name', 'primary_partner_name']]) if (Object.hasOwn(fields, input)) row[canonical] = String(fields[input] || '').trim();
      if (['contact_name', 'contact_title', 'contact_role'].some(key => Object.hasOwn(fields, key))) {
        let contact = s.contacts.find(c => c.customer_id === row.id && c.is_primary);
        if (!contact) {contact = {id: nextId(12), customer_id: row.id, is_primary: true, data_kind: 'demo'}; s.contacts.push(contact);}
        if (Object.hasOwn(fields, 'contact_name')) contact.name = String(fields.contact_name || '').trim();
        if (Object.hasOwn(fields, 'contact_title')) contact.title = String(fields.contact_title || '').trim();
        if (Object.hasOwn(fields, 'contact_role')) contact.relationship_role_code = ({决策者: 'decision_maker', 影响者: 'influencer', 使用者: 'user', 采购者: 'buyer', 支持者: 'supporter'})[fields.contact_role] || fields.contact_role;
        row.primary_contact = {...contact};
      }
      row.updated_at = now();
    };
    if (path === '/customers' && method === 'POST') {
      requireCap(a, 'customer.create');
      for (const key of ['name', 'customer_type', 'level_code', 'source', 'target_team', 'contact_name', 'contact_title', 'contact_role']) if (!String(body[key] || '').trim()) error(422, '请补齐客户建档字段：' + key);
      const name = String(body.name).trim(); if (s.customers.some(c => c.name === name)) error(409, '客户名称已存在，请使用已有客户');
      const teams = directoryTeams(a), target = body.target_team_id ? teams.find(team => team.id === body.target_team_id) : teams.find(team => team.name === body.target_team);
      if (!target) error(403, '客户归属团队不在授权目录中');
      if (body.target_team && body.target_team !== target.name) error(422, '客户归属团队名称与ID不一致');
      const row = {id: nextId(10), name, target_team: target.name, target_team_id: target.id, workspace_id: a.workspace_id, potential_score: null, relationship_score: null, attributes: {}, version_no: 1, data_kind: 'demo', created_at: now()};
      assignOwner(row, a.role === 'sales' ? a : null); row.owner_team_id = target.id; row.team_name = target.name; customerFields(row, body); s.customers.unshift(row); return row;
    }
    if ((match = path.match(/^\/customers\/([^/]+)$/)) && method === 'PATCH') {
      requireCap(a, 'customer.edit'); const row = customer(a, match[1]);
      for (const key of ['industry', 'customer_type', 'level_code', 'source', 'partner_name']) if (Object.hasOwn(body, key) && !String(body[key] || '').trim()) error(422, '客户维护字段不能为空：' + key);
      customerFields(row, body); row.version_no = (row.version_no || 1) + 1; return {...row, message: '资料已保存'};
    }
    if ((match = path.match(/^\/customers\/([^/]+)\/claims$/)) && method === 'POST') {
      requireCap(a, 'customer.claim'); const row = s.customers.find(c => c.id === match[1]); if (!row) error(404, '客户不存在');
      if (row.owner_id || row.ownership_state === 'legacy_review') error(409, '该客户已有负责人或待运营核对，不能申请认领');
      const previous = s.claims.find(c => c.customer_id === row.id && c.applicant_user_id === a.user_id);
      if (previous && previous.status === 'pending') return previous;
      const claim = {id: nextId(26), customer_id: row.id, applicant_user_id: a.user_id, applicant_name: a.display_name, status: 'pending', created_at: now(), data_kind: 'demo', message: '申请已记录，须由运营审批'};
      s.claims.unshift(claim); return claim;
    }
    if ((match = path.match(/^\/customers\/([^/]+)\/assignments$/)) && method === 'POST') {
      requireCap(a, 'customer.create'); if (!['manager', 'supervisor'].includes(a.role)) error(403, '只有管理角色可以下发客户');
      const row = s.customers.find(c => c.id === match[1]), owner = actors.find(m => m.account_code === body.assignee_account_code && m.role === 'sales');
      if (!row) error(404, '客户不存在'); if (!owner || !String(body.first_action || '').trim()) error(422, '请选择有效销售并填写首步行动');
      if (row.owner_id) error(409, '该客户已分配，请刷新客户池');
      assignOwner(row, owner); row.version_no++; row.updated_at = now(); row.attributes = {...row.attributes, first_action: String(body.first_action).trim()};
      const assignment = {id: nextId(27), customer_id: row.id, assignee_user_id: owner.user_id, assignee_account_code: owner.account_code, assigned_by: a.user_id, first_action: row.attributes.first_action, status: 'assigned', created_at: now(), data_kind: 'demo'};
      s.assignments.unshift(assignment); notify([owner.user_id], 'customer_assigned', 'customer', row, {first_action: assignment.first_action, customer_name: row.name, actor_name: a.display_name}); return assignment;
    }
    if ((match = path.match(/^\/customers\/([^/]+)\/opportunities$/)) && method === 'POST') {
      requireCap(a, 'opportunity.edit'); const c = customer(a, match[1]); if (!String(body.name || '').trim() || !Number.isFinite(Number(body.amount)) || Number(body.amount) <= 0) error(422, '请填写商机名称及大于零的有效金额');
      let row = body.opportunity_id ? opportunity(a, body.opportunity_id) : null;
      if (row && row.customer_id !== c.id) error(409, '商机不属于当前客户，不能转移归属');
      if (row && Number(body.version_no) !== row.version_no) error(409, '商机版本已变化，请刷新后重试');
      if (s.opportunities.some(o => o.customer_id === c.id && o.name === String(body.name).trim() && (!row || o.id !== row.id))) error(409, '当前客户下已存在同名商机');
      const stageIndex = body.status === 'lost' ? 6 : PROBABILITY.indexOf(Number(body.probability)), stage = STAGES[stageIndex];
      if (!stage || !['open', 'won', 'lost'].includes(body.status) || (body.status === 'won') !== (stage === 'won') || !/^\d{4}-\d{2}-\d{2}$/.test(body.expected_close_date || '') || !Number.isFinite(Date.parse(body.expected_close_date))) error(422, '请选择有效商机阶段及预计关单日期');
      if (body.status !== 'open' && (!row || row.status !== body.status) && body.closure_confirmed !== true) error(422, '请确认关闭商机');
      if (row && row.status !== 'open' && body.status === 'open' && body.reopen_confirmed !== true) error(422, '请确认重新打开商机');
      if (!['direct', 'partner'].includes(body.sales_channel) || body.sales_channel === 'partner' && body.partner_id !== uid(14, 1)) error(422, '请选择有效销售渠道及已有伙伴');
      const quarters = body.quarterly_forecasts || [];
      if (!Array.isArray(quarters) || quarters.some(q => !Number.isInteger(q.year) || ![1, 2, 3, 4].includes(q.quarter) || ['recognized_amount', 'collection_amount'].some(key => q[key] != null && (!Number.isFinite(Number(q[key])) || Number(q[key]) < 0))) || new Set(quarters.map(q => `${q.year}-${q.quarter}`)).size !== quarters.length) error(422, '季度预测包含无效金额或重复季度');
      const started = quarters.filter(q => q.recognized_amount != null || q.collection_amount != null);
      if (Number(body.probability) >= 30 && (!started.length || started.some(q => q.recognized_amount == null || q.collection_amount == null))) error(422, '30%及以上阶段须至少填写一组完整的季度确收和回款，金额为零请填 0');
      const fdeIds = uniq(body.fde_member_ids || row && row.fde_member_ids || []);
      if (fdeIds.some(id => !actors.some(m => isFde(m) && m.user_id === id))) error(422, '请选择有效 FDE 成员');
      const values = {name: String(body.name).trim(), amount: Number(body.amount), expected_close_date: body.expected_close_date, probability: body.status === 'lost' ? null : Number(body.probability),
        stage_code: stage, status: body.status, customer_id: c.id, customer_name: c.name, product_line: String(body.product_line || '').trim(), sales_channel: body.sales_channel, partner_id: body.sales_channel === 'partner' ? body.partner_id : null,
        owner_id: row ? row.owner_id : c.owner_id || a.user_id, owner_user_ref_id: row ? row.owner_id : c.owner_id || a.user_id, owner_name: row ? row.owner_name : c.owner_name || a.display_name, team_name: c.team_name, team_id: c.owner_team_id, partner_name: body.sales_channel === 'partner' ? '华南数码渠道' : '直销',
        fde_member_ids: fdeIds, quarterly_forecasts: copy(quarters), data_kind: 'demo'};
      values.fde_members = actors.filter(m => values.fde_member_ids.includes(m.user_id)).map(member);
      const changed = !row || ['name', 'amount', 'expected_close_date', 'probability', 'status', 'product_line', 'sales_channel', 'partner_id', 'fde_member_ids', 'quarterly_forecasts'].some(key => JSON.stringify(row[key]) !== JSON.stringify(values[key]));
      if (!changed) return {...enrichOpportunity(row,a), opportunity_id: row.id, changed: false, version_no: row.version_no};
      const event = {id: nextId(17), type: !row ? 'created' : row.status !== values.status ? values.status === 'open' ? 'reopened' : 'closed' : 'updated', actor_name: a.display_name, at: now(), before: row ? copy(row) : null};
      values.won_at = values.status === 'won' ? row && row.status === 'won' && row.won_at || now() : null;
      values.actual_close_date = values.status === 'open' ? null : today(); values.updated_at = now();
      if (row) Object.assign(row, values, {version_no: row.version_no + 1}); else {row = {...values, id: nextId(11), version_no: 1, created_at: now()}; s.opportunities.unshift(row);}
      s.opportunityEvents.unshift({...event, key: event.id, opportunity_id: row.id, title: `商机${({created: '已创建', reopened: '已重新打开', closed: '已关闭', updated: '已更新'})[event.type]}`, summary: row.name, description: `保存后的阶段：${STAGE_NAMES[stageIndex]}`, after: copy(row), data_kind: 'demo'});
      return {...enrichOpportunity(row,a), opportunity_id: row.id, changed: true, event_id: event.id, version_no: row.version_no};
    }
    if ((match = path.match(/^\/opportunities\/([^/]+)\/fde-members$/)) && method === 'PUT') {requireCap(a, 'fde.members.manage'); const row = opportunity(a, match[1]); if (Number(body.version_no) !== row.version_no) error(409, '商机版本已变化'); row.fde_member_ids = uniq(body.member_ids || []); row.fde_members = actors.filter(m => row.fde_member_ids.includes(m.user_id)).map(member); row.version_no++; return {...row, changed: true, event_id: nextId(17)};}
    if(path==='/tasks/batch'&&method==='POST'){
      const tasks=body.tasks;if(!Array.isArray(tasks)||tasks.length<1||tasks.length>100)error(422,'每次选择 1 至 100 位接收人');
      if(new Set(tasks.map(t=>t.assignee_account_code||t.target_position)).size!==tasks.length)error(422,'接收人不能重复');
      const before=copy(s);try{const items=tasks.map(task=>mutate(a,'/tasks','POST',task,p));return {items,total:items.length};}catch(failure){for(const key of Object.keys(s))delete s[key];Object.assign(s,before);throw failure;}
    }
    if (path === '/tasks' && method === 'POST') {
      requireCap(a, 'task.create'); if (String(body.description || '').trim().length < 5 || !Number.isFinite(Date.parse(body.due_at)) || Date.parse(body.due_at) <= Date.now()) error(422, '请填写至少五字的任务内容和未来截止时间');
      if (Boolean(body.assignee_account_code) === Boolean(body.target_position)) error(422, '请指定一位同事或一个有效岗位');
      const owner = body.assignee_account_code ? actors.find(m => m.account_code === body.assignee_account_code) : null;
      const candidates = body.target_position === 'self' ? [a] : body.target_position && ['supervisor', 'manager', 'operations', 'fde', 'fde_lead'].includes(body.target_position) ? actors.filter(m => m.role === body.target_position) : [];
      if (!owner && !candidates.length) error(422, '请选择有效任务负责人或岗位');
      const kind = body.association_kind || (body.customer_id || body.opportunity_id ? 'customer' : 'daily');
      if (!['customer', 'daily'].includes(kind) || kind === 'customer' && (!body.customer_id || !body.opportunity_id) || kind === 'daily' && (body.customer_id || body.opportunity_id)) error(422, '客户任务必须同时关联已有客户和商机；日常任务不能包含客户关联');
      const c = body.customer_id ? customer(a, body.customer_id) : null, op = body.opportunity_id ? opportunity(a, body.opportunity_id) : null;
      if (op && c && op.customer_id !== c.id) error(422, '任务的商机必须属于所选客户');
      const row = {...body, id: nextId(15), status: 'pending_confirm', version_no: 1, created_at: now(), creator_user_ref_id: a.user_id, creator_name: a.display_name,
        title: String(body.description).trim(), description: String(body.description).trim(), priority_code: ['normal', 'medium', 'high'].includes(body.priority_code) ? body.priority_code : 'normal',
        assignees: owner ? [{user_id: owner.user_id, name: owner.display_name, responsibility: 'owner'}] : [], assignee_name: owner ? owner.display_name : '', owner_name: owner ? owner.display_name : '',
        candidate_user_ids: candidates.map(a => a.user_id), declined_user_ids: [], customer_id: c ? c.id : op ? op.customer_id : null, customer_name: c ? c.name : op ? op.customer_name : null,
        opportunity_id: op ? op.id : null, opportunity_name: op && op.name, association_kind: kind, team_name: a.team_names[0], requires_action: true, data_kind: 'demo', events: [], task_type: 'management'};
      s.tasks.unshift(row); notify(owner ? [owner.user_id] : row.candidate_user_ids, 'task_assigned', 'task', row, {creator_name: a.display_name, due_at: row.due_at, priority_code: row.priority_code, target_position: row.target_position}); return taskResponse(row, a);
    }
    if ((match = path.match(/^\/tasks\/([^/]+)\/events$/)) && method === 'POST') {
      const row = taskRows(a).find(t => t.id === match[1]); if (!row) error(404, '任务不存在');
      if (Number(body.version_no) !== row.version_no) error(409, '任务版本已变化，请刷新后重试');
      const event = body.event_type, review = ['approve_completion', 'reject_completion'].includes(event), coordination = ['reassign', 'cancel'].includes(event); if (!review) requireCap(a, coordination ? 'task.coordinate' : 'task.respond');
      const isOwner = row.assignees.some(m => m.user_id === a.user_id && m.responsibility === 'owner');
      const candidate = row.target_position && row.status === 'pending_confirm' && (row.candidate_user_ids || []).includes(a.user_id) && !(row.declined_user_ids || []).includes(a.user_id);
      if (review && (row.creator_user_ref_id !== a.user_id || row.status !== 'pending_review')) error(403, '只有发起人可确认待验收任务');
      if (!coordination && !review && !isOwner && !candidate) error(403, '只有有效候选人或任务负责人可以操作');
      if (coordination && ['completed', 'cancelled'].includes(row.status)) error(409, '已结束的任务不能协调');
      if (row.handover_required && !coordination) error(409, '任务须先由负责人完成交接');
      if (['accept', 'reject'].includes(event) && row.status !== 'pending_confirm') error(409, '任务已处理');
      if (event === 'complete' && (!isOwner || !['pending_execution', 'in_progress'].includes(row.status))) error(409, '请先由本人接受任务');
      if ((['reject', 'complete', 'reject_completion'].includes(event) || coordination) && !String(body.note || '').trim()) error(422, '请填写拒绝或协调原因');
      const previousOwners = row.assignees.filter(p => p.responsibility === 'owner').map(p => p.user_id);
      let template = 'task_' + ({accept: 'accepted', reject: 'rejected', complete: 'completed', approve_completion: 'completed', reject_completion: 'completion_rejected', cancel: 'cancelled', reassign: 'reassigned'})[event];
      if (event === 'accept') {
        if (candidate) {row.assignees = [{user_id: a.user_id, name: a.display_name, responsibility: 'owner'}]; row.assignee_name = a.display_name; row.owner_name = a.display_name; template = 'task_claimed';}
        row.status = 'pending_execution';
      } else if (event === 'reject') {
        if (candidate) {row.declined_user_ids = uniq([...(row.declined_user_ids || []), a.user_id]); row.status = row.candidate_user_ids.every(id => row.declined_user_ids.includes(id)) ? 'cancelled' : 'pending_confirm'; template = 'task_candidate_declined';}
        else row.status = 'cancelled';
      } else if (event === 'cancel') row.status = 'cancelled';
      else if (event === 'complete') {
        const selfAssigned = row.creator_user_ref_id === a.user_id;
        row.status = selfAssigned ? 'completed' : 'pending_review'; row.completed_at = selfAssigned ? now() : null;
        row.completion_note = body.note.trim(); row.completed_by_name = a.display_name;
        row.completion_submitted_at = now(); row.completion_review_note = null;
        template = selfAssigned ? 'task_completed' : 'task_completion_submitted';
      } else if (event === 'approve_completion') {row.status = 'completed'; row.completed_at = now(); row.completion_review_note = body.note || '';}
      else if (event === 'reject_completion') {row.status = 'in_progress'; row.completed_at = null; row.completion_review_note = body.note.trim();}
      else if (event === 'reassign') {
        const owner = actors.find(m => m.account_code === body.assignee_account_code); if (!owner) error(422, '请选择有效负责人');
        row.assignees = [{user_id: owner.user_id, name: owner.display_name, responsibility: 'owner'}]; row.assignee_name = owner.display_name; row.owner_name = owner.display_name; row.assignee_account_code = owner.account_code;
        row.target_position = null; row.candidate_user_ids = []; row.declined_user_ids = []; row.status = 'pending_confirm'; row.handover_required = false;
      } else error(501, '此任务操作暂未实现');
      const recordedEvent = event === 'complete' && row.status === 'pending_review' ? 'submit_completion' : event;
      row.last_event_type = recordedEvent; row.last_event_note = body.note || ''; row.version_no++; row.requires_action = row.status === 'pending_confirm';
      row.events.push({id: nextId(17), event_type: recordedEvent, note: body.note || '', occurred_at: now(), created_at: now(), actor_user_id: a.user_id, actor_name: a.display_name, previous_owner_ids: previousOwners, status: row.status});
      notify([row.creator_user_ref_id, ...previousOwners, ...row.assignees.map(p => p.user_id), ...(row.candidate_user_ids || [])].filter(id => id !== a.user_id), template, 'task', row,
        {actor_name: a.display_name, creator_name: row.creator_name, owner_name: row.owner_name, previous_owner_names: actors.filter(a => previousOwners.includes(a.user_id)).map(a => a.display_name), note: body.note || '', comment: body.note || '', completion_note: row.completion_note, status: row.status});
      return taskResponse(row, a);
    }
    if (path === '/visits' && method === 'POST') error(501, '拜访校验模块未加载，不能跳过质检归档');
    if (path === '/customer-assets' && method === 'POST') {requireCap(a, 'actual.manage'); const c = customer(a, body.customer_id); if (!['recognized', 'collection'].includes(body.kind) || !Number.isFinite(Number(body.amount)) || Number(body.amount) < 0 || body.confirmed !== true) error(422, '请确认有效实绩'); const row = {...body, id: nextId(16), customer_name: c.name, owner_id: c.owner_id || a.user_id, amount: Number(body.amount), status: 'confirmed', created_at: now(), created_by_name: a.display_name, data_kind: 'demo'}; s.actuals.unshift(row); return row;}
    if ((match = path.match(/^\/customer-assets\/([^/]+)\/void$/)) && method === 'POST') {requireCap(a, 'actual.manage'); const row = s.actuals.find(e => e.id === match[1]); if (!row) error(404, '记录不存在'); customer(a, row.customer_id); row.status = 'void'; row.void_reason = body.reason; return row;}
    if (path === '/profile/sales-targets' && method === 'POST') {if (isFde(a)) error(403, 'FDE 不能维护销售目标'); if (!['recognized', 'collection'].includes(body.kind) || !Number.isFinite(Number(body.amount))) error(422, '请输入有效目标'); s.targets[a.user_id] = {...(s.targets[a.user_id] || {recognized: localDataset ? null : 5000000, collection: localDataset ? null : 4000000}), [body.kind]: Number(body.amount)}; return {saved: true, ...s.targets[a.user_id]};}
    if ((path === '/profile/sales-growth/review' || path === '/fde/profile/review') && method === 'POST') error(501, 'Agent 暂未接入，不能生成能力评分');
    if ((match = path.match(/^\/risks\/([^/]+)\/resolve$/)) && method === 'POST') {const row = s.risks.find(r => r.id === match[1]); if (!row) error(404, '风险不存在'); if (!a.capabilities['risk.resolve']) error(403, '当前身份不能解除风险'); Object.assign(row, {status: 'resolved', resolved_at: now(), resolved_by_name: a.display_name, resolution_note: body.resolution_note || ''}); return row;}
    if ((match = path.match(/^\/notifications\/([^/]+)\/read$/)) && method === 'POST') {const row = s.notifications.find(n => n.id === match[1] && (n.recipient_user_ref_id === a.user_id || n.recipient_user_id === a.user_id)); if (!row) error(404, '通知不存在'); row.read_at ||= now(); return {id: row.id, read: true, read_at: row.read_at};}
    if (path === '/conversations' && method === 'POST') {const id = nextId(20); s.conversations[id] = {...body, id, actor_id: a.user_id}; return {id};}
    if ((match = path.match(/^\/conversations\/([^/]+)\/messages$/)) && method === 'POST') {const conversation = s.conversations[match[1]]; if (!conversation || conversation.actor_id !== a.user_id) error(404, '会话不存在');
      if (['visit_entry', 'opportunity_draft', 'customer_create', 'task_create'].includes(conversation.mode)) error(501, '录入 Agent 暂未接入，请手工填写');
      const id = nextId(21), result = {title: '分析说明', summary: 'Agent 中台暂未接入，暂不提供分析结论。', text: '接入 Agent 中台后，这里会给出分析、评分与客户建议。', rows: [], metrics: []};
      s.runs[id] = {id, status: 'succeeded', actor_id: a.user_id, result, demo: true}; return {run_id: id};}
    if (path === '/advice' && method === 'POST') {const id = nextId(22); const result = {id, status: 'succeeded', subject_kind: body.subject_kind, subject_id: body.subject_id, section: body.section, summary: 'Agent 中台暂未接入，暂无经营建议。', content: '接入 Agent 中台后，这里会给出经营建议。', suggestions: [], data_kind: 'demo'}; s.advice[id] = result; return result;}
    error(501, `尚未实现此操作（${method} ${path}）`);
  }
  function respond(options, uploaded) {
    let finished = false;
    const timer = global.setTimeout(() => {
      if (finished) return; finished = true;
      let response;
      try {if (uploaded) error(501, '当前环境不支持上传文件或音频'); const data = dispatch(options); const accepted = /^\/api\/v1\/visit-flow\/(structure|quality)$/.test(new URL(options.url, 'https://preview.invalid').pathname); response = {statusCode: accepted ? 202 : 200, data: {...copy(data), demo: true, data_kind: 'demo', source_label: data.source_label || LABEL, ...(getState().migration_notice ? {migration_notice: getState().migration_notice} : {})}, header: {'X-Sales-Data-Mode': 'preview'}, errMsg: 'request:ok'};}
      catch (e) {response = {statusCode: e.status || 500, data: {message: e.message, detail: e.message, demo: true, source_label: LABEL}, errMsg: 'request:ok'};}
      if (uploaded) response.data = JSON.stringify(response.data);
      try {if (typeof options.success === 'function') options.success(response);} finally {if (typeof options.complete === 'function') options.complete(response);}
    }, 35);
    return {abort() {if (finished) return; finished = true; global.clearTimeout(timer); const failure = {errMsg: (uploaded ? 'uploadFile' : 'request') + ':fail abort'}; try {if (options.fail) options.fail(failure);} finally {if (options.complete) options.complete(failure);}}, onProgressUpdate() {}};
  }
  global.SalesPreview = Object.freeze({request(options = {}) {return respond(options, false);}, uploadFile(options = {}) {return respond(options, true);}, reset, loadLocal, inspect() {return localDataset ? getState() : null;}});
})(window);
