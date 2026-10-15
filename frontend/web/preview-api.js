/* Explicit, local-only example workspace for the Web compatibility client.
 * Business calls stay offline. Optional CRM samples load from a loopback-only
 * endpoint, only in preview mode; this is never a fallback for a live failure.
 * `data_source: database` is the imported page contract discriminator only;
 * every response carries demo/source labels, and the Web shell labels preview.
 */
(function (global) {
  'use strict';
  let KEY = 'sales-web:preview-workspace:v1';
  const VERSION = 10; // 10：补充 PC 工作台、跟进记录与工作报告演示用的合成跟进记录
  // Isolated synthetic metadata; live reads always use the authenticated backend.
  const BUSINESS_OPTIONS = {"contract_version": 1, "data_source": "synthetic_preview", "customer": {"industry": ["智能制造", "企业软件", "新能源", "医药健康", "交通物流", "人工智能", "其他"], "customer_type": ["潜在客户", "商机客户", "已成单客户", "新拓客户", "存量客户"], "level_code": ["Tier-1", "Tier-2", "Tier-3"], "source": ["销售自拓", "客户转介绍", "市场活动", "销售线索", "合作伙伴", "公司分配", "自主拓展", "其他"], "contact_role": ["使用者", "影响者", "决策者"]}, "opportunity": {"stages": [{"code": "identified", "label": "意向沟通", "probability": 10, "status": "open", "text": "意向沟通－10%"}, {"code": "qualified", "label": "商机确认", "probability": 30, "status": "open", "text": "商机确认－30%"}, {"code": "solution", "label": "方案沟通", "probability": 50, "status": "open", "text": "方案沟通－50%"}, {"code": "proposal", "label": "商务谈判", "probability": 70, "status": "open", "text": "商务谈判－70%"}, {"code": "negotiation", "label": "客户签约", "probability": 90, "status": "open", "text": "客户签约－90%"}, {"code": "won", "label": "赢单 Won", "probability": 100, "status": "won", "text": "赢单 Won－100%"}, {"code": "lost", "label": "丢单 Lost", "probability": null, "status": "lost", "text": "丢单 Lost"}], "grades": [{"code": "A", "label": "A｜100万以上", "amountLabel": "100万以上", "min": 1000000, "max": null}, {"code": "B", "label": "B｜50万–100万", "amountLabel": "50万–100万", "min": 500000, "max": 1000000}, {"code": "C", "label": "C｜10万–50万", "amountLabel": "10万–50万", "min": 100000, "max": 500000}, {"code": "D", "label": "D｜10万以下", "amountLabel": "10万以下", "min": 0, "max": 100000}]}, "map_amount_ranges": [{"value": "0-50", "label": "0–50 万", "min": 0, "max": 499999.99}, {"value": "50-100", "label": "50–100 万", "min": 500000, "max": 999999.99}, {"value": "100-300", "label": "100–300 万", "min": 1000000, "max": 2999999.99}, {"value": "300-500", "label": "300–500 万", "min": 3000000, "max": 4999999.99}, {"value": "500+", "label": "500 万以上", "min": 5000000, "max": null}]};
  let LABEL = '渠道销售工作区';
  // Synthetic response for the additive creation catalog, separate from history filters.
  BUSINESS_OPTIONS.customer_creation = {
    ...JSON.parse(JSON.stringify(BUSINESS_OPTIONS.customer)),
    business_identities: [{value: 'customer', label: '客户'}, {value: 'partner', label: '合作伙伴'}],
    customer_type: ['潜在客户', '商机客户', '已成单客户'],
    source: ['销售自拓', '客户转介绍', '市场活动', '销售线索', '合作伙伴', '其他'],
  };
  let localDataset = null;
  const ROLES = ['sales', 'supervisor', 'manager', 'fde', 'fde_lead'];
  const NAMES = ['王新源', '李明哲', '陈国栋', '周思远', '赵文博'];
  const TITLES = ['一线销售', '销售主管', '销售总经理', 'FDE', 'FDE主管'];
  const STAGES = ['identified', 'qualified', 'solution', 'proposal', 'negotiation', 'won', 'lost'];
  const PROBABILITY = [10, 30, 50, 70, 90, 100, null];
  const STAGE_NAMES = ['意向沟通', '商机确认', '方案沟通', '商务谈判', '客户签约', '赢单', '丢单'];
  const EXTRA_CAPS=['visit.structure','visit.quality_review','visit.read','visit.first','visit.suggest_tasks','advice.read','advice.request','dashboard.ranking','demo_scene.read','demo_scene.create','demo_scene.update','demo_scene.delete'];
  const CAPS = [...EXTRA_CAPS,'customer.read', 'opportunity.read', 'customer.create', 'customer.edit', 'customer.claim', 'opportunity.edit', 'fde.members.manage', 'visit.create', 'visit.supplement', 'task.create', 'task.respond', 'task.cancel', 'task.coordinate', 'risk.resolve', 'risk.decide', 'advice.decide', 'actual.manage', 'team.view', 'console.access'];
  const uid = (kind, index) => `${String(kind).padStart(8, '0')}-0000-4000-8000-${String(index).padStart(12, '0')}`;
  const now = () => new Date().toISOString();
  const today = () => new Date(Date.now() + 28800000).toISOString().slice(0, 10);
  const yearNow = () => Number(today().slice(0, 4));
  const date = (days = 0, hour = '10:00:00') => new Date(Date.now() + days * 86400000 + 28800000).toISOString().slice(0, 10) + 'T' + hour + '+08:00';
  const copy = value => JSON.parse(JSON.stringify(value));
  const sum = (rows, key) => rows.reduce((n, row) => n + Number(row[key] || 0), 0);
  const uniq = values => [...new Set(values.filter(Boolean))];
  const isFde = actor => actor.role === 'fde' || actor.role === 'fde_lead';
  function actorFor(role) {
    const index = ROLES.indexOf(role);
    const sales = !role.startsWith('fde');
    const open = sales ? CAPS.filter(key => !['console.access', 'actual.manage'].includes(key) && (role !== 'sales' || !['team.view', 'task.coordinate', 'risk.resolve'].includes(key))) :
      ['risk.decide', 'customer.read', 'opportunity.read', 'task.create', 'task.respond', 'task.cancel', 'visit.create', 'visit.supplement', ...(role === 'fde_lead' ? ['team.view', 'fde.members.manage', 'task.coordinate', 'risk.resolve'] : [])];
    const bound = localDataset?.actors.find(person => person.user_id === localDataset.role_bindings[role]);
    return normalizedActor({ user_id: bound?.user_id || uid(1, index + 1), workspace_id: uid(2, 1), company_code:'demo', company_name:'SalesBuddy 演示公司', account_code: 'PREVIEW_' + role.toUpperCase(), display_name: localDataset ? '本地' + TITLES[index] + '视角' + (bound ? ' · ' + bound.display_name : '') : NAMES[index], role,
      role_name: TITLES[index], scope_name: sales ? role === 'manager' ? '全部团队' : role === 'supervisor' ? '直属团队' : '仅本人' : role === 'fde' ? '本人协助项目' : 'FDE 团队协助项目',
      team_ids: [uid(3, 1)], team_names: [localDataset ? '本地样本组（非实际组织）' : '渠道销售-南区'], permission_version: 'preview-v1', capabilities: Object.fromEntries(CAPS.map(key => [key, open.includes(key) || (EXTRA_CAPS.includes(key) && (key.startsWith('demo_scene.') ? !sales : key==='visit.first' ? sales : true))])), demo: true });
  }
  // These are explicit synthetic server policies, never a client-side live-role fallback.
  const PREVIEW_READS = ['overview.read', 'directory.read', 'partner.read', 'battle_map.read', 'customer.read', 'opportunity.read', 'visit.read', 'task.read', 'risk.read', 'actual.read', 'material.read', 'dashboard.read', 'target.read', 'report.read'];
  const PREVIEW_READ_SCOPE = {sales: 'teams', fde: 'teams', supervisor: 'workspace', fde_lead: 'workspace', manager: 'workspace'};
  function normalizedActor(actor) {
    const aliases = {'customer.edit':['customer.update'], 'opportunity.edit':['opportunity.create','opportunity.update'], 'task.create':['task.create_daily','task.create_customer'], 'task.respond':['task.accept','task.decline','task.complete','task.review'], 'fde.members.manage':['opportunity.fde_members'], 'actual.manage':['actual.create','actual.void'], 'visit.first':['visit.first_visit']};
    const permissions = {'access.web': true, 'access.mini_program': true, 'visit.export': false, 'report.export': false};
    for (const [key, value] of Object.entries(actor.capabilities)) for (const action of aliases[key] || [key]) permissions[action] = value;
    for (const action of PREVIEW_READS) permissions[action] = true;
    permissions['customer.claim_directory'] = permissions['customer.claim'] === true;
    permissions[isFde(actor) ? 'profile.fde_read' : 'profile.sales_read'] = true;
    if (isFde(actor)) permissions['profile.fde_activity'] = true;
    for (const action of ['advice.customer', 'advice.opportunity', 'advice.visit']) permissions[action] = permissions['advice.request'] === true;
    const scope = PREVIEW_READ_SCOPE[actor.role];
    const permission_grants = Object.entries(permissions).filter(([, allowed]) => allowed).map(([permission_code]) => ({permission_code,
      scope_code: PREVIEW_READS.includes(permission_code) ? scope : permission_code === 'visit.create' && isFde(actor) ? 'assigned' : actor.role === 'manager' ? 'workspace' : actor.role === 'supervisor' || actor.role === 'fde_lead' && ['risk.decide','risk.resolve'].includes(permission_code) ? 'teams' : 'self',
      team_ids: actor.team_ids.slice(), source_code: 'preview_fixture', source_id: actor.role, effect: 'allow'}));
    return {...actor, permissions, permission_grants, client_export_permissions: {'visit.export': false, 'report.export': false}, permission_version: 'preview-auth001-v1', scope_name: scope === 'workspace' ? '全公司' : '本部门'};
  }
  function readGrants(actor, permission) {
    if (!actor.permissions?.[permission] || actor.permission_grants.some(g => g.permission_code === permission && g.effect === 'deny')) error(403, '当前账号没有该数据的查看权限');
    return actor.permission_grants.filter(g => g.permission_code === permission && g.effect === 'allow');
  }
  function readDirectoryPeople(actor, permission = 'directory.read') {
    const grants = readGrants(actor, permission);
    return actors.filter(person => grants.some(g => g.scope_code === 'workspace' || g.scope_code === 'teams' && person.team_ids.some(id => g.team_ids.includes(id)) || g.scope_code === 'self' && person.user_id === actor.user_id));
  }
  function readDirectoryTeams(actor, permission = 'directory.read') {
    const grants = readGrants(actor, permission);
    return directoryTeams({...actor, capabilities: {...actor.capabilities, 'team.view': true}}).filter(team => grants.some(g => g.scope_code === 'workspace' || g.scope_code === 'teams' && g.team_ids.includes(team.id)));
  }
  function readable(actor, permission, owners, teams = []) {
    return readGrants(actor, permission).some(g => g.scope_code === 'workspace' || g.scope_code === 'teams' && teams.some(id => g.team_ids.includes(id)) || ['self','assigned'].includes(g.scope_code) && owners.includes(actor.user_id));
  }
  function objectAllowed(actor, action, owners, teams = []) {
    return !!actor && actor.permissions?.[action] === true && !actor.permission_grants.some(g => g.permission_code === action && g.effect === 'deny') && actor.permission_grants.some(g => g.permission_code === action && g.effect === 'allow' && (g.scope_code === 'workspace' || g.scope_code === 'teams' && teams.some(id => g.team_ids.includes(id)) || ['self','assigned'].includes(g.scope_code) && owners.includes(actor.user_id)));
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
      potential_score: [87, 92, 42, 55, 76, 81, 61, 33, 94][i % 9], relationship_score: [48, 83, 38, 85, 32, 91, 52, 76, 63][i % 9], quadrant_code: quadrants[i % 4],
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
        creator_id: actors[ci < 6 ? 0 : 1].user_id, creator_name: actors[ci < 6 ? 0 : 1].display_name, business_created_on: dayKey(date(-30 - index)),
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
    // 补充的合成跟进：覆盖今天、本周、本月与本季度，记录人含一线销售、销售主管与 FDE，内容均为虚构示例。
    const notes = [
      ['客户信息化部关注数据安全与私有化部署，要求补充部署架构说明。', '本周五前发送部署架构说明，并约技术评审时间。', 'offline_meeting'],
      ['与业务负责人过了一遍质检规则样例，对误报率有顾虑，约定下周看小样本测试结果。', '下周三前跑完 200 条小样本测试，输出误报率对比。', 'online_meeting'],
      ['客户希望先在两条产线试点，预算在年度信息化专项里，需要分阶段报价。', '三天内给出两阶段报价方案，经主管审核后发客户。', 'offline_meeting'],
      ['演示了知识助理的问答效果，客户提出要接入现有的 OA 文档库。', '明天和 FDE 确认 OA 文档库的接入方式与权限范围。', 'online_meeting'],
      ['采购部门要求补充同类客户案例与资质材料，已承诺本周内提供。', '周四前整理资质与案例包（脱敏版）发采购。', 'phone_voice'],
      ['技术交流会：FDE 讲解了接口对接方式，客户 IT 认可方案，等待内部评审。', '跟进评审结论，评审通过后启动联调排期。', 'offline_meeting'],
      ['客户高层对经营分析看板很感兴趣，希望先看本季度的样例报表。', '下周一前用客户提供的样例数据做一版看板演示。', 'online_meeting'],
      ['电话沟通试点进展，客户反馈首批数据已经准备好，可以开始联调。', '今天下班前同步 FDE，明天上午开联调会。', 'phone_voice'],
      ['客户内部调整，原对接人转岗，需要重新建立与新负责人的联系。', '本周内通过原对接人引荐，约新负责人见面。', 'social_meal'],
      ['复盘试点第一周使用情况，日活稳定，客户提出导出报表的需求。', '整理导出需求，评估是否纳入二期范围。', 'online_meeting'],
    ];
    const plan = [[0, 0, 0], [0, 1, 1], [0, 3, 3], [0, 0, 6], [1, 1, 2], [1, 0, 5], [1, 3, 7], [2, 1, 4], [2, 0, 8], [3, 0, 9], [3, 4, 1], [4, 1, 3], [5, 0, 0], [6, 1, 6],
      [8, 0, 2], [9, 3, 5], [12, 1, 8], [15, 0, 4], [18, 1, 7], [22, 0, 1], [26, 3, 9], [33, 1, 3], [41, 0, 6], [55, 1, 2]];
    // 「今天」的合成跟进不能晚于当前时刻：凌晨打开演示时，时间点压到今天 0 点之后、现在之前
    const stamp = ms => new Date(ms + 28800000).toISOString().slice(0, 19) + '+08:00';
    const startToday = Date.parse(today() + 'T00:00:00+08:00');
    const when = (days, hour, k) => days ? date(-days, hour) : stamp(Math.max(startToday + 60000 * (k + 1), Math.min(Date.parse(date(0, hour)), Date.now() - 300000 * (k + 1))));
    plan.forEach(([days, who, n], k) => {
      const recorder = actors[who], own = opportunities.filter(op => op.status === 'open' && (who === 3 || who === 4 ? (op.fde_member_ids || []).includes(recorder.user_id) : op.owner_id === recorder.user_id));
      const op = own[(k * 3) % own.length] || opportunities[k % opportunities.length];
      const [record, next, mode] = notes[n];
      visits.push({id: uid(13, 101 + k), customer_id: op.customer_id, customer_name: op.customer_name, opportunity_id: op.id, opportunity_name: op.name,
        recorder_id: recorder.user_id, recorder_name: recorder.display_name, creator_name: recorder.display_name,
        interaction_at: when(days, ['09:30:00', '11:00:00', '14:30:00', '16:00:00'][k % 4], k), visit_date: date(-days).slice(0, 10), created_at: days ? date(-days, '18:00:00') : stamp(Math.max(Date.parse(when(0, ['09:30:00', '11:00:00', '14:30:00', '16:00:00'][k % 4], k)), Math.min(Date.now() - 60000, Date.parse(date(0, '18:00:00'))))), created_date: date(-days),
        customer_type: 'opportunity', follow_up_record: record, next_action: next, visit_goal: ['确认试点边界', '推进方案评审', '确认预算与采购流程', '建立新联系人'][k % 4],
        interaction_mode_code: mode, expectation_code: ['met', 'met_50_100', 'exceeded', 'met_30_50'][k % 4], duration_minutes: [30, 45, 60, 90][k % 4], contact_name_snapshot: k % 2 ? '刘志强' : '张晓静',
        partner_name_snapshot: op.partner_name, fde_participants: who >= 3 ? [member(recorder)] : op.fde_members.slice(0, k % 2), fde_participant_ids: who >= 3 ? [recorder.user_id] : op.fde_member_ids.slice(0, k % 2),
        collaborators: [], within_seven_days: days < 7, can_read_detail: true, is_first_visit: k === 8, status: 'archived', quality_score: null, follow_up_score: null, data_kind: 'demo', version_no: 1});
    });
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
      owner_id: op.owner_id, owner_name: op.owner_name, team_name: owner?.team_names?.[0] || '渠道销售-南区', title: r.title, description: r.description, evidence: r.evidence, suggested_action: r.next_action || null, next_action: r.next_action || null, version_no:1, events:[],
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
  function error(status, message, code) { throw Object.assign(new Error(message), {status, code}); }
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
  // Synthetic preview policy only. Live writes always go to the backend's RLS and permissions.
  function previewRiskAllowed(actor,row,permission) {
    const owner=row.owner_user_ref_id || row.owner_id;
    const teams=row.owner_team_id?[row.owner_team_id]:(actors.find(person=>person.user_id===owner)?.team_ids||[]);
    const leader=['manager','supervisor','fde_lead'].includes(actor.role);
    const managed=leader && (actor.role==='manager' || teams.some(id=>actor.team_ids.includes(id)));
    return objectAllowed(actor,permission,[owner],teams) && (permission==='risk.resolve'?managed:owner===actor.user_id || managed);
  }
  function previewRiskView(actor,row) {
    const suggestion=Object.hasOwn(row,'suggested_action')?row.suggested_action:row.next_action||null;
    const events=row.events||[],latest=[...events].reverse().find(e=>e.event_type==='suggestion_decided');
    return {...row,version_no:row.version_no||1,events,suggested_action:suggestion,next_action:suggestion,
      can_resolve:row.status!=='resolved' && previewRiskAllowed(actor,row,'risk.resolve'),
      can_decide:row.status!=='resolved' && Boolean(suggestion) && previewRiskAllowed(actor,row,'risk.decide'),
      current_decision:latest?{event_id:latest.id,decision:latest.payload.decision,note:latest.payload.note,actor_name:latest.actor_name,occurred_at:latest.occurred_at,risk_version:latest.payload.risk_version_after,is_current:latest.payload.risk_version_after===(row.version_no||1)}:null};
  }
  function directoryPeople(actor) {
    return actors.filter(person => person.user_id === actor.user_id || actor.capabilities['team.view'] &&
      (actor.role === 'manager' || isFde(person) === isFde(actor) && person.team_ids.some(id => actor.team_ids.includes(id))));
  }
  // 一线销售可只读本人团队（2026-10-06 需求：团队 +1 主管能看的客户，本人也能看）；跨团队与写操作仍按原权限。
  function teamPeers(actor) {
    return actor.role === 'sales' ? actors.filter(person => !isFde(person) && person.team_ids.some(id => actor.team_ids.includes(id))) : [];
  }
  function salesTeamRead(actor, params) {
    if (actor.role !== 'sales') return false;
    const teamId = params.get('team_id'), memberIds = [params.get('member_id'), ...params.getAll('member_ids')].filter(Boolean);
    return params.get('scope') === 'team' || params.get('scope') === 'person' || (!!teamId && actor.team_ids.includes(teamId)) || memberIds.some(id => id !== actor.user_id);
  }
  function scopeSelection(actor, params, permission = 'directory.read') {
    const teamId = params.get('team_id'), memberId = params.get('member_id'), memberIds = params.getAll('member_ids');
    const allowed = readContext ? readDirectoryPeople(actor, permission) : directoryPeople(actor);
    if (teamId && !(readContext ? readDirectoryTeams(actor, permission) : directoryTeams(actor)).some(team => team.id === teamId)) error(403, '无权查看该团队');
    if (!readContext && teamId && !actor.capabilities['team.view'] && !(actor.role === 'sales' && actor.team_ids.includes(teamId))) error(403, '当前身份不能选择团队');
    const ids = memberId ? [memberId] : memberIds;
    if (ids.some(id => !allowed.some(person => person.user_id === id))) error(403, '无权查看该成员');
    if (teamId && ids.some(id => !allowed.find(person => person.user_id === id).team_ids.includes(teamId))) error(422, '所选成员不属于该团队');
    return {teamId, ids, people: allowed.filter(person => (!teamId || person.team_ids.includes(teamId)) && (!ids.length || ids.includes(person.user_id))), active: Boolean(teamId || ids.length)};
  }
  function visibleOpportunities(actor, params = new URLSearchParams()) {
    const selection = scopeSelection(actor, params, 'opportunity.read');
    let rows = getState().opportunities;
    if (readContext) rows = rows.filter(o => readable(actor, 'opportunity.read', [o.owner_id, ...(o.fde_member_ids || [])], [o.team_id]));
    else if (actor.role === 'sales') {const owners = new Set((salesTeamRead(actor, params) || readContext?.user_id === actor.user_id ? teamPeers(actor) : [actor]).map(person => person.user_id)); rows = rows.filter(o => owners.has(o.owner_id));}
    else if (!readContext && isFde(actor) && (actor.role === 'fde' || params.get('scope') === 'self')) rows = rows.filter(o => (o.fde_member_ids || []).includes(actor.user_id));
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
    if (params.get('trade_relation')) rows = rows.filter(row => row.trade_relation === params.get('trade_relation'));
    if (params.get('exclude_customer_id')) rows = rows.filter(row => row.customer_id !== params.get('exclude_customer_id'));
    if (params.get('exclude_opportunity_id')) rows = rows.filter(row => row.id !== params.get('exclude_opportunity_id'));
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
    return rows.map(enrichOpportunity);
  }
  function visibleCustomers(actor) {
    if (readContext) return getState().customers.filter(c => readable(actor, 'customer.read', [c.owner_id], [c.owner_team_id]));
    const visible = new Set(visibleOpportunities(actor).map(o => o.customer_id));
    const peers = readContext?.user_id === actor.user_id ? new Set(teamPeers(actor).map(person => person.user_id)) : null;
    return getState().customers.filter(c => actor.role === 'manager' || actor.role === 'supervisor' || c.owner_id === actor.user_id || visible.has(c.id) || (peers && peers.has(c.owner_id)));
  }
  // Explicit synthetic read context; fixture write operations keep their independent checks.
  let readContext = null;
  function withTeamRead(actor, method, run) {
    const previous = readContext;
    readContext = method === 'GET' ? actor : null;
    try { return run(); } finally { readContext = previous; }
  }
  function scopedCustomers(actor, params) {
    const selection = scopeSelection(actor, params, 'customer.read');
    let rows = salesTeamRead(actor, params) ? withTeamRead(actor, 'GET', () => visibleCustomers(actor)) : visibleCustomers(actor);
    if (isFde(actor) && selection.active) {
      const customerIds = new Set(visibleOpportunities(actor, params).map(op => op.customer_id));
      return rows.filter(row => customerIds.has(row.id));
    }
    if (selection.teamId) rows = rows.filter(row => row.owner_team_id === selection.teamId || selection.people.some(person => person.user_id === row.owner_id));
    if (selection.ids.length) rows = rows.filter(row => selection.ids.includes(row.owner_id) || (row.sales_members || []).some(person => selection.ids.includes(person.user_id || person.id)));
    if (!isFde(actor) && params.get('scope') === 'self') rows = rows.filter(row => row.owner_id === actor.user_id);
    if (!readContext && actor.role === 'sales' && params.get('scope') === 'team' && !selection.teamId) rows = rows.filter(row => teamPeers(actor).some(person => person.user_id === row.owner_id));
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
  // 与小程序 1.1.1 的活跃筛选一致：窗口内有已确认 / 已归档跟进的客户与商机才算活跃
  function activitySince(windowKey) {
    const day = new Date(today() + 'T00:00:00Z');
    if (windowKey === '3d') day.setUTCDate(day.getUTCDate() - 2);
    else if (windowKey === 'week') day.setUTCDate(day.getUTCDate() - (day.getUTCDay() + 6) % 7);
    else if (windowKey === 'month') day.setUTCDate(1);
    else if (windowKey !== 'today') return null;
    return day.toISOString().slice(0, 10);
  }
  function activeVisits(params) {
    const since = activitySince(params.get('activity_window'));
    if (!since) return null;
    return getState().visits.filter(v => ['confirmed', 'archived'].includes(v.status) && dayKey(v.visit_date || v.interaction_at) >= since && dayKey(v.visit_date || v.interaction_at) <= today());
  }
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
        recognized_amount: actualSummary(effectiveActuals().filter(entry => opIds.has(entry.opportunity_id) && actualInPeriod(entry, params))).recognized_amount};
    };
    const items = personal ? cohort.map(person => ({user_id: person.user_id, name: person.display_name, role: person.role, team_name: person.team_names[0], ...stats([person])})) : directoryTeams(actor).map(team => ({user_id: team.id, name: team.name, role: 'fde_team', team_name: team.name, ...stats(actors.filter(person => isFde(person) && person.team_ids.includes(team.id)))}));
    return {contract_version: 2, data_source: 'database', scope: personal ? selected.role === 'fde_lead' ? 'all_fde_leads' : 'all_fde' : 'company_fde_teams', complete: true,
      year: Number(params.get('year') || yearNow()), quarters: params.getAll('quarters').map(Number), selection: {personal, member_id: personal ? selected.user_id : null, cohort_role: selected.role, team_ids: personal ? [] : [params.get('team_id') || uid(3, 1)]}, total: items.length, items};
  }
  function enrichOpportunity(o) {
    const actuals = actualSummary(effectiveActuals().filter(e => e.opportunity_id === o.id));
    const editable = objectAllowed(readContext, 'opportunity.update', [o.owner_id], [o.team_id]), fde = objectAllowed(readContext, 'opportunity.fde_members', [o.owner_id], [o.team_id]);
    const primary = actors.find(person => person.user_id === o.primary_fde_id);
    return {...o, can_edit: editable, actuals, can_manage_fde_members: fde, capabilities: {editable_fields: [...(editable ? WORKSPACE_INLINE.concat('participants','stage','status','amount','sales_channel','partner','expected_close_date','forecast_recognized','forecast_collection','collection_confidence') : []), ...(fde ? ['primary_fde_id','fde_member_ids'] : [])], can_manage_fde_members: fde}, primary_fde: o.primary_fde_id ? primary ? {...member(primary), is_active:true} : {id:o.primary_fde_id,name:'历史成员',is_active:false} : null, participants: (o.participants||[]).map(p=>({...p,name:actors.find(a=>a.user_id===p.user_id)?.display_name||p.name,is_active:true}))};
  }
  function previewPartners(state) {
    return [...(localDataset ? [] : [{id: uid(14, 1), name: '华南数码渠道', partner_name: '华南数码渠道', status: 'active', active: true}]), ...(state.partner_profiles || [])]
      .map(row => ({...row, business_identities: ['partner'], linked_customer_id: state.customers.find(c => c.partner_profile_id === row.id)?.id || null}));
  }
  function enrichCustomer(c, actor) {
    const ops = visibleOpportunities(actor).filter(o => o.customer_id === c.id && o.status === 'open');
    return {...c, business_identities: c.business_identities || ['customer'], customer_analysis_applicable: (c.business_identities || ['customer']).includes('customer'), can_edit: objectAllowed(actor, 'customer.update', [c.owner_id], [c.owner_team_id]), opportunity_amount: sum(ops, 'amount'), acv_amount: sum(ops, 'amount'), opportunity_name: (ops[0] || {}).name || '', opportunity_stage: (ops[0] || {}).stage_code || '',
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
    const selection = scopeSelection(actor, params, 'task.read');
    let rows = getState().tasks;
    if (readContext) rows = rows.filter(t => readable(actor, 'task.read', [...t.assignees.map(p => p.user_id), t.creator_user_ref_id], uniq([...t.assignees.map(p => p.user_id), t.creator_user_ref_id, ...(t.candidate_user_ids || [])].flatMap(id => actors.find(a => a.user_id === id)?.team_ids || []))));
    if (!readContext && actor.role === 'fde_lead') rows = rows.filter(t => t.assignees.some(person => actors.some(a => isFde(a) && a.user_id === person.user_id)) || t.target_position === 'fde' || t.creator_user_ref_id === actor.user_id);
    if (!readContext && !['manager', 'supervisor', 'fde_lead'].includes(actor.role) || params.get('view') === 'self') rows = rows.filter(t => t.assignees.some(a => a.user_id === actor.user_id) || (t.candidate_user_ids || []).includes(actor.user_id) || t.creator_user_ref_id === actor.user_id);
    const memberId = params.get('member_id'); if (memberId) rows = rows.filter(t => t.assignees.some(a => a.responsibility === 'owner' && a.user_id === memberId));
    if (selection.teamId || params.getAll('member_ids').length) {
      const ids = new Set(selection.people.map(person => person.user_id));
      rows = rows.filter(t => t.assignees.some(person => person.responsibility === 'owner' && ids.has(person.user_id)));
    }
    for (const key of ['customer_id', 'opportunity_id']) if (params.get(key)) rows = rows.filter(t => t[key] === params.get(key));
    return rows;
  }
  function taskResponse(row, actor) {
    const own = row.assignees.some(p => p.user_id === actor.user_id && p.responsibility === 'owner');
    const requiresAction = row.status === 'pending_confirm' && (row.target_position ? (row.candidate_user_ids || []).includes(actor.user_id) && !(row.declined_user_ids || []).includes(actor.user_id) : own);
    const canCancel = actor.capabilities['task.cancel'] === true && ['pending_confirm','pending_execution','in_progress','deferred'].includes(row.status) && (actor.capabilities['task.coordinate'] || row.creator_user_ref_id === actor.user_id || own);
    const canCoordinate = actor.capabilities['task.coordinate'] === true && !['completed','cancelled'].includes(row.status);
    const actionPermissions = {'task.accept': requiresAction, 'task.decline': requiresAction,
      'task.complete': own && ['pending_execution','in_progress','deferred'].includes(row.status),
      'task.review': row.creator_user_ref_id === actor.user_id && row.status === 'pending_review',
      'task.cancel': canCancel, 'task.coordinate': canCoordinate};
    return {...row, title: row.title || row.description, requires_action: requiresAction, can_cancel: canCancel, can_coordinate: canCoordinate,
      action_permissions: Object.fromEntries(Object.entries(actionPermissions).map(([action, allowed]) => [action, allowed && actor.permissions[action] === true]))};
  }
  function customerDetail(actor, id, opId, mode = 'overview') {
    const c = customer(actor, id), ops = visibleOpportunities(actor).filter(o => o.customer_id === id && (!opId || o.id === opId)).map(enrichOpportunity);
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
  // Synthetic server read model only: effective_actuals is an explicit current
  // projection fixture. Raw history is never added to it or used as a fallback
  // when the chosen projection is empty. Live source decisions belong to API.
  function effectiveActuals() {
    const state = getState();
    return (state.effective_actuals ?? state.actuals).filter(row => row.status !== 'void').map(row => ({
      ...row, year: row.year ?? Number(String(row.occurred_on || '').slice(0, 4)),
      quarter: row.quarter ?? Math.ceil(Number(String(row.occurred_on || '').slice(5, 7)) / 3),
      source: row.source || 'entries', period_precision: row.period_precision || (row.occurred_on ? 'day' : 'quarter'),
      occurred_on: row.occurred_on || null, complete: row.complete !== false, can_void: false,
    }));
  }
  function filledActual(row) { return row.amount != null && row.status !== 'withdrawn'; }
  function actualAsOf(row, asOf) {
    return row.occurred_on ? row.occurred_on <= asOf : row.year < Number(asOf.slice(0, 4)) || row.year === Number(asOf.slice(0, 4)) && row.quarter <= Math.ceil(Number(asOf.slice(5, 7)) / 3);
  }
  function actualInPeriod(row, params) {
    if (row.occurred_on) return inPeriod(row.occurred_on, params);
    // A quarter-level confirmation has no daily/monthly occurrence to assign.
    if (params.get('date_from') || params.get('date_to') || ['month', 'week'].includes(params.get('period'))) return false;
    const quarters = params.getAll('quarters').map(Number);
    return row.year === Number(params.get('year') || yearNow()) && (!quarters.length || quarters.includes(row.quarter)) && (params.get('period') !== 'quarter' || row.quarter === Math.ceil(Number(today().slice(5, 7)) / 3));
  }
  function entriesFor(actor, params) {
    const ids = new Set(scopedCustomers(actor, params).map(c => c.id));
    let rows = effectiveActuals().filter(e => ids.has(e.customer_id));
    if (!isFde(actor) && params.get('scope') === 'self') rows = rows.filter(e => e.owner_id === actor.user_id);
    if (!isFde(actor) && params.get('scope') === 'person' && params.get('account_code')) {const selected = actors.find(a => a.account_code === params.get('account_code')); rows = rows.filter(e => selected && e.owner_id === selected.user_id);}
    for (const key of ['customer_id', 'opportunity_id', 'kind']) if (params.get(key)) rows = rows.filter(e => e[key] === params.get(key));
    const asOf = params.get('as_of') || today(); rows = rows.filter(e => actualAsOf(e, asOf));
    if (params.get('period') === 'year') rows = rows.filter(e => e.year === Number(asOf.slice(0, 4)));
    return rows;
  }
  function actualSummary(rows) {
    const result = {entry_count: rows.filter(filledActual).length};
    for (const kind of ['recognized', 'collection']) {
      const selected = rows.filter(row => row.kind === kind), complete = selected.length > 0 && selected.every(row => filledActual(row) && row.complete !== false && !row.tax_unknown);
      const periodCount = values => new Set(values.map(row => `${row.opportunity_id || row.customer_id}:${row.year}:${row.quarter}`)).size;
      Object.assign(result, {[kind + '_amount']: complete ? sum(selected, 'amount') : null,
        [kind + '_confirmed_subtotal']: subtotal(selected), [kind + '_complete']: complete,
        [kind + '_count']: selected.filter(filledActual).length, [kind + '_period_count']: periodCount(selected),
        [kind + '_incomplete_period_count']: periodCount(selected.filter(row => !filledActual(row) || row.complete === false || row.tax_unknown)),
        [kind + '_missing_material_count']: sum(selected, 'missing_material_count'),
        [kind + '_tax_unknown_period_count']: periodCount(selected.filter(row => row.tax_unknown)),
        [kind + '_conflict_period_count']: periodCount(selected.filter(row => row.status === 'conflict'))});
    }
    result.complete = result.recognized_complete && result.collection_complete;
    return result;
  }
  function subtotal(rows) {
    const known = rows.filter(row => row.status !== 'withdrawn' && (row.confirmed_subtotal != null || filledActual(row)));
    return known.length ? known.reduce((n, row) => n + Number(row.confirmed_subtotal ?? row.amount), 0) : null;
  }
  function actualQuarters(rows) {
    const groups = {};
    for (const row of rows) (groups[`${row.year}-${row.quarter}`] ||= []).push(row);
    return Object.entries(groups).map(([id, values]) => ({id, year: values[0].year, quarter: values[0].quarter, ...actualSummary(values),
      recognized_count: values.filter(e => e.kind === 'recognized' && filledActual(e)).length,
      collection_count: values.filter(e => e.kind === 'collection' && filledActual(e)).length, conflict_period_count: 0}));
  }
  function financialOverview(op, params) {
    const selectedYear = params.get('year'), selectedQuarter = params.get('quarter');
    if (Boolean(selectedYear) !== Boolean(selectedQuarter) || selectedQuarter && ![1,2,3,4].includes(Number(selectedQuarter))) error(422, '请同时选择有效年和季度');
    let rows = effectiveActuals().filter(row => row.opportunity_id === op.id);
    if (selectedYear) rows = rows.filter(row => row.year === Number(selectedYear) && row.quarter === Number(selectedQuarter));
    const keys = uniq(rows.map(row => `${row.year}-${row.quarter}`));
    if (selectedYear && !keys.length) keys.push(`${selectedYear}-${selectedQuarter}`);
    const periods = keys.flatMap(key => ['recognized', 'collection'].map(kind => {
      const [year, quarter] = key.split('-').map(Number), values = rows.filter(row => row.year === year && row.quarter === quarter && row.kind === kind);
      const summary = actualSummary(values), amount = summary[kind + '_amount'], current = values[0];
      const status = values.some(row => row.status === 'partial') ? 'partial' : values.length && values.every(row => row.status === 'withdrawn') ? 'withdrawn' : amount == null ? 'missing' : 'confirmed';
      return {year, quarter, kind, amount, confirmed_subtotal: subtotal(values), known_exclusive_subtotal: kind === 'recognized' ? subtotal(values) : null,
        source: current?.source || null, status, complete: summary[kind + '_complete'], tax_basis: kind === 'recognized' && values.length ? 'exclusive' : null,
        tax_unknown: values.some(row => row.tax_unknown), confirmations: values.flatMap(row => row.confirmations || []),
        expected_material_line_ids: uniq(values.flatMap(row => row.expected_material_line_ids || [])),
        expected_material_count: sum(values, 'expected_material_count'), confirmed_material_count: sum(values, 'confirmed_material_count'), missing_material_count: sum(values, 'missing_material_count'),
        entry_amount: current?.source === 'entries' ? amount : null, entries: values.filter(row => row.source === 'entries'), history: [], decision: null,
        reference_changed: values.some(row => row.reference_changed), version_no: current?.version_no || 0, source_fingerprint: '0'.repeat(64), period_precision: 'quarter', occurred_on: null};
    }));
    const totals = Object.fromEntries(['recognized', 'collection'].map(kind => {
      const ps = periods.filter(row => row.kind === kind);
      return [kind, {amount: actualSummary(ps)[kind + '_amount'], confirmed_subtotal: subtotal(ps), complete: ps.length > 0 && ps.every(row => row.complete),
        conflict_period_count: 0, resolved_period_count: ps.filter(row => row.complete).length, tax_unknown_period_count: ps.filter(row => row.tax_unknown).length,
        partial_period_count: ps.filter(row => row.status === 'partial').length, missing_period_count: ps.filter(row => ['missing','withdrawn'].includes(row.status)).length}];
    }));
    return {schema_version: 2, currency: 'CNY', opportunity_id: op.id, customer_id: op.customer_id, name: op.name, opportunity_version: op.version_no,
      periods, totals, conflict_period_count: 0, can_confirm: false, quality: periods.every(row => row.complete) ? 'complete' : 'incomplete', coverage_note: '合成预览：季度归属，不代表真实到账日'};
  }
  function materialDraft(body, existing, actor, customerId, nextId) {
    const state = getState(), relation = body.trade_relation ?? existing?.trade_relation ?? null;
    const upstreamId = body.upstream_opportunity_id ?? existing?.upstream_opportunity_id ?? null;
    const previous = existing?.material_lines || [], retired = copy(existing?.retired_material_lines || []);
    if (relation && !['direct', 'distributor', 'downstream'].includes(relation)) error(422, '请选择有效交易关系');
    if (relation === 'downstream') {
      const upstream = opportunity(actor, upstreamId);
      if (upstream.trade_relation !== 'distributor' || upstream.customer_id === customerId || upstream.id === existing?.id) error(422, '请选择其他客户的渠道商商机作为上游');
    } else if (body.upstream_opportunity_id) error(422, '仅渠道下游终端关联上游商机');
    if (existing?.trade_relation && (relation !== existing.trade_relation || (relation === 'downstream' ? upstreamId : null) !== (existing.upstream_opportunity_id || null)) && effectiveActuals().some(row => row.opportunity_id === existing.id && (filledActual(row) || row.confirmed_subtotal != null))) error(409, '已有实际金额，须先由运营核对交易关系');
    let lines = copy(previous);
    if (body.material_lines != null) {
      if (!Array.isArray(body.material_lines) || body.material_lines.length > 200 || body.material_lines.length && !relation) error(422, '物料最多200行，添加物料前须明确交易关系');
      const ids = body.material_lines.map(row => row.id).filter(Boolean);
      if (ids.length !== new Set(ids).size) error(422, '物料行标识重复');
      const scaled = (value, decimals, label) => {
        const text = String(value ?? '');
        if (!new RegExp('^\\d{1,14}(?:\\.\\d{1,' + decimals + '})?$').test(text)) error(422, label + '格式无效');
        const [whole, fraction = ''] = text.split('.'); return BigInt(whole + fraction.padEnd(decimals, '0'));
      };
      lines = body.material_lines.map((input, index) => {
        const old = input.id && previous.find(row => row.id === input.id), catalog = (state.materials || []).find(row => row.id === input.material_id && row.is_active !== false);
        if (input.id && !old || old && old.material_id !== input.material_id) error(422, '已有物料行不能更换物料，请新增交易行');
        if (!old && !catalog) error(422, '请选择目录中启用的物料');
        if (!['self_use','resale'].includes(input.usage)) error(422, '请选择物料用途');
        const quantity = scaled(input.quantity, 4, '数量'), price = scaled(input.unit_price, 2, '成交单价');
        if (quantity <= 0n) error(422, '数量必须大于零');
        const cents = (quantity * price + 5000n) / 10000n;
        const line = {...old, id: old?.id || nextId(32), material_id: input.material_id, line_no: index + 1, quantity: String(input.quantity), unit_price: String(input.unit_price), usage: input.usage,
          catalog_snapshot: copy(old?.catalog_snapshot || catalog), line_amount: `${cents / 100n}.${String(cents % 100n).padStart(2, '0')}`, is_active: true, note: String(input.note || '')};
        for (const field of ['expected_delivery_on','expected_start_on','expected_oa_on','service_months','expected_account_count','expected_consumption_amount','expected_consumption_year','expected_consumption_quarter']) line[field] = input[field] ?? null;
        if (Boolean(line.expected_consumption_year) !== Boolean(line.expected_consumption_quarter)) error(422, '预计消耗期间须同时提供年和季度');
        return line;
      });
      const activeIds = new Set(lines.map(row => row.id));
      retired.push(...previous.filter(row => !activeIds.has(row.id)).map(row => ({...row, is_active: false})));
    }
    return {trade_relation: relation, upstream_opportunity_id: relation === 'downstream' ? upstreamId : null, material_lines: lines, retired_material_lines: retired};
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
    const ops = state.opportunities.filter(op => allLocal || owners.has(ownerOf(op))).map(enrichOpportunity), customerIds = new Set(ops.map(op => op.customer_id));
    const entries = effectiveActuals().filter(entry => {
      const owner = ownerOf(entry) || ownerOf(state.opportunities.find(op => op.id === entry.opportunity_id) || {}) || ownerOf(state.customers.find(customer => customer.id === entry.customer_id) || {});
      return owners.has(owner) && ['recognized', 'collection'].includes(entry.kind) && actualAsOf(entry, today());
    });
    const customers = state.customers.filter(customer => allLocal || owners.has(ownerOf(customer)) || customerIds.has(customer.id)).map(customer => {
      const own = ops.filter(op => op.customer_id === customer.id && op.status === 'open');
      return {...customer, opportunity_amount: sum(own, 'amount'), acv_amount: sum(own, 'amount'), opportunity_name: (own[0] || {}).name || '', opportunity_stage: (own[0] || {}).stage_code || '',
        agent_plan: localDataset ? null : {year: yearNow(), segment: customer.quadrant_code, source: 'agent'}, analysis_summary: localDataset ? 'CRM 原始资料，暂无评分' : '按客户资料与象限位置给出'};
    });
    return {contract_version: 4, forecast_basis: 'latest_sales_input', data_source: 'database', scope: personal ? selected.user_id === actor.user_id ? 'self' : 'member' : actor.role === 'manager' && teamGroups.length === 2 ? 'department' : 'team',
      selection: {personal, member_id: personal ? selected.user_id : null, cohort_role: personal ? selected.role : null, team_groups: teamGroups}, generated_at: now(),
      opportunities: ops.filter(o => o.status === 'open'), customers, quarter_forecasts: ops.flatMap(o => (o.quarterly_forecasts || []).map(q => ({...q, opportunity_id: o.id, customer_id: o.customer_id, owner_id: o.owner_id, status: o.status, probability: o.probability, updated_at: o.updated_at || null}))),
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
    return {contract_version: 2, data_source: 'database', scope: selection.personal ? 'peer' : 'company_teams', selection, complete: true, year, quarters: quarters.slice().sort((a, b) => a - b),
      opportunity_acv: bucket(ops, rows => sum(rows, 'amount'), ownerOf), active_opportunities: bucket(ops, rows => rows.length, ownerOf),
      followup: selection.personal?bucket(visits,rows=>rows.length,v=>v.recorder_id):(()=>{const rows=groups(visits,r=>r.length,v=>v.recorder_id).map(r=>{const members=people.filter(p=>groupFor(p.user_id)===r.code).map(p=>({user_id:p.user_id,name:p.display_name,current_member:true,count:visits.filter(v=>v.recorder_id===p.user_id).length}));const n=members.length;return {...r,member_count:n,members,average:n?Math.round(r.record_count/n*100)/100:null,value:n?Math.round(r.record_count/n*100)/100:null,rank:n?r.rank:null};});const rankedRows=ranked(rows.filter(r=>r.member_count));const result=[...rankedRows,...rows.filter(r=>!r.member_count)];return {calculation:'team_followup_per_capita_v1',rows:result,groups:result};})(), partner: partners, partners, partner_acv: partners, region: {rows: regions, groups: regions.map(row => ({...row}))}, generated_at: now()};
  }
  // Explicit preview-only implementation of the workspace contract. These
  // records live under the existing demo storage key and never satisfy live IO.
  const WORKSPACE_ENUMS = {
    customer_source: [['company_lead','公司线索'],['partner_existing','伙伴存量客户'],['marketing_event','市场活动'],['customer_referral','以客带客'],['partner_stock','伙伴压货'],['digital_marketing','数字营销'],['sales_outbound','销售自拓']],
    priority:[['p0','P0']], trial_status:[['preparing','素材准备中'],['requested','申请试用'],['enabled','开通试用']],
    ppl_status:[['created','已创建'],['not_created','未创建']], external_system_status:[['commit','commit 十拿九稳'],['high','High 很有可能'],['medium','Medium 一半赢率'],['low','low 三成把握']],
    fde_support_stage:[['trial_support','试用支持'],['demo_build','Demo 搭建'],['project_delivery','项目落地']]
  };
  const WORKSPACE_INLINE = ['name','product_line','follow_up_plan',...Object.keys(WORKSPACE_ENUMS),'is_key','summary','note','fde_progress'];
  // Fixed Backend e28aef10f2da2fcb1ba9e6a6199dbc534720b3b9 catalog; preview applies only its own synthetic actor capabilities.
  const WORKSPACE_FIELD_CATALOG = [
    {"key":"id","label":"商机ID","type":"text","nullable":false,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","in","contains","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":160},
    {"key":"name","label":"商机名称","type":"text","nullable":false,"read_only":false,"editable":false,"edit_mode":"inline","filter_ops":["eq","ne","in","contains","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":240,"max_length":200},
    {"key":"customer_name","label":"客户名称","type":"text","nullable":false,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","in","contains","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":180},
    {"key":"stage","label":"商机阶段","type":"enum","nullable":false,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":130,"options":[{"value":"identified","label":"意向沟通"},{"value":"qualified","label":"商机确认"},{"value":"solution","label":"方案沟通"},{"value":"proposal","label":"商务谈判"},{"value":"negotiation","label":"客户签约"},{"value":"won","label":"赢单 Won"},{"value":"lost","label":"丢单 Lost"}]},
    {"key":"status","label":"商机状态","type":"enum","nullable":false,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":130,"options":[{"value":"open","label":"在推"},{"value":"won","label":"赢单"},{"value":"lost","label":"输单"}]},
    {"key":"owner","label":"商机销售","type":"person","nullable":true,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":130,"directory":{"source":"scope_options.owners","kind":"person"}},
    {"key":"team","label":"所属团队","type":"person","nullable":true,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":140,"directory":{"source":"scope_options.teams","kind":"team"}},
    {"key":"amount","label":"ACV（元）","type":"money","nullable":true,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["eq","ne","gt","gte","lt","lte","between","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":150},
    {"key":"sales_channel","label":"签单方式","type":"enum","nullable":true,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160,"options":[{"value":"direct","label":"直签"},{"value":"partner","label":"伙伴签约"}]},
    {"key":"partner","label":"所属伙伴","type":"person","nullable":true,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160,"directory":{"url":"/api/v1/directory/partners","kind":"partner"}},
    {"key":"product_line","label":"产品线","type":"text","nullable":true,"read_only":false,"editable":false,"edit_mode":"inline","filter_ops":["eq","ne","in","contains","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160,"max_length":200},
    {"key":"expected_close_date","label":"预计关单日期","type":"date","nullable":true,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["eq","ne","gt","gte","lt","lte","between","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":160},
    {"key":"expected_close_year","label":"预计关单年份","type":"number","nullable":true,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","gt","gte","lt","lte","between","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160},
    {"key":"expected_close_quarter","label":"预计关单季度","type":"number","nullable":true,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","gt","gte","lt","lte","between","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160},
    {"key":"customer_source","label":"客户来源","type":"multi_enum","nullable":false,"read_only":false,"editable":false,"edit_mode":"inline","filter_ops":["has_any","has_all","is_empty","is_not_empty"],"sortable":false,"groupable":false,"width":160,"options":[{"value":"company_lead","label":"公司线索"},{"value":"partner_existing","label":"伙伴存量客户"},{"value":"marketing_event","label":"市场活动"},{"value":"customer_referral","label":"以客带客"},{"value":"partner_stock","label":"伙伴压货"},{"value":"digital_marketing","label":"数字营销"},{"value":"sales_outbound","label":"销售自拓"}]},
    {"key":"priority","label":"优先级","type":"enum","nullable":true,"read_only":false,"editable":false,"edit_mode":"inline","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160,"options":[{"value":"p0","label":"P0"}]},
    {"key":"is_key","label":"是否重点","type":"boolean","nullable":true,"read_only":false,"editable":false,"edit_mode":"inline","filter_ops":["eq","ne","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160},
    {"key":"trial_status","label":"是否开通试用","type":"enum","nullable":true,"read_only":false,"editable":false,"edit_mode":"inline","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160,"options":[{"value":"preparing","label":"素材准备中"},{"value":"requested","label":"申请试用"},{"value":"enabled","label":"开通试用"}]},
    {"key":"summary","label":"一句话总结","type":"text","nullable":true,"read_only":false,"editable":false,"edit_mode":"inline","filter_ops":["eq","ne","in","contains","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":260,"max_length":500},
    {"key":"note","label":"备注","type":"text","nullable":true,"read_only":false,"editable":false,"edit_mode":"inline","filter_ops":["eq","ne","in","contains","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":260,"max_length":5000},
    {"key":"ppl_status","label":"系统是否创建PPL","type":"enum","nullable":true,"read_only":false,"editable":false,"edit_mode":"inline","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160,"options":[{"value":"created","label":"已创建"},{"value":"not_created","label":"未创建"}]},
    {"key":"external_system_status","label":"系统里状态","type":"enum","nullable":true,"read_only":false,"editable":false,"edit_mode":"inline","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160,"options":[{"value":"commit","label":"commit 十拿九稳"},{"value":"high","label":"High 很有可能"},{"value":"medium","label":"Medium 一半赢率"},{"value":"low","label":"low 三成把握"}]},
    {"key":"primary_fde_id","label":"主责FDE","type":"person","nullable":true,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160,"directory":{"url":"/api/v1/directory/opportunity-members?kind=fde","kind":"person"}},
    {"key":"fde_member_ids","label":"长期协助FDE","type":"people","nullable":true,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["has_any","has_all","is_empty","is_not_empty"],"sortable":false,"groupable":false,"width":160,"directory":{"url":"/api/v1/directory/opportunity-members?kind=fde","kind":"person"}},
    {"key":"participants","label":"项目参与人员","type":"people","nullable":true,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["has_any","has_all","is_empty","is_not_empty"],"sortable":false,"groupable":false,"width":160,"directory":{"url":"/api/v1/directory/opportunity-members?kind=all","kind":"person"}},
    {"key":"fde_support_stage","label":"FDE支持阶段","type":"enum","nullable":true,"read_only":false,"editable":false,"edit_mode":"inline","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160,"options":[{"value":"trial_support","label":"试用支持"},{"value":"demo_build","label":"Demo 搭建"},{"value":"project_delivery","label":"项目落地"}]},
    {"key":"fde_progress","label":"FDE进展","type":"text","nullable":true,"read_only":false,"editable":false,"edit_mode":"inline","filter_ops":["eq","ne","in","contains","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":260,"max_length":5000},
    {"key":"follow_up_plan","label":"下一步计划","type":"text","nullable":true,"read_only":false,"editable":false,"edit_mode":"inline","filter_ops":["eq","ne","in","contains","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":260,"max_length":5000},
    {"key":"customer_owner","label":"客户所属销售","type":"person","nullable":true,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160,"directory":{"source":"scope_options.owners","kind":"person"}},
    {"key":"region","label":"所属区域","type":"text","nullable":true,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","in","contains","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160},
    {"key":"creator","label":"创建人","type":"person","nullable":true,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160,"directory":{"url":"/api/v1/directory/opportunity-members?kind=all","kind":"person"}},
    {"key":"business_created_on","label":"商机创建日期","type":"date","nullable":true,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","gt","gte","lt","lte","between","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":160},
    {"key":"latest_followup_at","label":"最近一次跟进时间","type":"datetime","nullable":true,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","gt","gte","lt","lte","between","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":160},
    {"key":"latest_progress","label":"最新进展","type":"text","nullable":true,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","in","contains","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":260},
    {"key":"has_followup_7d","label":"7天内有跟进","type":"boolean","nullable":false,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160},
    {"key":"demo_count","label":"Demo数量","type":"number","nullable":false,"read_only":true,"editable":false,"edit_mode":"none","filter_ops":["eq","ne","gt","gte","lt","lte","between","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":160},
    {"key":"forecast_recognized","label":"期间预测含税确收（元）","type":"money","nullable":true,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["eq","ne","gt","gte","lt","lte","between","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":160},
    {"key":"forecast_collection","label":"期间预测回款（元）","type":"money","nullable":true,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["eq","ne","gt","gte","lt","lte","between","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":160},
    {"key":"collection_confidence","label":"期间回款信心","type":"enum","nullable":true,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["eq","ne","in","is_empty","is_not_empty"],"sortable":true,"groupable":true,"width":160,"options":[{"value":"high","label":"高"},{"value":"low","label":"低"}]},
    {"key":"actual_recognized","label":"期间实际不含税确收（元）","type":"money","nullable":true,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["eq","ne","gt","gte","lt","lte","between","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":160},
    {"key":"actual_collection","label":"期间实际回款（元）","type":"money","nullable":true,"read_only":false,"editable":false,"edit_mode":"panel","filter_ops":["eq","ne","gt","gte","lt","lte","between","is_empty","is_not_empty"],"sortable":true,"groupable":false,"width":160}
  ];
  function workspaceFields(a) {
    return copy(WORKSPACE_FIELD_CATALOG).map(field => ({...field, editable: !field.read_only && (
      ['primary_fde_id','fde_member_ids'].includes(field.key) ? !!a.permissions['opportunity.fde_members'] :
      !['actual_recognized','actual_collection'].includes(field.key) && !!a.permissions['opportunity.update'])}));
  }
  function workspaceOptions(a) {
    const teams = readDirectoryTeams(a,'opportunity.read').map(t=>({id:t.id,name:t.name}));
    const owners = readDirectoryPeople(a,'opportunity.read').map(p=>({id:p.user_id,name:p.display_name,team_ids:p.team_ids}));
    return {definition_version:'opportunity_workspace_v2',fields:workspaceFields(a),participant_roles:[{value:'sales',label:'销售参与者'},{value:'fde',label:'FDE 参与者'},{value:'other',label:'其他参与者'}],
      scope_options:{kinds:[{value:'self',label:'本人'},{value:'authorized',label:'授权范围'}],teams,owners,default:{kind:'authorized'}},
      publish_scopes:a.role === 'manager' ? ['private','team','company'] : ['supervisor','fde_lead'].includes(a.role) ? ['private','team'] : ['private'],teams:['supervisor','manager','fde_lead'].includes(a.role) ? teams.filter(t=>a.role === 'manager' || a.team_ids.includes(t.id)) : [],
      default_config:{schema_version:2,columns:[{key:'name',width:240,pinned:'left'},{key:'customer_name',width:180},{key:'stage',width:150},{key:'owner',width:150},{key:'amount',width:160}],q:'',filters:[],sorts:[{field:'status',direction:'asc'},{field:'expected_close_date',direction:'asc'}],group_by:null,scope:{kind:'authorized'},period:{kind:'current_quarter'},density:'comfortable',wrap:false}};
  }
  function workspaceConfig(a,config) {
    const options=workspaceOptions(a),fields=new Map(options.fields.map(f=>[f.key,f]));
    if(config?.schema_version!==2 || !Array.isArray(config.columns) || !config.columns.length || config.columns.length>60 || config.columns.some(c=>!fields.has(c.key)||c.width<80||c.width>600)||new Set(config.columns.map(c=>c.key)).size!==config.columns.length)error(422,'方案列配置无效');
    if(!Array.isArray(config.filters)||config.filters.length>30||config.filters.some(f=>!fields.get(f.field)?.filter_ops.includes(f.operator)))error(422,'筛选配置无效');
    if(!Array.isArray(config.sorts)||config.sorts.length>5||config.sorts.some(s=>!fields.get(s.field)?.sortable||!['asc','desc'].includes(s.direction))||new Set(config.sorts.map(s=>s.field)).size!==config.sorts.length)error(422,'排序配置无效');
    if(config.group_by&&!fields.get(config.group_by)?.groupable)error(422,'分组字段无效');
    if(!['self','authorized'].includes(config.scope?.kind)||config.scope.kind==='self'&&(config.scope.team_id||config.scope.owner_id))error(422,'查看范围无效');
    if(config.scope.team_id&&!options.scope_options.teams.some(t=>t.id===config.scope.team_id)||config.scope.owner_id&&!options.scope_options.owners.some(p=>p.id===config.scope.owner_id))error(403,'方案查看范围已不可用');
    if(!['current_quarter','fixed'].includes(config.period?.kind)||config.period.kind==='fixed'&&(!Number.isInteger(config.period.year)||config.period.year<2000||config.period.year>2100||![1,2,3,4].includes(config.period.quarter)))error(422,'财务期间无效');
    if(typeof config.q!=='undefined'&&(typeof config.q!=='string'||config.q.length>100))error(422,'搜索最多100字');
    return fields;
  }
  function workspaceRow(a,op,period={year:yearNow(),quarter:Math.ceil(Number(today().slice(5,7))/3)}) {
    const s=getState(),person=(id,name)=>id?{id,name:actors.find(p=>p.user_id===id)?.display_name||name||'未命名成员'}:null;
    const customerRow=s.customers.find(c=>c.id===op.customer_id),visits=s.visits.filter(v=>['confirmed','archived'].includes(v.status)&&(v.opportunity_id===op.id||(v.opportunity_ids||[]).includes(op.id))).sort((a,b)=>String(b.visit_date||b.interaction_at).localeCompare(String(a.visit_date||a.interaction_at)));
    const finance=financialOverview(op,new URLSearchParams({year:period.year,quarter:period.quarter})),forecast=(op.quarterly_forecasts||[]).find(q=>q.year===period.year&&q.quarter===period.quarter);
    const money=v=>v==null?null:String(v),latest=visits[0],seven=new Date(Date.now()-6*86400000+28800000).toISOString().slice(0,10);
    const values={...Object.fromEntries(workspaceFields(a).map(f=>[f.key,op[f.key]??null])),name:op.name,customer_name:op.customer_name||customerRow?.name,stage:op.stage_code||op.stage,status:op.status,amount:money(op.amount),owner:person(op.owner_id,op.owner_name),team:op.team_id?{id:op.team_id,name:op.team_name||directoryTeams(a).find(t=>t.id===op.team_id)?.name||'未命名部门'}:null,partner:op.partner_id?{id:op.partner_id,name:op.partner_name}:null,
      customer_owner:person(customerRow?.owner_id,customerRow?.owner_name),creator:person(op.creator_id,op.creator_name),business_created_on:op.business_created_on||null,region:op.region||null,customer_source:op.customer_source||[],primary_fde_id:person(op.primary_fde_id),fde_member_ids:(op.fde_member_ids||[]).map(id=>({...person(id),is_active:true})),participants:(op.participants||[]).map(p=>({...p,...person(p.user_id),is_active:true})),
      latest_followup_at:latest?.visit_date||latest?.interaction_at||null,latest_progress:latest?.follow_up_record||null,has_followup_7d:visits.some(v=>{const day=dayKey(v.visit_date||v.interaction_at);return day>=seven&&day<=today();}),demo_count:demoRows().filter(d=>d.opportunity_id===op.id&&!d.deleted_at).length,
      forecast_recognized:money(forecast?.recognized_amount),forecast_collection:money(forecast?.collection_amount),collection_confidence:forecast?.collection_confidence||null,actual_recognized:money(finance.totals.recognized.amount),actual_collection:money(finance.totals.collection.amount)};
    const update=objectAllowed(a,'opportunity.update',[op.owner_id],[op.team_id]),fde=objectAllowed(a,'opportunity.fde_members',[op.owner_id],[op.team_id]);
    return {id:op.id,customer_id:op.customer_id,version_no:op.version_no,values,finance_meta:{actual_recognized:finance.totals.recognized,actual_collection:finance.totals.collection},capabilities:{editable_fields:[...(update?WORKSPACE_INLINE.concat('participants','stage','status','amount','sales_channel','partner','expected_close_date','forecast_recognized','forecast_collection','collection_confidence'):[]),...(fde?['primary_fde_id','fde_member_ids']:[])],can_manage_fde_members:fde}};
  }
  function workspaceQuery(a,body) {
    const {config}=body,fields=workspaceConfig(a,config),offset=body.offset||0,limit=body.limit||50;
    if(!Number.isInteger(offset)||offset<0||!Number.isInteger(limit)||limit<1||limit>100)error(422,'分页参数无效');
    const period=config.period.kind==='fixed'?{year:config.period.year,quarter:config.period.quarter}:{year:yearNow(),quarter:Math.ceil(Number(today().slice(5,7))/3)};
    let ops=withTeamRead(a,'GET',()=>visibleOpportunities(a));
    if(config.scope.kind==='self')ops=ops.filter(op=>op.owner_id===a.user_id||(op.fde_member_ids||[]).includes(a.user_id));
    if(config.scope.team_id)ops=ops.filter(op=>op.team_id===config.scope.team_id);
    if(config.scope.owner_id)ops=ops.filter(op=>op.owner_id===config.scope.owner_id);
    let rows=ops.map(op=>workspaceRow(a,op,period));
    if(config.q)rows=rows.filter(row=>[row.values.name,row.values.customer_name,row.values.product_line].some(v=>String(v||'').toLowerCase().includes(config.q.toLowerCase())));
    const scalar=value=>value&&typeof value==='object'?value.id||value.user_id:value;
    for(const f of config.filters)rows=rows.filter(row=>{
      const raw=row.values[f.field],value=Array.isArray(raw)?raw.map(scalar):scalar(raw),missing=raw==null||raw===''||Array.isArray(raw)&&!raw.length;
      if(f.operator==='is_empty'||f.operator==='is_not_empty'){if(Object.hasOwn(f,'value'))error(422,'空值筛选不传值');return f.operator==='is_empty'?missing:!missing;}
      if(f.value==null)error(422,'筛选值不能为空');if(missing)return false;
      const number=['money','number'].includes(fields.get(f.field).type),v=number?Number(value):value,expected=number?Number(f.value):f.value;
      if(f.operator==='eq')return v===expected;if(f.operator==='ne')return v!==expected;if(f.operator==='contains')return String(v).toLowerCase().includes(String(expected).toLowerCase());
      if(f.operator==='in')return f.value.some(x=>(number?Number(x):x)===v);if(f.operator==='has_any')return f.value.some(x=>value.includes(x));if(f.operator==='has_all')return f.value.every(x=>value.includes(x));
      if(f.operator==='gt')return v>expected;if(f.operator==='gte')return v>=expected;if(f.operator==='lt')return v<expected;if(f.operator==='lte')return v<=expected;if(f.operator==='between')return v>=(number?Number(f.value[0]):f.value[0])&&v<=(number?Number(f.value[1]):f.value[1]);return false;
    });
    const compare=(a,b,key,direction='asc')=>{const x=a.values[key],y=b.values[key];if(x==null||y==null)return x==null&&y==null?0:x==null?1:-1;const field=fields.get(key),ordinal=v=>field.options?.findIndex(o=>o.value===v);let result;if(field.options)result=ordinal(x)-ordinal(y);else if(['money','number','boolean'].includes(field.type))result=Number(x)-Number(y);else result=String(x?.name??x).localeCompare(String(y?.name??y),'zh-CN');return direction==='desc'?-result:result;};
    const sorts=config.group_by?[{field:config.group_by,direction:config.sorts.find(s=>s.field===config.group_by)?.direction||'asc'},...config.sorts.filter(s=>s.field!==config.group_by)]:config.sorts;
    rows.sort((a,b)=>{for(const s of sorts){const n=compare(a,b,s.field,s.direction);if(n)return n;}return a.id.localeCompare(b.id);});
    const summarize=items=>{const unknown=items.filter(r=>r.values.amount==null).length,total=items.reduce((n,r)=>n+Number(r.values.amount||0),0);return {count:items.length,amount:unknown?null:String(total),amount_unknown_count:unknown,known_amount_subtotal:String(total)};};
    const groups=[];
    if(config.group_by){const grouped=new Map();for(const row of rows){const value=row.values[config.group_by],key=scalar(value)??null,field=fields.get(config.group_by),label=value?.name||field.options?.find(o=>o.value===value)?.label||(value==null?'未填写':String(value));row.group_key=key;row.group_label=label;if(!grouped.has(key))grouped.set(key,{label,rows:[]});grouped.get(key).rows.push(row);}for(const[key,g]of grouped)groups.push({key,label:g.label,...summarize(g.rows)});}
    return {definition_version:'opportunity_workspace_v2',items:rows.slice(offset,offset+limit),total:rows.length,has_more:offset+limit<rows.length,next_offset:offset+limit<rows.length?offset+limit:null,summary:summarize(rows),groups,resolved_period:{...period,timezone:'Asia/Shanghai'}};
  }
  function workspaceEndpoint(a,path,method,body,p) {
    const s=getState(),scopeKey=a.workspace_id+':'+a.user_id;
    s.opportunityViews ||= [];s.opportunityViewPreferences ||= {};
    const visible=view=>view.workspace_id===a.workspace_id&&!view.deleted_at&&(view.owner_user_ref_id===a.user_id||view.visibility==='company'||view.visibility==='team'&&a.team_ids.includes(view.team_id));
    const decorate=view=>({...view,can_edit:view.owner_user_ref_id===a.user_id,can_delete:view.owner_user_ref_id===a.user_id});
    const preference=()=>{const current=s.opportunityViewPreferences[scopeKey]||{default_view_id:null,version_no:0};const effective=current.default_view_id&&s.opportunityViews.some(v=>v.id===current.default_view_id&&visible(v))?current.default_view_id:null;return {...current,effective_view_id:effective,unavailable_reason:current.default_view_id&&!effective?'unavailable':null};};
    if(path==='/opportunity-views/options'||path==='/opportunities/field-options')return workspaceOptions(a);
    if(path==='/directory/opportunity-members')return pagination(actors.filter(person=>(p.get('kind')!=='fde'||isFde(person))&&(!p.get('q')||(person.display_name+' '+person.account_code).toLowerCase().includes(p.get('q').toLowerCase()))).map(person=>({...member(person),is_active:true,roles:[person.role],teams:person.team_ids.map((id,i)=>({id,name:person.team_names[i]}))})),p);
    if(path==='/opportunity-views/query'&&method==='POST')return workspaceQuery(a,body);
    if(path==='/opportunity-views/preference'){
      if(method==='PUT'){const current=preference();if(body.version_no!==current.version_no)error(409,'默认方案已变化，请重新读取');if(body.default_view_id&&!s.opportunityViews.some(v=>v.id===body.default_view_id&&visible(v)))error(404,'方案不可用');s.opportunityViewPreferences[scopeKey]={default_view_id:body.default_view_id,version_no:current.version_no+1};}
      return preference();
    }
    if(path==='/opportunity-views'&&method==='GET')return pagination(s.opportunityViews.filter(visible).map(decorate),p);
    let match=path.match(/^\/opportunity-views\/([^/]+)$/);
    if(path==='/opportunity-views'&&method==='POST'||match){
      const existing=match?s.opportunityViews.find(v=>v.id===match[1]&&visible(v)):null;
      if(match&&!existing)error(404,'方案不可用');if(method==='GET')return decorate(existing);
      if(existing&&existing.owner_user_ref_id!==a.user_id)error(403,'只能修改本人方案');
      if(existing&&body.version_no!==existing.version_no)error(409,'方案已由其他页面修改，请重新读取');
      if(method==='DELETE'){existing.deleted_at=now();return {ok:true};}
      const options=workspaceOptions(a);
      if(!body.name?.trim()||body.name.length>40||!options.publish_scopes.includes(body.visibility)||body.visibility==='team'&&!options.teams.some(t=>t.id===body.team_id)||body.visibility!=='team'&&Object.hasOwn(body,'team_id'))error(422,'请检查名称和分享范围');
      workspaceConfig(a,body.config);
      const row=existing||{id:uid(40,++s.serial),workspace_id:a.workspace_id,owner_user_ref_id:a.user_id,owner_name:a.display_name,created_at:now(),version_no:0};
      Object.assign(row,{name:body.name.trim(),visibility:body.visibility,team_id:body.visibility==='team'?body.team_id:null,config:copy(body.config),version_no:row.version_no+1,updated_at:now()});if(!existing)s.opportunityViews.push(row);return decorate(row);
    }
    match=path.match(/^\/opportunities\/([^/]+)\/(fields|people)$/);
    if(match){const op=withTeamRead(a,'GET',()=>opportunity(a,match[1])),row=workspaceRow(a,op);if(method==='GET')return row;
      if(body.version_no!==op.version_no)error(409,'商机已由其他页面修改，请重新读取');if(!body.reason?.trim())error(422,'请填写修改说明');
      const values=match[2]==='fields'?body.values:Object.fromEntries(Object.entries(body).filter(([key])=>!['version_no','reason'].includes(key)));
      for(const key of Object.keys(values)){if(!row.capabilities.editable_fields.includes(key))error(403,'当前商机字段不可修改');if(match[2]==='fields'&&!WORKSPACE_INLINE.includes(key)||match[2]==='people'&&!['primary_fde_id','fde_member_ids','participants'].includes(key))error(422,'请使用对应字段维护入口');const field=workspaceFields(a).find(f=>f.key===key),value=values[key];if(field.options&&value!=null&&(Array.isArray(value)?value:[value]).some(v=>!field.options.some(o=>o.value===v)))error(422,'请选择有效选项');if(field.type==='boolean'&&value!=null&&typeof value!=='boolean')error(422,'请选择是否重点');if(field.type==='text'&&value!=null&&(typeof value!=='string'||value.length>field.max_length))error(422,'文本超过长度');}
      const members=values.fde_member_ids||op.fde_member_ids||[],primary=Object.hasOwn(values,'primary_fde_id')?values.primary_fde_id:op.primary_fde_id;
      if(primary&&!members.includes(primary))error(422,'主责须在长期FDE名单中');
      if(values.fde_member_ids?.some(id=>!actors.some(p=>p.user_id===id&&isFde(p))))error(422,'请选择有效FDE');
      if(values.participants?.some(p=>!actors.some(actor=>actor.user_id===p.user_id&&(p.role!=='fde'||isFde(actor)))||!['sales','fde','other'].includes(p.role)))error(422,'请选择有效参与人员和职责');
      Object.assign(op,copy(values));op.version_no++;return workspaceRow(a,op);
    }
    error(501,'演示未实现此商机工作区请求');
  }

  // Synthetic preview counterpart only. Existing snapshots with unknown creator
  // provenance stay unknown; neither owner nor import operator is substituted.
  function creationHistoryEndpoint(actor, path, params) {
    readGrants(actor, 'opportunity.read');
    const teams = readDirectoryTeams(actor, 'opportunity.read'), teamId = params.get('team_id'), memberId = params.get('member_id');
    if (teamId && !teams.some(team => team.id === teamId)) error(403, '无权查看该部门');
    const customerIds = new Set(visibleCustomers(actor).map(row => row.id));
    const rows = visibleOpportunities(actor).map(op => {
      const historical = op.import_meta?.import_type === 'crm_history';
      const creatorId = historical ? null : op.creator_id || op.created_by_user_ref_id || null;
      const creator = actors.find(person => person.user_id === creatorId);
      return {id: op.id, customer_id: op.customer_id, name: op.name, customer_name: op.customer_name || null,
        creator_id: creatorId, creator_name: creatorId ? creator?.display_name || op.creator_name || '成员' : null,
        business_created_on: op.business_created_on || (!historical && op.created_at ? dayKey(op.created_at) : null),
        created_at: historical ? null : op.created_at || null, owner_name: op.owner_name || null,
        status: op.status, stage: op.stage_code, can_read_customer: customerIds.has(op.customer_id)};
    });
    const creators = [...new Set(rows.map(row => row.creator_id).filter(Boolean))].map(id => {
      const person = actors.find(person => person.user_id === id), row = rows.find(row => row.creator_id === id);
      return {id, name: person?.display_name || row.creator_name, is_active: person?.is_active !== false, team_ids: (person?.team_ids || []).filter(id => teams.some(team => team.id === id))};
    });
    if (memberId && !creators.some(person => person.id === memberId && (!teamId || person.team_ids.includes(teamId)))) error(403, '无权查看该创建人');
    const offset = Number(params.get('offset') || 0), limit = Number(params.get('limit') || (path.endsWith('/options') ? 100 : 50)), q = (params.get('q') || '').trim().toLocaleLowerCase();
    if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isInteger(limit) || limit < 1 || limit > 100 || q.length > 100) error(422, '创建历史筛选无效');
    const page = items => ({total: items.length, has_more: offset + limit < items.length, next_offset: offset + limit < items.length ? offset + limit : null});
    if (path.endsWith('/options')) {
      const selected = creators.filter(person => (!teamId || person.team_ids.includes(teamId)) && (!q || person.name.toLocaleLowerCase().includes(q))).sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
      return {teams, members: selected.slice(offset, offset + limit), ...page(selected), default_scope: 'self', team_basis: 'current_creator_membership'};
    }
    const scope = params.get('scope') || 'self', from = params.get('created_from'), to = params.get('created_to');
    if (!['self', 'team'].includes(scope) || scope === 'self' && (memberId || teamId) || [from, to].some(day => day && (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !Number.isFinite(Date.parse(day)) || new Date(day).toISOString().slice(0, 10) !== day)) || from && to && from > to) error(422, '创建历史筛选无效');
    const selected = rows.filter(row => (scope !== 'self' || row.creator_id === actor.user_id) && (!memberId || row.creator_id === memberId) && (!teamId || creators.some(person => person.id === row.creator_id && person.team_ids.includes(teamId))) &&
      (!from || row.business_created_on && row.business_created_on >= from) && (!to || row.business_created_on && row.business_created_on <= to) && (!q || [row.name, row.customer_name, row.creator_name].some(value => String(value || '').toLocaleLowerCase().includes(q))))
      .sort((a, b) => String(b.business_created_on || '').localeCompare(String(a.business_created_on || '')) || a.id.localeCompare(b.id));
    return {items: selected.slice(offset, offset + limit), ...page(selected)};
  }

  function dispatch(options, teamRead = false) {
    if (global.SALES_MODE !== 'preview') error(403, '工作区尚未启用');
    const url = new URL(options.url, 'http://preview.invalid'), path = decodeURIComponent(url.pathname.replace(/^.*\/api\/v1(?=\/|$)/, '') || '/'), p = url.searchParams;
    const method = String(options.method || 'GET').toUpperCase(), body = options.data && typeof options.data === 'object' ? options.data : {};
    if (method === 'GET' && options.data) Object.entries(body).forEach(([key, value]) => p.set(key, String(value)));
    const s = getState();
    if (path === '/auth/company-entry') {const code=String(p.get('company_code') || '').trim().toLowerCase();if(!['demo','demo2'].includes(code))error(404,'公司码不存在');return {company_code:code,company_name:code==='demo'?'SalesBuddy 演示公司':'第二演示公司'};}
    if (path === '/auth/password/login' && method === 'POST') { const role = String(body.account_code || '').replace(/^PREVIEW_/i, '').toLowerCase(); if (!ROLES.includes(role)) error(401, '账号或密码不正确'); const result=auth(role);const code=body.workspace || body.company_code || 'demo';result.actor.company_code=code;result.actor.company_name=code==='demo2'?'第二演示公司':'SalesBuddy 演示公司';if(code==='demo2')result.actor.workspace_id=uid(2,2);return result; }
    if (path === '/auth/refresh' && method === 'POST') {const token = String(body.refresh_token || ''), role = token.replace(/^preview-refresh-/, ''); if (!token.startsWith('preview-refresh-') || !ROLES.includes(role)) error(401, '会话已失效，请重新登录'); return auth(role);}
    const a = session(options);
    if (!teamRead) return withTeamRead(a, method, () => dispatch(options, true));
    if (/^\/visit-imports\/requests\/[^/]+$/.test(path) && method === 'GET') {
      requireCap(a, 'visit.create'); const receipt = s.previewUploadReceipts?.[a.user_id + ':' + path.split('/').at(-1)];
      if (!receipt) error(404, '本次文件未被演示环境接收', 'UPLOAD_INTENT_NOT_FOUND'); return receipt;
    }
    if (path === '/auth/me' && method === 'GET') return {actor: a};
    if (path === '/auth/permissions' && method === 'GET') return {permissions: a.permissions, grants: a.permission_grants, client_export_permissions: a.client_export_permissions, permission_version: a.permission_version};
    if (path === '/auth/logout' && method === 'POST') return {ok: true};
    if (path === '/auth/password' && method === 'POST') error(501, '当前环境不支持修改密码');
    if (method === 'GET' && ['/opportunities/creation-history', '/opportunities/creation-history/options'].includes(path)) return creationHistoryEndpoint(a, path, p);
    const workspaceRoute = path.startsWith('/opportunity-views') || path === '/opportunities/field-options' || path === '/directory/opportunity-members' || /^\/opportunities\/[^/]+\/(fields|people)$/.test(path);
    if (workspaceRoute && (method === 'GET' || path === '/opportunity-views/query')) return workspaceEndpoint(a,path,method,body,p);
    if (method !== 'GET') {
      const idem = options.header && options.header['Idempotency-Key'];
      if (workspaceRoute && !idem) error(422, '商机工作区修改需要提交标识');
      const key = idem && [a.user_id, method, path, idem].join(':');
      if (key && s.idempotency[key]) {if (s.idempotency[key].body !== JSON.stringify(body)) error(409, '提交标识已用于其他内容'); return s.idempotency[key].response;}
      const result = workspaceRoute ? workspaceEndpoint(a,path,method,body,p) : mutate(a, path, method, body, p);
      if (key) s.idempotency[key] = {body: JSON.stringify(body), response: copy(result)};
      save(); return result;
    }
    if (path === '/assistant/home') {
      const customerIds = new Set(visibleCustomers(a).map(c => c.id)), tasks = taskRows(a);
      const records = s.visits.filter(v => customerIds.has(v.customer_id)).slice(0, 3).map(v => localDataset ? {...v, archived_at: null, completed_count: ['customer_name', 'customer_type', 'opportunity_name', 'recorder_name', 'visit_date', 'created_date', 'contact_name_snapshot', 'follow_up_record', 'next_action'].filter(key => v[key] != null && v[key] !== '').length, total_count: 9, score: null, grade: '未评分'} : {...v, archived_at: v.created_at, interaction_mode: '线上会议', completed_count: 8, total_count: 8, score: null, grade: '暂未评分'});
      return {archived_visits: records, display_policy: {definition: {message_order: 'desc'}}, team_summary: {overdue: tasks.filter(t => !['completed', 'cancelled'].includes(t.status) && t.due_at < now()).length, claim: tasks.filter(t => t.status === 'pending_confirm').length, handover: tasks.filter(t => t.handover_required).length}, message: LABEL};
    }
    if (path === '/company-rules/presentation') return {opportunity_amount:{definition:{schema_version:1,warning_threshold_wan:1000}}};
    if (/^\/tasks\/[^/]+\/reassignment-options$/.test(path)) {requireCap(a,'task.coordinate');const id=path.split('/')[2];const row=taskRows(a).find(t=>t.id===id);if(!row)error(404,'任务不存在');return {items:actors.filter(p=>!row.assignees.some(o=>o.user_id===p.user_id)).map(member),teams:directoryTeams(a),defaults:{team_id:a.team_ids[0] || null}};}
    if (path === '/customers/claim-pool/options') return {industries:[{value:'',label:'全部行业'},...BUSINESS_OPTIONS.customer.industry.map(v=>({value:v,label:v}))],claim_statuses:[{value:'',label:'全部状态'},...['unclaimed','claimed','pending'].map((v,i)=>({value:v,label:['未认领','已认领','审批中'][i]}))]};
    if (path === '/metadata/business-options') return copy(BUSINESS_OPTIONS);
    if (path === '/directory/teams') {if (p.get('purpose') && !['browse','dashboard','profile','fde','assignment'].includes(p.get('purpose'))) error(422, '团队目录用途无效'); return {data_source: 'database', teams: readDirectoryTeams(a, p.get('permission') || 'directory.read'),defaults:{team_id:a.team_ids[0] || null}};}
    if (path === '/directory/members') {const permission = p.get('permission') || 'directory.read'; if (!PREVIEW_READS.includes(permission)) error(422, '目录仅接受业务读取操作'); return {items: readDirectoryPeople(a, permission).map(member), teams: readDirectoryTeams(a, permission), defaults: {team_id: a.team_ids[0] || null}};}
    if (path === '/directory/task-assignees' || path === '/directory/colleagues') return {items: directoryPeople(a).filter(person => path !== '/directory/colleagues' || !isFde(person)).map(member), teams: directoryTeams(a), defaults:{team_id:a.team_ids[0] || null}};
    if (path === '/tasks/recipients') {requireCap(a, 'task.create'); const q = (p.get('q') || '').toLowerCase(); return pagination(actors.map(member).filter(person => (person.name + person.account_code).toLowerCase().includes(q)), p,{teams:directoryTeams(a),defaults:{team_id:a.team_ids[0] || null}});}
    if (path === '/tasks/customers' || path === '/tasks/opportunities') {
      requireCap(a, 'task.create'); const q = (p.get('q') || '').toLowerCase();
      const ops = visibleOpportunities(a);
      if (path === '/tasks/customers') {const ids = new Set(ops.map(op => op.customer_id)); return pagination(visibleCustomers(a).filter(c => ids.has(c.id) && c.name.toLowerCase().includes(q)).map(c => ({id: c.id, name: c.name, customer_id: null, customer_name: null})), p);}
      if (!p.get('customer_id')) error(422, '任务商机选择必须指定客户');
      return pagination(ops.filter(op => op.customer_id === p.get('customer_id') && (!p.get('opportunity_id') || op.id === p.get('opportunity_id')) && op.name.toLowerCase().includes(q)).map(op => ({id: op.id, name: op.name, customer_id: op.customer_id, customer_name: op.customer_name})), p);
    }
    if (path === '/dashboard/options') return {members: directoryPeople(a).map(member), team_groups: a.capabilities['team.view'] ? directoryTeams(a).map(team => ({...team, code: 'team:' + team.id, kind: 'team'})) : [], teams: directoryTeams(a),defaults:{team_id:a.team_ids[0] || null}};
    if (path === '/profile/scope-options' || path === '/fde/scope-options') {
      if (path.startsWith('/fde/') && !isFde(a)) error(403, '当前身份没有 FDE 范围');
      const people = directoryPeople(a);
      return {data_source: 'database', members: people.map(person => ({...member(person), team_ids: person.team_ids})), teams: directoryTeams(a),
        allowed_scopes: a.role === 'manager' ? ['self', 'person', 'team', 'department'] : a.capabilities['team.view'] ? ['self', 'person', 'team'] : ['self'], defaults: {scope: a.role === 'manager' ? 'department' : 'self', member_id: a.user_id, team_id: null}};
    }
    if (path === '/directory/fde-members') return pagination(actors.filter(isFde).map(member), p);
    if (path === '/directory/task-positions') return {items: [{code: 'self', label: '本人', candidate_count: 1, available: true}, {code: 'fde', label: 'FDE 岗位', candidate_count: 1, available: true}]};
    if (path === '/directory/partners') {readGrants(a, 'partner.read'); return pagination(previewPartners(s).filter(r => r.name.includes(p.get('q') || '')), p);}
    // Synthetic preview of the portfolio contract. Live requests never enter this provider.
    if (path === '/customers/portfolio') {
      const grants = action => Boolean(a.permissions?.[action] && !a.permission_grants.some(g => g.permission_code === action && g.effect === 'deny'));
      readGrants(a, 'customer.read');
      const availability = {activity: grants('visit.read'), opportunity: grants('opportunity.read'), map: grants('battle_map.read'), pool: grants('customer.claim_directory')};
      const selectedScope = scopeSelection(a, p, 'customer.read');
      const from = p.get('activity_start') || yearNow() + '-01-01', to = [p.get('activity_end') || today(), today()].sort()[0];
      const segment = p.get('segment') || 'claimed', threshold = Number(p.get('min_probability') || 10);
      if (segment === 'active' && !availability.activity || ['pipeline', 'won'].includes(segment) && !availability.opportunity) error(403, '当前账号没有该客户范围的查看权限');
      if ((!availability.opportunity && (p.get('plan') || p.has('amount_min') || p.has('amount_max'))) || (!availability.map && (p.get('quadrant') || p.get('unscored') === 'true'))) error(403, '当前账号没有该筛选条件的查看权限');
      const scored = row => row.potential_score != null && row.relationship_score != null;
      const availableOps = availability.opportunity ? visibleOpportunities(a) : [];
      const visits = availability.activity ? s.visits.filter(v => ['confirmed', 'archived'].includes(v.status) && !v.deleted_at &&
        readable(a, 'visit.read', [v.recorder_id], actors.find(person => person.user_id === v.recorder_id)?.team_ids || []) &&
        dayKey(v.visit_date || v.interaction_at) && dayKey(v.visit_date || v.interaction_at) <= today()) : [];
      let base = visibleCustomers(a).filter(row =>
        (row.business_identities || ['customer']).includes('customer') &&
        (p.get('business_identity') !== 'partner' || (row.business_identities || []).includes('partner')) &&
        (!selectedScope.teamId || row.owner_team_id === selectedScope.teamId) &&
        (!selectedScope.ids.length || selectedScope.ids.includes(row.owner_id)) &&
        (p.get('scope') !== 'self' || row.owner_id === a.user_id)).map(row => {
          const teams = [row.owner_team_id, ...(actors.find(person => person.user_id === row.owner_id)?.team_ids || [])].filter(Boolean);
          const activity_readable = objectAllowed(a, 'visit.read', [row.owner_id], teams);
          const opportunity_readable = objectAllowed(a, 'opportunity.read', [row.owner_id], teams);
          const map_readable = objectAllowed(a, 'battle_map.read', [row.owner_id], teams);
          const ops = availableOps.filter(op => op.customer_id === row.id), current = ops.filter(op => op.status === 'open');
          const followups = visits.filter(v => v.customer_id === row.id || availableOps.some(op => op.customer_id === row.id && [v.opportunity_id, ...(v.opportunity_ids || [])].includes(op.id)));
          const planYears = current.map(op => Number(String(op.expected_close_date || '').slice(0, 4)) || Number(op.expected_close_year) || null);
          const unknown_amount_count = current.filter(op => op.amount == null || !Number.isFinite(Number(op.amount)) || Number(op.amount) < 0).length;
          return {...row, business_identities: row.business_identities || ['customer'], owner_name: row.owner_name || actors.find(person => person.user_id === row.owner_id)?.display_name || '',
            ownership_state: row.ownership_state || (row.owner_id ? 'claimed' : 'unclaimed'), can_open: true,
            activity_readable, opportunity_readable, map_readable,
            max_open_probability: opportunity_readable && current.length ? Math.max(...current.map(op => Number(op.probability || 0))) : null,
            open_count: opportunity_readable ? current.length : null, has_won: opportunity_readable ? ops.some(op => op.status === 'won') : null,
            is_active: activity_readable ? followups.some(v => dayKey(v.visit_date || v.interaction_at) >= from && dayKey(v.visit_date || v.interaction_at) <= to) : null,
            latest_visit_at: activity_readable ? followups.map(v => v.visit_date || v.interaction_at).sort().at(-1) || null : null,
            acv_amount: opportunity_readable && !unknown_amount_count ? sum(current, 'amount') : null,
            unknown_amount_count: opportunity_readable ? unknown_amount_count : null,
            plan_current_year: opportunity_readable ? planYears.includes(yearNow()) : null,
            plan_long_term: opportunity_readable ? !planYears.includes(yearNow()) && planYears.some(year => year > yearNow()) : null,
            quadrant_code: map_readable ? row.quadrant_code : null,
            potential_score: map_readable ? row.potential_score ?? null : null, relationship_score: map_readable ? row.relationship_score ?? null : null};
        });
      const q = String(p.get('q') || '').toLowerCase();
      base = base.filter(row => (!q || [row.name, row.owner_name].some(value => String(value || '').toLowerCase().includes(q))) &&
        (!p.get('quadrant') || row.quadrant_code === p.get('quadrant')) && (!p.get('level') || row.level_code === p.get('level')) &&
        (!p.has('amount_min') || row.acv_amount != null && row.acv_amount >= Number(p.get('amount_min'))) &&
        (!p.has('amount_max') || row.acv_amount != null && row.acv_amount <= Number(p.get('amount_max'))) &&
        (!p.get('plan') || (p.get('plan') === 'current_year' ? row.plan_current_year : row.plan_long_term)) &&
        (p.get('unscored') !== 'true' || row.map_readable && !scored(row)));
      const claimed = base.filter(row => row.ownership_state === 'claimed');
      const summary = {claimed: claimed.length,
        active: availability.activity ? claimed.filter(row => row.is_active).length : null,
        won: availability.opportunity ? claimed.filter(row => row.has_won).length : null, pool_total: availability.pool ? s.customers.length : null,
        activity_unavailable_count: claimed.filter(row => !row.activity_readable).length,
        opportunity_unavailable_count: claimed.filter(row => !row.opportunity_readable).length};
      for (const n of [10, 30, 50, 70, 90]) summary['pipeline_ge_' + n] = availability.opportunity ? claimed.filter(row => row.max_open_probability >= n).length : null;
      const selected = claimed.filter(row => segment === 'active' ? row.is_active : segment === 'pipeline' ? row.max_open_probability >= threshold : segment === 'won' ? row.has_won : true)
        .sort((x, y) => String(y.latest_visit_at || '').localeCompare(String(x.latest_visit_at || '')) || x.name.localeCompare(y.name) || x.id.localeCompare(y.id));
      const offset = Math.max(0, Number(p.get('offset') || 0)), limit = Math.max(1, Math.min(100, Number(p.get('limit') || 20)));
      return {filters_applied: Object.fromEntries(p), summary, availability, items: selected.slice(offset, offset + limit), total: selected.length, offset, limit,
        map_points: selected.filter(row => row.map_readable && scored(row)), unscored_count: availability.map ? selected.filter(row => row.map_readable && !scored(row)).length : null,
        map_unavailable_count: selected.filter(row => !row.map_readable).length};
    }
    if (path === '/customers/requests/history') {
      const scope = p.get('scope') || 'self';
      if (!['self', 'team'].includes(scope) || p.get('kind') && !['claim', 'creation'].includes(p.get('kind')) || p.get('status') && !['pending', 'approved', 'rejected', 'cancelled'].includes(p.get('status'))) error(422, '申请筛选无效');
      const selection = scopeSelection(a, p, 'customer.read');
      const people = scope === 'self' ? [a.user_id] : selection.people.map(person => person.user_id);
      const readableIds = new Set(visibleCustomers(a).map(row => row.id));
      const rows = s.claims.map(row => {
        const c = s.customers.find(customer => customer.id === row.customer_id);
        return {...row, kind: 'claim', customer_name: c?.name || '客户资料未授权', requested_at: row.requested_at || row.created_at,
          reviewer_name: row.reviewer_name || null, reviewed_at: row.reviewed_at || null, decision_reason: row.decision_reason || null, can_read_customer: readableIds.has(row.customer_id)};
      }).filter(row => people.includes(row.applicant_user_id) && (!p.get('kind') || row.kind === p.get('kind')) &&
        (!p.get('q') || [row.customer_name, row.applicant_name, row.id].some(value => String(value || '').includes(p.get('q')))))
        .sort((a, b) => String(b.requested_at).localeCompare(String(a.requested_at)) || b.id.localeCompare(a.id));
      const summary = Object.fromEntries(['pending', 'approved', 'rejected', 'cancelled'].map(status => [status, rows.filter(row => row.status === status).length]));
      return pagination(rows.filter(row => !p.get('status') || row.status === p.get('status')), p, {summary});
    }
    if (path === '/customers' || path === '/customer-assets/map' || path === '/customers/claim-pool') {
      if (path === '/customers/claim-pool') readGrants(a, 'customer.claim_directory');
      let rows = path === '/customers/claim-pool' ? s.customers : scopedCustomers(a, p);
      rows = rows.filter(c => (!['customer','partner'].includes(p.get('business_identity')) || (c.business_identities || ['customer']).includes(p.get('business_identity'))) && (path !== '/customer-assets/map' || (c.business_identities || ['customer']).includes('customer')));
      if(path==='/customer-assets/map'){const ids=new Set((activeVisits(p)||s.visits.filter(v=>['confirmed','archived'].includes(v.status)&&dayKey(v.visit_date || v.interaction_at)>=yearNow()+'-01-01'&&dayKey(v.visit_date || v.interaction_at)<=today())).map(v=>v.customer_id));rows=rows.filter(c=>ids.has(c.id));}
      if (p.get('q')) rows = rows.filter(c => c.name.includes(p.get('q'))); if (p.get('unassigned') === 'true') rows = rows.filter(c => !c.owner_id); if (p.get('level')) rows = rows.filter(c => c.level_code === p.get('level'));
      rows = rows.map(c => {const claim = s.claims.find(claim => claim.customer_id === c.id && claim.applicant_user_id === a.user_id); return {...enrichCustomer(c, a), can_read_customer: objectAllowed(a, 'customer.read', [c.owner_id], [c.owner_team_id]), claim_status: claim ? claim.status : c.owner_id ? 'claimed' : 'unclaimed', claimed: c.owner_id === a.user_id, can_claim: !c.owner_id && c.ownership_state !== 'legacy_review' && (!claim || claim.status === 'rejected'), already_claimed: Boolean(c.owner_id)};});
      // 作战地图的「最近沟通」取该客户最近一条已确认 / 已归档跟进，与活跃筛选同一口径（种子值只作兜底）
      if (path === '/customer-assets/map') {const latest = new Map(); for (const v of s.visits) {if (!['confirmed', 'archived'].includes(v.status)) continue; const at = v.visit_date || v.interaction_at, day = dayKey(at); if (!day || day > today()) continue; if (!latest.has(v.customer_id) || Date.parse(at) > Date.parse(latest.get(v.customer_id))) latest.set(v.customer_id, at);}
        rows = rows.map(c => latest.has(c.id) && !(Date.parse(c.latest_visit_at) >= Date.parse(latest.get(c.id))) ? {...c, latest_visit_at: latest.get(c.id)} : c);}
      return path === '/customer-assets/map' ? {items: rows, total: rows.length, summary: {customer_count: rows.length, acv_amount: sum(rows, 'acv_amount')}, activity_since:yearNow()+'-01-01', as_of: today()} : pagination(rows, p);
    }
    let match;
    if (path === '/material-product-lines') return {items: (s.material_product_lines || []).filter(row => row.is_active !== false), version_no: 0};
    if (path === '/materials') {
      if (p.get('status') === 'all') error(403, '销售仅能读取启用物料目录');
      const q = (p.get('q') || '').toLowerCase();
      return pagination((s.materials || []).filter(row => row.is_active !== false && (!q || (row.name + ' ' + row.code).toLowerCase().includes(q)) && (!p.get('product_line_id') || row.product_line_id === p.get('product_line_id'))), p);
    }
    if ((match = path.match(/^\/opportunities\/([^/]+)\/materials$/))) {
      const op = opportunity(a, match[1]);
      return {opportunity_id: op.id, version_no: op.version_no, trade_relation: op.trade_relation || null, upstream_opportunity_id: op.upstream_opportunity_id || null, items: op.material_lines || [], retired_items: op.retired_material_lines || []};
    }
    if ((match = path.match(/^\/opportunities\/([^/]+)\/financial-overview$/))) return financialOverview(opportunity(a, match[1]), p);
    if (path === '/targets') {const context = targetContext(a, Object.fromEntries(p)), values = targetState(context); return {...context, ...values, pending_requests: [], data_source: 'database'};}
    if ((match = path.match(/^\/opportunities\/([^/]+)\/demo-scenes$/))) {requireCap(a, 'opportunity.read'); const op = opportunity(a, match[1]); return pagination(demoRows().filter(row => row.opportunity_id === op.id && !row.deleted_at).map(row => sceneView(row, a)), p, {editable: scenePermission(a, op), data_source: 'database'});}
    if ((match = path.match(/^\/demo-scenes\/([^/]+)(?:\/(history))?$/))) {
      const row = demoRows().find(row => row.id === match[1] && !row.deleted_at); if (!row) error(404, '场景不存在'); requireCap(a, 'opportunity.read');
      const view = sceneView(row, a); return match[2] ? pagination(row.history || [], p) : view;
    }
    if ((match = path.match(/^\/customers\/([^/]+)\/opportunities\/check-name$/))) {customer(a, match[1]); const duplicate = s.opportunities.some(o => o.customer_id === match[1] && o.name === String(p.get('name') || '').trim() && o.id !== p.get('exclude_id')); return {available: !duplicate, duplicate, exists: duplicate, message: duplicate ? '当前客户下已存在同名商机' : '名称可用'};}
    if ((match = path.match(/^\/customers\/([^/]+)\/opportunities\/([^/]+)\/(header|overview)$/))) {const op = opportunity(a, match[2]); if (op.customer_id !== match[1]) error(404, '商机不属于该客户'); return customerDetail(a, match[1], match[2], match[3]);}
    if ((match = path.match(/^\/customers\/([^/]+)\/(header|overview|reference)$/))) return match[2] === 'reference' ? customer(a, match[1]) : customerDetail(a, match[1], null, match[2]);
    if ((match = path.match(/^\/customers\/([^/]+)\/(opportunities|contacts)$/))) {customer(a, match[1]); return pagination(match[2] === 'contacts' ? s.contacts.filter(c => c.customer_id === match[1]) : visibleOpportunities(a).filter(o => o.customer_id === match[1] && (!p.get('q') || o.name.includes(p.get('q')))).map(enrichOpportunity), p);}
    if ((match = path.match(/^\/customers\/([^/]+)$/))) return customerDetail(a, match[1], null, 'detail');
    if (path === '/opportunities') {
      let rows = filteredOpportunities(a, p);
      const active = activeVisits(p);
      if (active) {const ids = new Set(active.flatMap(v => [v.opportunity_id, ...(v.opportunity_ids || [])]).filter(Boolean)); rows = rows.filter(o => ids.has(o.id));}
      if (p.get('order') === 'quarter_stage') {
        const year = row => Number(String(row.expected_close_date || '').slice(0, 4)) || row.expected_close_year || Infinity;
        const quarter = row => Math.ceil(Number(String(row.expected_close_date || '').slice(5, 7)) / 3) || row.expected_close_quarter || Infinity;
        const compare = (x, y) => x === y ? 0 : x < y ? -1 : 1;
        rows.sort((x, y) => Number(y.status === 'open') - Number(x.status === 'open') || compare(year(x), year(y)) || compare(quarter(x), quarter(y)) || compare(y.probability ?? -Infinity, x.probability ?? -Infinity) || compare(x.expected_close_date || '9999', y.expected_close_date || '9999') || String(x.id).localeCompare(String(y.id)));
      }
      const extra = {};
      // 商机页的筛选统计（opportunity_filtered_overview_v1）：口径与列表同一组筛选结果
      if (p.get('include_overview') === 'true') {
        const year = Number(p.get('overview_year') || yearNow()), quarters = [...new Set(p.getAll('overview_quarters').map(Number))].sort();
        const selected = value => {
          const stamp = Date.parse(value), day = Number.isFinite(stamp) ? new Date(stamp + 28800000).toISOString().slice(0, 10) : '';
          return !quarters.length || Boolean(day) && Number(day.slice(0, 4)) === year && quarters.includes(Math.ceil(Number(day.slice(5, 7)) / 3));
        };
        const followedIds = new Set(s.visits.filter(visit => !visit.deleted_at && ['confirmed', 'archived'].includes(visit.status) && selected(visit.interaction_at || visit.visit_date)).flatMap(visit => visit.opportunity_id ? [visit.opportunity_id] : visit.opportunity_ids || []).filter(Boolean));
        extra.overview = {definition_version: 'opportunity_filtered_overview_v1', year, quarters, metrics: {total: rows.length, active: rows.filter(o => o.status !== 'lost' && followedIds.has(o.id)).length,
          won: rows.filter(o => o.status === 'won' && selected(o.won_at || o.actual_close_date)).length, newCount: rows.filter(o => o.created_at && selected(o.created_at)).length,
          missingCloseDates: quarters.length ? rows.filter(o => o.status !== 'lost' && !o.expected_close_date && !o.expected_close_year).length : 0, missingWonDates: quarters.length ? rows.filter(o => o.status === 'won' && !o.won_at && !o.actual_close_date).length : 0,
          missingCreatedDates: rows.filter(o => !o.created_at).length, demo_scene_count: demoRows().filter(scene => !scene.deleted_at && selected(scene.created_at) && rows.some(op => op.id === scene.opportunity_id)).length}};
      }
      return pagination(rows, p, {...extra, summary: {total: rows.length, open_amount: sum(rows.filter(o => o.status === 'open'), 'amount')}, team_options: directoryTeams(a),owner_options:directoryPeople(a).map(member),defaults:{team_id:a.team_ids[0] || null}, facets: {teams: uniq(rows.map(o => o.team_name)), owners: uniq(rows.map(o => o.owner_name)), product_lines: uniq(rows.map(o => o.product_line))}});
    }
    if (path === '/opportunities/overview') {
      const all = visibleOpportunities(a, p), quarters = p.getAll('quarters').map(Number), year = Number(p.get('year') || yearNow());
      const selected = value => Boolean(value) && Number(String(value).slice(0, 4)) === year && (!quarters.length || quarters.includes(Math.ceil(Number(String(value).slice(5, 7)) / 3)));
      const unfilteredCrm = localDataset && !quarters.length;
      const rows = unfilteredCrm ? all : all.filter(o => selected(o.expected_close_date));
      const metrics = {total: rows.length, active: rows.filter(o => o.status === 'open').length,
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
      return {metrics, total: rows.length, summary: {total: rows.length, open_amount: sum(rows.filter(o => o.status === 'open'), 'amount')}, product_lines: uniq(rows.map(o => o.product_line))};
    }
    if ((match = path.match(/^\/opportunities\/([^/]+)\/(header|overview|detail)$/))) {const op = opportunity(a, match[1]); return customerDetail(a, op.customer_id, op.id, match[2]);}
    if ((match = path.match(/^\/opportunities\/([^/]+)\/timeline$/))) {const op = opportunity(a, match[1]), events = s.opportunityEvents.filter(e => e.opportunity_id === op.id); return pagination(events.length ? events : [{id: uid(17, 1), key: uid(17, 1), at: op.created_at, type: 'created', title: '商机已创建', summary: '商机推进时间线起点', actor_name: op.owner_name, detail: '商机建档', description: '商机建档'}], p);}
    if (path === '/tasks/overview') {const rows = taskRows(a), ids = p.getAll('task_ids'); return {items: (ids.length ? rows.filter(t => ids.includes(t.id)) : rows.slice(0, 20)).map(t => taskResponse(t, a)), metrics: {today_completed: rows.filter(t => t.status === 'completed' && (t.completed_at || '').slice(0, 10) === today()).length, today_pending: rows.filter(t => !['completed', 'cancelled'].includes(t.status) && t.due_at.slice(0, 10) === today()).length, all_pending: rows.filter(t => !['completed', 'cancelled'].includes(t.status)).length}};}
    if (path === '/tasks/management' || path === '/tasks/management/owners') {
      const filters = new URLSearchParams(p);
      filters.set('view', p.get('scope') === 'mine' ? 'self' : 'team');
      let rows = taskRows(a, filters);
      if (p.get('date_from') || p.get('date_to')) rows = rows.filter(t => {
        const key = dayKey(t.status === 'completed' ? t.completed_at : t.due_at);
        return !key ? p.get('undated') !== 'exclude' : (!p.get('date_from') || key >= p.get('date_from')) && (!p.get('date_to') || key <= p.get('date_to'));
      });
      const summary = {total: rows.length, pending_count: rows.filter(t => !['completed','cancelled'].includes(t.status)).length,
        completed_count: rows.filter(t => t.status === 'completed').length, cancelled_count: rows.filter(t => t.status === 'cancelled').length,
        overdue_count: rows.filter(t => !['completed','cancelled'].includes(t.status) && t.due_at && Date.parse(t.due_at) < Date.now()).length};
      if (p.get('status')) rows = rows.filter(t => t.status === p.get('status'));
      if (path.endsWith('/owners')) return pagination(readDirectoryPeople(a, 'task.read').map(person => ({...member(person), task_count: rows.filter(t => t.assignees.some(p => p.user_id === person.user_id && p.responsibility === 'owner')).length})), p);
      return pagination(rows.map(row => taskResponse(row, a)), p, {summary});
    }
    if (path === '/tasks') {
      const all = taskRows(a, p); let rows = all; const tab = p.get('tab'), status = p.get('status');
      if (tab === 'pending') rows = rows.filter(t => !['completed', 'cancelled'].includes(t.status)); if (tab === 'completed') rows = rows.filter(t => t.status === 'completed'); if (tab === 'rejected') rows = rows.filter(t => t.status === 'cancelled'); if (status) rows = rows.filter(t => t.status === status);
      const overview = p.get('overview'); if (overview === 'today_completed') rows = rows.filter(t => t.status === 'completed' && (t.completed_at || '').slice(0, 10) === today()); if (overview === 'today_pending') rows = rows.filter(t => !['completed', 'cancelled'].includes(t.status) && t.due_at.slice(0, 10) === today()); if (overview === 'all_pending') rows = rows.filter(t => !['completed', 'cancelled'].includes(t.status));
      if (p.get('opportunity_only') === 'true') rows = rows.filter(t => t.opportunity_id); if (p.get('completed_year')) rows = rows.filter(t => (t.completed_at || '').startsWith(p.get('completed_year')));
      const quarters = p.getAll('completed_quarters').map(Number); if (quarters.length) rows = rows.filter(t => quarters.includes(Math.ceil(Number((t.completed_at || '').slice(5, 7)) / 3)));
      rows = sortTaskRows(rows, p.get('order'));
      const pending = all.filter(t => !['completed', 'cancelled'].includes(t.status));
      return pagination(rows.map(row => taskResponse(row, a)), p, {summary: {total: all.length, pending_count: pending.length,
        overdue_count: pending.filter(t => t.due_at && Date.parse(t.due_at) < Date.now()).length,
        today_pending_count: pending.filter(t => dayKey(t.due_at) === today()).length,
        today_completed_count: all.filter(t => t.status === 'completed' && dayKey(t.completed_at) === today()).length,
        completed_count: all.filter(t => t.status === 'completed').length, filtered_total: rows.length}});
    }
    if ((match = path.match(/^\/tasks\/([^/]+)$/))) {const row = taskRows(a).find(t => t.id === match[1]); if (!row) error(404, '任务不存在'); return taskResponse(row, a);}
    if (path === '/visits/range') {
      const selection = scopeSelection(a, new URLSearchParams([...p, ...p.getAll('recorder_ids').map(id => ['member_ids', id])]), 'visit.read');
      let rows = s.visits.filter(v => readable(a, 'visit.read', [v.recorder_id], actors.find(person => person.user_id === v.recorder_id)?.team_ids || []));
      rows = rows.filter(v => ['confirmed', 'archived'].includes(v.status));
      if (p.get('scope') === 'mine') rows = rows.filter(v => v.recorder_id === a.user_id);
      const recorderIds = p.getAll('recorder_ids'); if (recorderIds.length) rows = rows.filter(v => recorderIds.includes(v.recorder_id));
      if (p.get('team_id')) rows = rows.filter(v => actors.some(person => person.user_id === v.recorder_id && person.team_ids?.includes(p.get('team_id'))));
      const from = p.get('date_from') || '', to = p.get('date_to') || '', basis = p.get('date_basis') === 'created' ? 'created_at' : 'interaction_at';
      if (from) rows = rows.filter(v => dayKey(v[basis] || v.visit_date) >= from);
      if (to) rows = rows.filter(v => dayKey(v[basis] || v.visit_date) <= to);
      const q = String(p.get('q') || '').toLowerCase();
      if (q) rows = rows.filter(v => [v.customer_name, v.opportunity_name, v.recorder_name, v.follow_up_record, v.next_action].some(value => String(value || '').toLowerCase().includes(q)));
      rows.sort((x, y) => String(y.interaction_at || y.visit_date || y.created_at || '').localeCompare(String(x.interaction_at || x.visit_date || x.created_at || '')) || String(y.id).localeCompare(String(x.id)));
      const summary = {total: rows.length, subject_count: new Set(rows.map(v => v.customer_id).filter(Boolean)).size, scored_count: rows.filter(v => Number.isFinite(Number(v.follow_up_score))).length};
      return pagination(rows.map(v => ({...v, recorder_team_id: v.recorder_team_id || actors.find(person => person.user_id === v.recorder_id)?.team_ids[0]})), p, {summary, as_of: new Date().toISOString()});
    }
    if (path === '/visits' || path === '/fde/activity') {const customerIds = new Set(visibleCustomers(a).map(c => c.id)); let rows = s.visits.filter(v => (localDataset && ['manager','supervisor'].includes(a.role)) || customerIds.has(v.customer_id)); for (const key of ['customer_id', 'opportunity_id']) if (p.get(key)) rows = rows.filter(v => v[key] === p.get(key) || (key === 'opportunity_id' && v.opportunity_ids?.includes(p.get(key)))); if (path === '/fde/activity') {if (!isFde(a)) error(403, '当前身份没有 FDE 活动范围'); const selection = scopeSelection(a, p); rows = rows.filter(v => selection.people.some(person => isFde(person) && person.user_id === v.recorder_id) && inPeriod(v.visit_date || v.interaction_at, p)); if (a.role === 'fde' || p.get('scope') === 'self') rows = rows.filter(v => v.recorder_id === a.user_id); if (p.get('member_id')) rows = rows.filter(v => v.recorder_id === p.get('member_id')); const selected = p.getAll('member_ids'); if (selected.length) rows = rows.filter(v => selected.includes(v.recorder_id));} if (p.get('sort') === 'created_desc') rows = rows.slice().sort((a,b) => Date.parse(b.created_at || 0) - Date.parse(a.created_at || 0) || String(b.id).localeCompare(String(a.id))); return pagination(rows, p, p.get('sort') === 'created_desc' ? {sort: 'created_desc'} : {});}
    if ((match = path.match(/^\/visits\/([^/]+)$/))) {const row = s.visits.find(v => v.id === match[1]); if (!row) error(404, '拜访不存在'); if (!(localDataset && ['manager','supervisor'].includes(a.role))) customer(a, row.customer_id); return row;}
    if (path === '/customer-assets/quarters') {const rows = entriesFor(a, p); return {items: actualQuarters(rows), years: uniq(rows.map(e => e.year)), as_of: p.get('as_of') || today()};}
    if (path === '/customer-assets') {const entries = entriesFor(a, p), view = p.get('customer_id') ? 'entries' : 'customers'; const rows = view === 'entries' ? entries : scopedCustomers(a, p).map(c => ({customer_id: c.id, customer_name: c.name, owner_name: c.owner_name || actors.find(x => x.user_id === c.owner_user_ref_id)?.display_name || '', team_name: c.team_name || actors.find(x => x.user_id === c.owner_user_ref_id)?.team_names?.[0] || '', data_kind: 'demo', ...actualSummary(entries.filter(e => e.customer_id === c.id))})); return pagination(rows, p, {view, summary: {...actualSummary(entries),portfolio_customer_count:scopedCustomers(a,p).length,acv_amount:sum(scopedCustomers(a,p).map(c=>enrichCustomer(c,a)),'acv_amount'),unknown_acv_count:0}, as_of: p.get('as_of') || today(), can_manage: a.capabilities['actual.manage']});}
    if (path === '/dashboard') return scopedDashboard(a, p);
    if (path === '/dashboard/rankings') return rankings(a, p);
    if (path === '/profile/performance') {
      const year = Number(p.get('year') || yearNow()), quarter = Number(p.get('quarter') || Math.ceil(Number(today().slice(5, 7)) / 3));
      const context = targetContext(a, {scope: p.get('scope') || 'self', user_id: p.get('member_id') || a.user_id, team_id: p.get('team_id'), period_type: 'quarter', anchor_date: `${year}-${String((quarter - 1) * 3 + 1).padStart(2, '0')}-01`});
      const ops = visibleOpportunities(a, p), ids = new Set(ops.map(op => op.id)), actualRows = effectiveActuals().filter(entry => ids.has(entry.opportunity_id) && entry.year === year && entry.quarter === quarter), totals = actualSummary(actualRows);
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
      response.sample_count = s.visits.filter(visit => (scope === 'team' ? actors.some(person => isFde(person) && person.user_id === visit.recorder_id && (!p.get('team_id') || person.team_ids.includes(p.get('team_id')))) : visit.recorder_id === selected.user_id) && dayKey(visit.visit_date || visit.interaction_at) >= dayKey(new Date(Date.now() - days * 86400000).toISOString()) && dayKey(visit.visit_date || visit.interaction_at) <= today()).length; // 按北京日期比较：只有日期的 visit_date 按 UTC 解析时，凌晨会把「今天」误判成未来
      response.latest.input_snapshot.visit_count = response.sample_count;
      return {...response, scope, member_id: scope === 'team' ? null : selected.user_id, member_name: scope === 'team' ? 'FDE 团队' : selected.display_name, can_review: false, overall_score: null};
    }
    if (path === '/fde/visit-opportunities') return pagination(visibleOpportunities(a).filter(o => (o.fde_member_ids || []).includes(a.user_id) && (!p.get('customer_id') || o.customer_id === p.get('customer_id')) && (!p.get('opportunity_id') || o.id === p.get('opportunity_id'))), p);
    if (path === '/fde/dashboard') {
      if (!isFde(a)) error(403, '当前身份没有 FDE 看板范围');
      const ops = visibleOpportunities(a, p), ids = new Set(ops.map(o => o.id)), tasks = taskRows(a, p);
      const visits = s.visits.filter(v => ids.has(v.opportunity_id) && actors.some(person => isFde(person) && person.user_id === v.recorder_id) && (p.get('scope') !== 'self' && a.role !== 'fde' || v.recorder_id === a.user_id) && (!p.get('member_id') || v.recorder_id === p.get('member_id')) && (!p.getAll('member_ids').length || p.getAll('member_ids').includes(v.recorder_id)) && inPeriod(v.visit_date || v.interaction_at, p));
      const entries = entriesFor(a, p).filter(e => ids.has(e.opportunity_id) && actualInPeriod(e, p));
      const stats = {opportunities: ops.length, open_opportunities: ops.filter(o => o.status === 'open').length, customers: uniq(ops.map(o => o.customer_id)).length, open_acv: sum(ops.filter(o => o.status === 'open'), 'amount'), visits: visits.length, period_visits: visits.length, active_recorders: uniq(visits.map(v => v.recorder_id)).length, period_customers: uniq(visits.map(v => v.customer_id)).length, completed_tasks: tasks.filter(t => t.status === 'completed').length, pending_tasks: tasks.filter(t => !['completed', 'cancelled'].includes(t.status)).length, overdue_tasks: tasks.filter(t => !['completed', 'cancelled'].includes(t.status) && t.due_at < now()).length, ...actualSummary(entries)};
      stats.period_opportunities = uniq(visits.map(v => v.opportunity_id)).length;
      stats.demo_scene_count = demoRows().filter(scene => ids.has(scene.opportunity_id) && !scene.deleted_at).length;
      const selectedPeople = actors.filter(person => (!p.get('team_id') || person.team_ids.includes(p.get('team_id'))) && isFde(person) && (p.get('scope') === 'team' && !p.get('member_id') && a.role === 'fde_lead' || person.user_id === (p.get('member_id') || a.user_id)));
      stats.own_demo_scene_count = demoRows().filter(scene => !scene.deleted_at && selectedPeople.some(person => person.user_id === scene.creator_id) && inPeriod(scene.created_at, p)).length;
      const distribution = actors.filter(person => (!p.get('team_id') || person.team_ids.includes(p.get('team_id'))) && isFde(person) && (a.role === 'fde_lead' || person.user_id === a.user_id)).map(person => {const own = visits.filter(visit => visit.recorder_id === person.user_id), projects = ops.filter(op => (op.fde_member_ids || []).includes(person.user_id)); return {...member(person), opportunities: projects.length, open_acv: sum(projects.filter(op => op.status === 'open'), 'amount'), period_visits: own.length, visits: own.length, period_opportunities: uniq(own.map(visit => visit.opportunity_id)).length};});
      return {data_source: 'database', summary: stats, company_rankings: fdeCompanyRankings(a, p), scope_label: p.get('scope') === 'team' ? 'FDE 团队' : '本人协作', members: actors.filter(person => isFde(person) && (a.capabilities['team.view'] || person.user_id === a.user_id)).map(member), ranking: distribution, recent_visits: visits.slice(0, 8), stages: STAGES.map((code, i) => ({code, label: STAGE_NAMES[i], name: STAGE_NAMES[i], count: ops.filter(o => o.stage_code === code).length, amount: sum(ops.filter(o => o.stage_code === code), 'amount')})), rhythm: [6, 5, 4, 3, 2, 1, 0].map(i => ({date: date(-i).slice(0, 10), visits: visits.filter(v => v.visit_date === date(-i).slice(0, 10)).length})), as_of: now()};
    }
    if (path === '/notifications') return {items: s.notifications.filter(n => n.recipient_user_ref_id === a.user_id || n.recipient_user_id === a.user_id)};
    if (path === '/risks') {
      const ids = new Set(visibleCustomers(a).map(c => c.id));
      let items = s.risks.filter(r => ids.has(r.customer_id));
      const scope = p.get('scope') || 'all', memberId = p.get('member_id'), teamId = p.get('team_id');
      if (!['all','self','person','team'].includes(scope) || scope === 'person' && !memberId || scope === 'team' && !teamId || scope === 'self' && (memberId || teamId)) error(422, '风险范围参数无效');
      const owner = r => r.owner_user_ref_id || r.owner_id;
      if (scope === 'self') items = items.filter(r => owner(r) === a.user_id);
      if (memberId) items = items.filter(r => owner(r) === memberId);
      // Older synthetic seeds predate the owner snapshot fields; derive them only inside the fixture.
      if (teamId) items = items.filter(r => (r.owner_team_id || actors.find(person => person.user_id === owner(r))?.team_ids?.[0]) === teamId);
      if (p.get('opportunity_only') === 'true') items = items.filter(r => r.opportunity_id);
      const closed = r => r.status === 'resolved';
      const summary = {total:items.length,open_count:items.filter(r=>!closed(r)).length,resolved_count:items.filter(closed).length,overdue_count:items.filter(r=>!closed(r) && r.due_at && Date.parse(r.due_at)<Date.now()).length};
      if (p.get('status') === 'open') items = items.filter(r => !closed(r));
      if (p.get('status') === 'resolved') items = items.filter(closed);
      return pagination(items, p, {summary});
    }
    if ((match = path.match(/^\/risks\/([^/]+)$/))) {const ids = new Set(visibleCustomers(a).map(c => c.id)); const row = s.risks.find(r => r.id === match[1] && ids.has(r.customer_id)); if (!row) error(404, '风险不存在或当前身份不可见'); return previewRiskView(a,row);}
    if (path === '/workbench') return {items: [], customers: visibleCustomers(a).map(c => enrichCustomer(c, a)), tasks: taskRows(a), summary: {customer_count: visibleCustomers(a).length, task_count: taskRows(a).length}, source_label: LABEL};
    if ((match = path.match(/^\/agent\/runs\/([^/]+)$/))) {if (!s.runs[match[1]] || s.runs[match[1]].actor_id !== a.user_id) error(404, '运行记录不存在或当前身份不可见'); return s.runs[match[1]];}
    if (path === '/advice/statistics') return {items: [], total: 0};
    if (path === '/advice/latest') {const analysis = Object.values(s.advice).filter(row => row.actor_id === a.user_id && row.workspace_id === a.workspace_id && row.subject_kind === p.get('subject_kind') && row.subject_id === p.get('subject_id') && row.section === (p.get('section') || 'overview')).at(-1) || null; return {state: analysis ? (analysis.stale || analysis.status === 'superseded' ? 'stale' : analysis.status) : 'none', analysis};}
    if ((match = path.match(/^\/advice\/([^/]+)$/))) {if (!s.advice[match[1]]) error(404, '建议不存在'); return s.advice[match[1]];}
    error(501, `尚未实现此功能（${method} ${path}）`);
  }
  function mutate(a, path, method, body, p) {
    const s = getState(), nextId = kind => uid(kind, ++s.serial); let match;
    if ((match = path.match(/^\/opportunities\/([^/]+)\/material-forecast-preview$/)) && method === 'POST') {
      const op = opportunity(a, match[1]);
      if (!Number.isInteger(body.year) || ![1,2,3,4].includes(body.quarter)) error(422, '请选择参考年和季度');
      // Preview does not impersonate the server's rule engine or invent catalog
      // prices. Explicit synthetic fixtures may supply a reference for UI tests.
      const fixture = op.material_preview_fixtures?.[`${body.year}-${body.quarter}`];
      return fixture ? copy(fixture) : {status: 'incomplete', recognized_inclusive: null, recognized_exclusive: null,
        lines: (op.material_lines || []).map(row => ({line_id: row.id, status: 'incomplete', amount_inclusive: null, amount_exclusive: null, missing_fields: ['preview_rule_not_configured']})), missing_fields: ['preview_rule_not_configured']};
    }
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
      const identities = Object.hasOwn(fields, 'business_identities') ? fields.business_identities : row.business_identities || ['customer'];
      if (!Array.isArray(identities) || !identities.length || identities.some(v => !['customer','partner'].includes(v))) error(422, '请至少选择一种有效业务身份');
      const ownId = Object.hasOwn(fields, 'partner_profile_id') ? fields.partner_profile_id : row.partner_profile_id;
      if (row.partner_profile_id && ownId !== row.partner_profile_id) error(409, '已有伙伴档案关联不能替换或清除');
      const own = ownId ? previewPartners(s).find(p => p.id === ownId && (!p.linked_customer_id || p.linked_customer_id === row.id)) : null;
      if (ownId && !own) error(422, '请选择可关联的伙伴档案');
      const externalId = Object.hasOwn(fields, 'partner_id') ? fields.partner_id : row.primary_partner_id;
      const external = externalId ? previewPartners(s).find(p => p.id === externalId && p.id !== ownId) : null;
      if (externalId && !external) error(422, '请选择其他合作伙伴');
      if (identities.includes('customer') && (!row.created_at || !(row.business_identities || ['customer']).includes('customer')) && !BUSINESS_OPTIONS.customer_creation.customer_type.includes(fields.customer_type)) error(422, '请选择客户类型');
      const sameName = !ownId && identities.includes('partner') && previewPartners(s).find(p => p.name === row.name);
      if (sameName) error(409, '已有同名伙伴档案，请选择关联');
      row.business_identities = uniq(identities);
      if (ownId) row.partner_profile_id = ownId;
      else if (identities.includes('partner')) {
        const profile = {id: nextId(14), name: row.name, status: 'active', active: true};
        (s.partner_profiles ||= []).push(profile); row.partner_profile_id = profile.id;
      }
      if (Object.hasOwn(fields, 'partner_id')) {row.primary_partner_id = external?.id || null; row.primary_partner_name = external?.name || null;}
      for (const [input, canonical] of [['industry', 'industry_code'], ['customer_type', 'customer_type_code'], ['level_code', 'level_code'], ['source', 'source_code'], ['partner_name', 'primary_partner_name']]) if (Object.hasOwn(fields, input)) row[canonical] = String(fields[input] || '').trim();
      if (!identities.includes('customer')) row.customer_type_code = null;
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
      for (const key of ['name', ...((body.business_identities || ['customer']).includes('customer') ? ['customer_type'] : []), 'level_code', 'source', 'target_team', 'contact_name', 'contact_title', 'contact_role']) if (!String(body[key] || '').trim()) error(422, '请补齐客户建档字段：' + key);
      const name = String(body.name).trim(); if (s.customers.some(c => c.name === name)) error(409, '客户名称已存在，请使用已有客户');
      const teams = directoryTeams(a), target = body.target_team_id ? teams.find(team => team.id === body.target_team_id) : teams.find(team => team.name === body.target_team);
      if (!target) error(403, '客户归属团队不在授权目录中');
      if (body.target_team && body.target_team !== target.name) error(422, '客户归属团队名称与ID不一致');
      const row = {id: nextId(10), name, target_team: target.name, target_team_id: target.id, workspace_id: a.workspace_id, potential_score: null, relationship_score: null, attributes: {}, version_no: 1, data_kind: 'demo', created_at: now()};
      assignOwner(row, a.role === 'sales' ? a : null); row.owner_team_id = target.id; row.team_name = target.name; customerFields(row, body); s.customers.unshift(row); return row;
    }
    if ((match = path.match(/^\/customers\/([^/]+)$/)) && method === 'PATCH') {
      requireCap(a, 'customer.edit'); const row = customer(a, match[1]);
      for (const key of ['industry', ...((body.business_identities || row.business_identities || ['customer']).includes('customer') ? ['customer_type'] : []), 'level_code', 'source', 'partner_name']) if (Object.hasOwn(body, key) && !String(body[key] || '').trim()) error(422, '客户维护字段不能为空：' + key);
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
      if (row && !objectAllowed(a,'opportunity.update',[row.owner_id],[row.team_id])) error(403,'不能修改当前商机');
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
      const extras = Object.fromEntries([...Object.keys(WORKSPACE_ENUMS),'is_key','summary','note','fde_progress','primary_fde_id','participants'].filter(key=>Object.hasOwn(body,key)).map(key=>[key,copy(body[key])]));
      if(Object.keys(extras).length && body.fields_schema_version !== 2) error(422,'新增字段需要新版表单');
      for(const [key,value] of Object.entries(extras)){const field=workspaceFields(a).find(f=>f.key===key);if(field.options&&value!=null&&(Array.isArray(value)?value:[value]).some(v=>!field.options.some(o=>o.value===v)))error(422,'请选择有效字段选项');if(key==='is_key'&&value!=null&&typeof value!=='boolean')error(422,'请选择是否重点');if(field.type==='text'&&value!=null&&(typeof value!=='string'||value.length>field.max_length))error(422,'请检查文本长度');if(['customer_source','participants'].includes(key)&&!Array.isArray(value))error(422,'多选字段须为数组');}
      const primary=Object.hasOwn(extras,'primary_fde_id')?extras.primary_fde_id:row?.primary_fde_id;
      if(primary&&!fdeIds.includes(primary))error(422,'主责须在长期FDE名单中');
      if((Object.hasOwn(extras,'primary_fde_id')||body.fields_schema_version===2&&Object.hasOwn(body,'fde_member_ids'))&&!objectAllowed(a,'opportunity.fde_members',[row?.owner_id||a.user_id],[row?.team_id||a.team_ids[0]]))error(403,'不能维护商机FDE人员');
      if(extras.participants?.some(p=>!actors.some(person=>person.user_id===p.user_id&&(p.role!=='fde'||isFde(person)))||!['sales','fde','other'].includes(p.role)))error(422,'请选择有效参与人员');
      const values = {...extras,name: String(body.name).trim(), amount: Number(body.amount), expected_close_date: body.expected_close_date, probability: body.status === 'lost' ? null : Number(body.probability),
        stage_code: stage, status: body.status, customer_id: c.id, customer_name: c.name, product_line: String(body.product_line || '').trim(), sales_channel: body.sales_channel, partner_id: body.sales_channel === 'partner' ? body.partner_id : null,
        owner_id: row ? row.owner_id : c.owner_id || a.user_id, owner_user_ref_id: row ? row.owner_id : c.owner_id || a.user_id, owner_name: row ? row.owner_name : c.owner_name || a.display_name, team_name: c.team_name, team_id: c.owner_team_id, partner_name: body.sales_channel === 'partner' ? '华南数码渠道' : '直销',
        fde_member_ids: fdeIds, quarterly_forecasts: copy([...(row?.quarterly_forecasts || []).filter(old => !quarters.some(q => q.year === old.year && q.quarter === old.quarter)), ...quarters]), data_kind: 'demo'};
      // Only material-aware saves add modeling fields. Omitted/null inputs keep
      // an older client's existing lines; explicit [] retires them, not actuals.
      if (row?.material_lines || body.material_lines != null || body.trade_relation != null) Object.assign(values, materialDraft(body, row, a, c.id, nextId));
      values.fde_members = actors.filter(m => values.fde_member_ids.includes(m.user_id)).map(member);
      const changed = !row || [...Object.keys(extras),'name', 'amount', 'expected_close_date', 'probability', 'status', 'product_line', 'sales_channel', 'partner_id', 'fde_member_ids', 'quarterly_forecasts', 'trade_relation', 'upstream_opportunity_id', 'material_lines', 'retired_material_lines'].some(key => JSON.stringify(row[key]) !== JSON.stringify(values[key]));
      if (!changed) return {...enrichOpportunity(row), opportunity_id: row.id, changed: false, version_no: row.version_no};
      const event = {id: nextId(17), type: !row ? 'created' : row.status !== values.status ? values.status === 'open' ? 'reopened' : 'closed' : 'updated', actor_name: a.display_name, at: now(), before: row ? copy(row) : null};
      values.won_at = values.status === 'won' ? row && row.status === 'won' && row.won_at || now() : null;
      values.actual_close_date = values.status === 'open' ? null : today(); values.updated_at = now();
      if (row) Object.assign(row, values, {version_no: row.version_no + 1}); else {row = {...values, id: nextId(11), version_no: 1, creator_id: a.user_id, creator_name: a.display_name, business_created_on: today(), created_at: now()}; s.opportunities.unshift(row);}
      s.opportunityEvents.unshift({...event, key: event.id, opportunity_id: row.id, title: `商机${({created: '已创建', reopened: '已重新打开', closed: '已关闭', updated: '已更新'})[event.type]}`, summary: row.name, description: `保存后的阶段：${STAGE_NAMES[stageIndex]}`, after: copy(row), data_kind: 'demo'});
      return {...enrichOpportunity(row), opportunity_id: row.id, changed: true, event_id: event.id, version_no: row.version_no};
    }
    if ((match = path.match(/^\/opportunities\/([^/]+)\/fde-members$/)) && method === 'PUT') {requireCap(a, 'fde.members.manage'); const row = opportunity(a, match[1]); if (Number(body.version_no) !== row.version_no) error(409, '商机版本已变化'); row.fde_member_ids = uniq(body.member_ids || []); row.fde_members = actors.filter(m => row.fde_member_ids.includes(m.user_id)).map(member); row.version_no++; return {...row, changed: true, event_id: nextId(17)};}
    if(path==='/tasks/batch' && method==='POST'){if(!Array.isArray(body.tasks)||!body.tasks.length||body.tasks.length>100)error(422,'请选择有效接收人');const saved=copy(s);try{return {items:body.tasks.map(task=>mutate(a,'/tasks','POST',task,p))};}catch(e){for(const key of Object.keys(s))delete s[key];Object.assign(s,saved);throw e;}}
    if(path==='/customer-assets/map/refresh' && method==='POST')return {status:'ready'};
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
      const event = body.event_type, review = ['approve_completion', 'reject_completion'].includes(event), coordination = ['reassign', 'cancel'].includes(event); if (!review) requireCap(a, event==='cancel'?'task.cancel':coordination ? 'task.coordinate' : 'task.respond');
      const isOwner = row.assignees.some(m => m.user_id === a.user_id && m.responsibility === 'owner');
      const candidate = row.target_position && row.status === 'pending_confirm' && (row.candidate_user_ids || []).includes(a.user_id) && !(row.declined_user_ids || []).includes(a.user_id);
      if (review && (row.creator_user_ref_id !== a.user_id || row.status !== 'pending_review')) error(403, '只有发起人可确认待验收任务');
      if (!coordination && !review && !isOwner && !candidate) error(403, '只有有效候选人或任务负责人可以操作');
      if(event==='cancel' && !taskResponse(row,a).can_cancel)error(403,'当前任务不允许取消');
      if (coordination && ['completed', 'cancelled','pending_review'].includes(row.status)) error(409, '已结束的任务不能协调');
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
      row.events.push({id: nextId(17), event_type: recordedEvent, note: body.note || '', occurred_at: now(), created_at: now(), actor_user_id: a.user_id, actor_name: a.display_name, previous_owner_ids: previousOwners,payload:{previous_owners:actors.filter(p=>previousOwners.includes(p.user_id)).map(p=>({user_id:p.user_id,name:p.display_name})),new_owner:event==='reassign'?actors.filter(p=>row.assignees.some(o=>o.user_id===p.user_id)).map(p=>({user_id:p.user_id,display_name:p.display_name}))[0]:null}, status: row.status});
      notify([row.creator_user_ref_id, ...previousOwners, ...row.assignees.map(p => p.user_id), ...(row.candidate_user_ids || [])].filter(id => id !== a.user_id), template, 'task', row,
        {actor_name: a.display_name, creator_name: row.creator_name, owner_name: row.owner_name, previous_owner_names: actors.filter(a => previousOwners.includes(a.user_id)).map(a => a.display_name), note: body.note || '', comment: body.note || '', completion_note: row.completion_note, status: row.status});
      return taskResponse(row, a);
    }
    if (path === '/visits' && method === 'POST') error(501, '拜访校验模块未加载，不能跳过质检归档');
    // The preview only has sales/FDE identities, never an operations writer.
    if ((path === '/customer-assets' || /^\/customer-assets\/[^/]+\/void$/.test(path)) && method === 'POST') error(403, '实际金额由运营在管理后台确认，当前销售身份只能查看');
    if (path === '/profile/sales-targets' && method === 'POST') {if (isFde(a)) error(403, 'FDE 不能维护销售目标'); if (!['recognized', 'collection'].includes(body.kind) || !Number.isFinite(Number(body.amount))) error(422, '请输入有效目标'); s.targets[a.user_id] = {...(s.targets[a.user_id] || {recognized: localDataset ? null : 5000000, collection: localDataset ? null : 4000000}), [body.kind]: Number(body.amount)}; return {saved: true, ...s.targets[a.user_id]};}
    if ((path === '/profile/sales-growth/review' || path === '/fde/profile/review') && method === 'POST') error(501, 'Agent 暂未接入，不能生成能力评分');
    if ((match = path.match(/^\/risks\/([^/]+)\/(decision|resolve)$/)) && method === 'POST') {
      const row = s.risks.find(r => r.id === match[1] && visibleCustomers(a).some(c=>c.id===r.customer_id));
      if (!row) error(404, '风险不存在或当前身份不可见');
      const decision=match[2]==='decision', permission=decision?'risk.decide':'risk.resolve';
      if (!previewRiskAllowed(a,row,permission)) error(403, '当前身份无权处理此风险');
      if (!Number.isInteger(body.version_no) || body.version_no<1) error(422, '请提供风险版本');
      const note=String(decision?body.note||'':body.resolution_note||'').trim();
      if (note.length<(decision?1:5) || note.length>2000) error(422,'请填写有效处理依据');
      if (decision && (!['adopt','defer'].includes(body.decision) || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(body.request_id||''))) error(422,'建议决定或请求编号无效');
      row.events ||= [];
      const request={decision:body.decision,note,version_no:body.version_no,request_id:body.request_id};
      const previous=decision && row.events.find(e=>e.event_type==='suggestion_decided' && e.actor_user_ref_id===a.user_id && e.payload.request_id===body.request_id);
      if(previous){if(JSON.stringify(previous.payload.request)!==JSON.stringify(request))error(409,'请求内容已变化');return {risk:previewRiskView(a,row),decision_event_id:previous.id,idempotent_replay:true};}
      if (body.version_no!==(row.version_no||1) || row.status==='resolved') error(409,'风险版本或状态已更新');
      if(decision && !previewRiskView(a,row).suggested_action) error(409,'当前没有建议动作');
      const event={id:nextId(43),event_type:decision?'suggestion_decided':'resolved',from_status:row.status,to_status:decision?row.status:'resolved',actor_user_ref_id:a.user_id,actor_name:a.display_name,occurred_at:now(),payload:decision?{request_id:body.request_id,request,decision:body.decision,note,risk_version_before:body.version_no,risk_version_after:body.version_no+1,suggested_action:previewRiskView(a,row).suggested_action,evidence:copy(row.evidence||[])}:{resolution_note:note}};
      row.version_no=body.version_no+1;row.events.push(event);
      if(!decision)Object.assign(row,{status:'resolved',resolved_at:event.occurred_at,resolved_by_name:a.display_name,resolution_note:note});
      return decision?{risk:previewRiskView(a,row),decision_event_id:event.id,idempotent_replay:false}:previewRiskView(a,row);
    }
    if ((match = path.match(/^\/notifications\/([^/]+)\/read$/)) && method === 'POST') {const row = s.notifications.find(n => n.id === match[1] && (n.recipient_user_ref_id === a.user_id || n.recipient_user_id === a.user_id)); if (!row) error(404, '通知不存在'); row.read_at ||= now(); return {id: row.id, read: true, read_at: row.read_at};}
    if (path === '/conversations' && method === 'POST') {const id = nextId(20); s.conversations[id] = {...body, id, actor_id: a.user_id}; return {id};}
    if ((match = path.match(/^\/conversations\/([^/]+)\/messages$/)) && method === 'POST') {const conversation = s.conversations[match[1]]; if (!conversation || conversation.actor_id !== a.user_id) error(404, '会话不存在');
      if (['visit_entry', 'opportunity_draft', 'customer_create', 'task_create'].includes(conversation.mode)) error(501, '录入 Agent 暂未接入，请手工填写');
      const id = nextId(21), result = {title: '分析说明', summary: 'Agent 中台暂未接入，暂不提供分析结论。', text: '接入 Agent 中台后，这里会给出分析、评分与客户建议。', rows: [], metrics: []};
      s.runs[id] = {id, status: 'succeeded', actor_id: a.user_id, result, demo: true}; return {run_id: id};}
    if (path === '/advice' && method === 'POST') {const id = nextId(22); const result = {id, status: 'succeeded', subject_kind: body.subject_kind, subject_id: body.subject_id, actor_id: a.user_id, workspace_id: a.workspace_id, customer_id: body.subject_kind === 'customer' ? body.subject_id : body.subject_kind === 'opportunity' ? opportunity(a, body.subject_id).customer_id : s.visits.find(v => v.id === body.subject_id)?.customer_id || null, opportunity_id: body.subject_kind === 'opportunity' ? body.subject_id : null, section: body.section, summary: 'Agent 中台暂未接入，暂无经营建议。', content: '接入 Agent 中台后，这里会给出经营建议。', suggestions: [], data_kind: 'demo'}; s.advice[id] = result; return result;}
    error(501, `尚未实现此操作（${method} ${path}）`);
  }
  // This provider never accepts original files or invents extraction results.
  // An explicit unregistered receipt lets the shared recovery state safely keep
  // or remove the local file instead of treating a known unsupported demo as a lost receipt.
  function previewUpload(options) {
    if (global.SALES_MODE !== 'preview') error(403, '工作区尚未启用');
    const path = new URL(options.url, 'https://preview.invalid').pathname;
    if (path !== '/api/v1/visit-imports') error(501, '当前环境不支持上传文件或音频');
    const actor = session(options); requireCap(actor, 'visit.create');
    const key = options.header?.['Idempotency-Key'], filename = options.formData?.original_filename;
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(key || '') || typeof filename !== 'string' || !filename.trim()) error(422, '请重新选择有效文件');
    const state = getState(); state.previewUploadReceipts ||= {};
    const receipt = {upload_intent_id:key,state:'retryable_failure',id:null,status:null,filename:filename.trim(),retryable:true,
      message:'演示模式未接收文件，请保留原件。可移除附件后使用文字录入。'};
    state.previewUploadReceipts[actor.user_id + ':' + key] = receipt; save(); return receipt;
  }
  function respond(options, uploaded) {
    let finished = false;
    const timer = global.setTimeout(() => {
      if (finished) return; finished = true;
      let response;
      try {const data = uploaded ? previewUpload(options) : dispatch(options); const accepted = /^\/api\/v1\/visit-flow\/(structure|quality)$/.test(new URL(options.url, 'https://preview.invalid').pathname); response = {statusCode: accepted ? 202 : 200, data: {...copy(data), demo: true, data_kind: 'demo', source_label: data.source_label || LABEL, ...(getState().migration_notice ? {migration_notice: getState().migration_notice} : {})}, header: {'X-Sales-Data-Mode': 'preview'}, errMsg: 'request:ok'};}
      catch (e) {response = {statusCode: e.status || 500, data: {message: e.message, detail: e.message, ...(e.code ? {code:e.code} : {}), demo: true, source_label: LABEL}, errMsg: 'request:ok'};}
      if (uploaded) response.data = JSON.stringify(response.data);
      try {if (typeof options.success === 'function') options.success(response);} finally {if (typeof options.complete === 'function') options.complete(response);}
    }, 35);
    return {abort() {if (finished) return; finished = true; global.clearTimeout(timer); const failure = {errMsg: (uploaded ? 'uploadFile' : 'request') + ':fail abort'}; try {if (options.fail) options.fail(failure);} finally {if (options.complete) options.complete(failure);}}, onProgressUpdate() {}};
  }
  global.SalesPreview = Object.freeze({request(options = {}) {return respond(options, false);}, uploadFile(options = {}) {return respond(options, true);}, reset, loadLocal, inspect() {return localDataset ? getState() : null;}});
})(window);
