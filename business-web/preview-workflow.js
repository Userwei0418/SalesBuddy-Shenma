(function (global) {
  'use strict';
  const LABEL = '字段校验';
  const FIRST = [['customer_main_business', '客户主营业务'], ['customer_needs', '客户需求'], ['customer_budget', '客户预算'], ['contact_role', '联系人角色']];
  const ALIASES = {
    follow_up_record: ['沟通内容', '拜访结果', '拜访记录', '跟进记录'],
    next_action: ['下一步行动计划', '下一步计划', '下一步', '后续计划'],
    interaction_at: ['跟进日期', '拜访日期'], created_date: ['创建时间', '录入日期'],
    contact_name: ['对接人', '联系人'], partner_name: ['伙伴名称', '合作伙伴'],
    ...Object.fromEntries(FIRST.map(([key, label]) => [key, [label]])),
  };
  const clean = value => String(value == null ? '' : value).trim();
  const present = value => clean(value) && !['未知', '待补充', '待确认', '不详', '—'].includes(clean(value));
  const isFde = actor => ['fde', 'fde_lead'].includes(actor.role);
  const isFirst = fields => [true, 1, '1', 'true'].includes(fields.is_first_visit);
  const today = now => new Date(Date.parse(now) + 8 * 3600000).toISOString().slice(0, 10);
  const clone = value => JSON.parse(JSON.stringify(value));
  const FIELD_KEYS = ['customer_name', 'customer_type', 'opportunity_name', 'follow_up_record', 'next_action', 'interaction_at', 'created_date', 'contact_name', 'partner_name', 'contact_title', 'interaction_mode', 'visit_location', 'visit_goal', 'customer_main_business', 'customer_needs', 'customer_budget', 'contact_role', 'is_first_visit'];
  const canonical = value => JSON.stringify(value, function (_, v) { return v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.keys(v).sort().map(k => [k, v[k]])) : v; });
  function strictDate(value) { return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number(value.slice(0, 4)) > 0 && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value; }
  function stageFields(fields, ctx) {
    if (!fields || FIELD_KEYS.some(key => !(key in fields)) || Object.keys(fields).some(key => !FIELD_KEYS.includes(key))) ctx.error(422, '质检须提交完整的规范字段快照');
    for (const key of FIELD_KEYS) if (typeof fields[key] !== (key === 'is_first_visit' ? 'boolean' : 'string')) ctx.error(422, '质检字段类型不正确：' + key);
    for (const [key, label] of [['interaction_at', '跟进日期'], ['created_date', '创建时间'], ['contact_name', '对接人']]) if (!present(fields[key])) ctx.error(422, '请补充' + label);
    if (!strictDate(fields.interaction_at) || !strictDate(fields.created_date)) ctx.error(422, '请填写有效的 YYYY-MM-DD 日期');
  }
  function archiveSnapshot(body) {
    const fields = body.fields || {};
    return {customer_id: body.customer_id, opportunity_id: fields.opportunity_id || null,
      fields: Object.fromEntries(FIELD_KEYS.map(key => [key, fields[key]])),
      collaborator_ids: fields.collaborator_ids || [], fde_participant_ids: body.fde_participant_ids || [],
      source_import_id: fields.source_import_id || null, opportunity_mutation: fields._opportunity_mutation || null};
  }
  function visitSnapshots(fields, actors, at) {
    const visitDate = clean(fields.interaction_at).slice(0, 10);
    const elapsedDays = (Date.parse(today(at)) - Date.parse(visitDate)) / 86400000;
    return {visit_date: visitDate, within_seven_days: Number.isFinite(elapsedDays) ? elapsedDays >= 0 && elapsedDays <= 6 : null,
      contact_name_snapshot: clean(fields.contact_name), partner_name_snapshot: clean(fields.partner_name),
      collaborators: actors.filter(person => (fields.collaborator_ids || []).includes(person.user_id)).map(person => ({id: person.user_id, name: person.display_name}))};
  }
  function labels(text, mapping) {
    const result = {}, raw = String(text);
    const names = [...Object.values(mapping).flat(), '拜访类型', '录入类型'];
    const alternatives = names.map(name => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
    const matches = [...raw.matchAll(new RegExp('(?:^|[\\n;；])\\s*【?(' + alternatives + ')\\s*[：:]\\s*', 'g'))];
    for (let index = 0; index < matches.length; index++) {
      const match = matches[index];
      const entry = Object.entries(mapping).find(([, aliases]) => aliases.includes(match[1]));
      if (entry) result[entry[0]] = raw.slice(match.index + match[0].length, index + 1 < matches.length ? matches[index + 1].index : raw.length).trim();
    }
    return result;
  }
  function dateField(value, now) {
    if (clean(value) === '今天') return today(now);
    const match = clean(value).match(/^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?/);
    return match ? `${match[1]}-${match[2].padStart(2, '0')}-${match[3].padStart(2, '0')}` : clean(value);
  }
  function parseVisit(text, now) {
    const raw = clean(text), fields = labels(raw, ALIASES);
    const original = raw.includes('【拜访原始记录】') ? raw.split('【拜访原始记录】').slice(1).join('【拜访原始记录】').trim() : raw;
    const reviewing = raw.startsWith('拜访审核 v2');
    if (!fields.follow_up_record && !reviewing) fields.follow_up_record = original;
    fields.follow_up_record ||= ''; fields.next_action ||= '';
    fields.is_first_visit = /(?:录入类型|拜访类型)[：:]首次拜访/.test(raw);
    if (!reviewing) {
      fields.created_date = fields.created_date ? dateField(fields.created_date, now) : today(now);
      fields.interaction_at = dateField(fields.interaction_at, now);
      fields.contact_name ||= '';
    }
    return fields;
  }
  function reviewContent(fields) {
    const lines = ['拜访审核 v2', `拜访结果：${clean(fields.follow_up_record)}`, `下一步行动计划：${clean(fields.next_action)}`];
    if (isFirst(fields)) lines.push('拜访类型：首次拜访', ...FIRST.map(([key, label]) => `${label}：${clean(fields[key])}`));
    return lines.join('\n');
  }
  function checkFields(fields) {
    const next = clean(fields.next_action);
    const checks = [
      {key: 'follow_up_record', label: '沟通内容已填写', passed: !!present(fields.follow_up_record)},
      {key: 'next_action', label: '下一步计划已填写', passed: !!present(next)},
      {key: 'next_date', label: '下一步包含日期或明确相对日期', passed: /(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}|\d{1,2}月\d{1,2}日|今天|明天|后天|本周[一二三四五六日天]|下周[一二三四五六日天])/.test(next)},
      {key: 'next_action_text', label: '下一步包含可核对的行动文字', passed: /(?:发送|提交|确认|整理|完成|联系|安排|预约|提供|沟通|演示|评审|拜访|跟进|收集|更新|检查|交付|汇报|签订|召开|输出|准备|发出|发给)/.test(next)},
    ];
    if (isFirst(fields)) FIRST.forEach(([key, label]) => checks.push({key, label: `${label}已填写`, passed: !!present(fields[key])}));
    const passed = checks.filter(check => check.passed).length;
    const gaps = checks.filter(check => !check.passed).map(check => `待补充：${check.label}`);
    return {demo: true, review_kind: 'deterministic_field_check', source_label: LABEL,
      follow_up_score: Math.floor(passed / checks.length * 100), checks, passed_count: passed, check_count: checks.length,
      admission_policy: {score_threshold: 100, inclusive: true, good_score: 100, excellent_score: 101},
      suggestions: [LABEL + '；数值仅为以上文字规则通过比例，不是销售质量评分。', ...gaps],
      next_action: {passed: checks.filter(check => check.key.startsWith('next_')).every(check => check.passed), suggestions: gaps.filter(message => message.includes('下一步'))},
    };
  }
  function assertFdeTarget(ctx, customerId, opportunityId) {
    if (!isFde(ctx.actor)) return;
    if (!opportunityId) ctx.error(422, 'FDE 须选择本人参与的商机');
    const row = ctx.opportunity(opportunityId);
    if (row.customer_id !== customerId || !(row.fde_member_ids || []).includes(ctx.actor.user_id)) ctx.error(403, '该商机不属于当前客户或本人未参与，不能录入');
  }
  function qualifiedReview(ctx, fields) {
    const run = ctx.state.runs[fields._quality_review_run_id];
    if (!run || run.actor_id !== ctx.actor.user_id || !['visit_entry', 'visit_quality'].includes(run.mode) || run.status !== 'succeeded') ctx.error(422, '请先完成当前身份的字段校验');
    if (run.mode === 'visit_quality') {
      const payload = run.review_payload;
      const expected = {customer_id: payload.customer_id, opportunity_id: payload.opportunity_id || null, fields: payload.fields,
        collaborator_ids: payload.collaborator_ids || [], fde_participant_ids: payload.fde_participant_ids || [],
        source_import_id: payload.source_import_id || null, opportunity_mutation: payload.opportunity_mutation || null};
      if (canonical(expected) !== canonical(archiveSnapshot(ctx.body))) ctx.error(409, '拜访字段、关联对象或参与人已修改，请重新执行字段校验');
      stageFields(fields && Object.fromEntries(FIELD_KEYS.map(key => [key, fields[key]])), ctx);
    }
    if (run.customer_id !== ctx.body.customer_id || isFde(ctx.actor) && run.opportunity_id !== fields.opportunity_id) ctx.error(409, '关联对象已变化，请重新执行字段校验');
    if (run.review_text !== reviewContent(fields)) ctx.error(409, '拜访正文已修改，请重新执行字段校验');
    const quality = checkFields(fields);
    if (quality.follow_up_score !== 100 || !quality.next_action.passed) ctx.error(422, '字段校验未通过，请补齐正文和带日期的下一步行动');
    if (run.archived_visit_id) ctx.error(409, '这次校验已经归档；新增拜访请重新校验');
    for (const [key, label] of [['interaction_at', '跟进日期'], ['created_date', '创建时间'], ['contact_name', '对接人']]) if (!present(fields[key])) ctx.error(422, `请补充${label}`);
    for (const key of ['interaction_at', 'created_date']) if (!Number.isFinite(Date.parse(fields[key]))) ctx.error(422, '请填写有效跟进日期及创建时间');
    return {run, quality};
  }
  function handle(ctx) {
    if (global.SALES_MODE !== 'preview') return {handled: false};
    const {method, path, body, actor, state: state, nextId, now} = ctx;
    const done = value => ({handled: true, value: {...value, demo: true, data_kind: 'demo', source_label: LABEL}});
    let match;
    if (path === '/visit-flow/structure' && method === 'POST') {
      ctx.requireCap(actor, 'visit.create');
      const customer = ctx.customer(body.customer_id);
      assertFdeTarget(ctx, customer.id, body.opportunity_id);
      if (!present(body.text) || body.text.length > 50000) ctx.error(422, '请填写不超过 50000 字的拜访原文');
      if (body.source_import_id) ctx.error(501, '暂不支持文件导入，请使用文字录入');
      const parsed = parseVisit(body.text, now());
      const fields = Object.fromEntries(FIELD_KEYS.map(key => [key, key === 'is_first_visit' ? !isFde(actor) && body.is_first_visit === true : clean(parsed[key])]));
      fields.customer_name = customer.name; fields.customer_type = '客户';
      if (body.opportunity_id) {const op = ctx.opportunity(body.opportunity_id); if (op.customer_id !== customer.id) ctx.error(422, '关联商机不属于该客户'); fields.opportunity_name = op.name;}
      const id = nextId(21);
      state.runs[id] = {id, status: 'succeeded', mode: 'visit_structure', actor_id: actor.user_id, customer_id: customer.id, opportunity_id: body.opportunity_id || null, source_import_id: null,
        result: {visit_stage: 'structure', fields, summary: fields.follow_up_record.slice(0, 5000), extraction_method: 'explicit_text_labels', demo: true, source_label: LABEL}, created_at: now(), completed_at: now()};
      return done({run_id: id, status: 'queued'});
    }
    if (path === '/visit-flow/quality' && method === 'POST') {
      ctx.requireCap(actor, 'visit.create'); ctx.customer(body.customer_id); assertFdeTarget(ctx, body.customer_id, body.opportunity_id);
      const source = state.runs[body.source_run_id];
      if (!source || source.mode !== 'visit_structure' || source.actor_id !== actor.user_id || source.customer_id !== body.customer_id || source.status !== 'succeeded') ctx.error(422, '请先整理当前身份、当前客户的拜访原文');
      if (body.source_import_id || isFde(actor) && source.opportunity_id !== body.opportunity_id) ctx.error(409, '原文来源或 FDE 关联商机已变化，请重新整理');
      stageFields(body.fields, ctx);
      if (typeof body.summary !== 'string' || body.summary.length > 5000) ctx.error(422, '质检摘要格式不正确');
      if (body.opportunity_id && ctx.opportunity(body.opportunity_id).customer_id !== body.customer_id) ctx.error(422, '商机不属于当前客户');
      const collaborators = body.collaborator_ids || [], participants = body.fde_participant_ids || [];
      if (!Array.isArray(collaborators) || !Array.isArray(participants) || collaborators.length > 30 || participants.length > 30) ctx.error(422, '参与人字段无效');
      if (collaborators.some(id => !ctx.actors.some(person => person.user_id === id && !isFde(person))) || participants.some(id => !ctx.actors.some(person => person.user_id === id && isFde(person)))) ctx.error(422, '请从当前目录重新核对协同人和 FDE 参与人');
      if (isFde(actor) && (body.opportunity_mutation || participants.length || collaborators.length || body.fields.is_first_visit)) ctx.error(403, 'FDE 本人录入不能修改商机、首次拜访或代选参与人');
      if (body.opportunity_mutation) ctx.requireCap(actor, 'opportunity.edit');
      if (participants.length && !body.opportunity_id && !body.opportunity_mutation) ctx.error(422, 'FDE 参与人须关联商机');
      const id = nextId(21), quality = checkFields(body.fields);
      state.runs[id] = {id, status: 'succeeded', mode: 'visit_quality', actor_id: actor.user_id, customer_id: body.customer_id, opportunity_id: body.opportunity_id || null,
        review_payload: clone(body), review_text: reviewContent(body.fields), result: {visit_stage: 'quality', quality_review: quality, demo: true, source_label: LABEL}, created_at: now(), completed_at: now()};
      return done({run_id: id, status: 'queued'});
    }
    if (path === '/conversations' && method === 'POST' && ['visit_entry', 'opportunity_draft'].includes(body.mode)) {
      ctx.requireCap(actor, body.mode === 'visit_entry' ? 'visit.create' : 'opportunity.edit');
      if (body.customer_id) ctx.customer(body.customer_id);
      if (body.mode === 'visit_entry') assertFdeTarget(ctx, body.customer_id, body.opportunity_id);
      const id = nextId(20); state.conversations[id] = {...body, id, actor_id: actor.user_id};
      return done({id});
    }
    if ((match = path.match(/^\/conversations\/([^/]+)\/messages$/)) && method === 'POST') {
      const conversation = state.conversations[match[1]];
      if (!conversation || !['visit_entry', 'opportunity_draft'].includes(conversation.mode)) return {handled: false};
      if (conversation.actor_id !== actor.user_id) ctx.error(404, '当前身份不可访问该会话');
      ctx.requireCap(actor, conversation.mode === 'visit_entry' ? 'visit.create' : 'opportunity.edit');
      if (!present(body.text)) ctx.error(422, '请先填写拜访原文');
      if (conversation.customer_id) ctx.customer(conversation.customer_id);
      if (conversation.mode === 'visit_entry') assertFdeTarget(ctx, conversation.customer_id, conversation.opportunity_id);
      const id = nextId(21); let result, reviewText;
      if (conversation.mode === 'visit_entry') {
        const fields = parseVisit(body.text, now());
        result = {fields, quality_review: checkFields(fields), extraction_method: 'explicit_text_labels', source_label: LABEL, demo: true};
        reviewText = reviewContent(fields);
      } else {
        const parsed = labels(body.text, {name: ['商机名称', '项目名称'], amount_wan: ['ACV（万元）', 'ACV(万元)'], expected_close_date: ['预计关单日期'], product_line: ['产品线']});
        result = {opportunity_draft: parsed, source_label: '标签提取 · 只保留原文明确填写的商机字段', demo: true};
      }
      state.runs[id] = {id, status: 'succeeded', mode: conversation.mode, result, actor_id: actor.user_id, customer_id: conversation.customer_id || null, opportunity_id: conversation.opportunity_id || null,
        review_text: reviewText || null, created_at: now(), completed_at: now(), intent_code: conversation.mode, demo: true};
      return done({run_id: id});
    }
    if (path === '/visits' && method === 'POST') {
      ctx.requireCap(actor, 'visit.create');
      const customer = ctx.customer(body.customer_id), fields = clone(body.fields || {});
      assertFdeTarget(ctx, customer.id, fields.opportunity_id);
      if (isFde(actor) && (fields._opportunity_mutation || (body.fde_participant_ids || []).length)) ctx.error(403, 'FDE 本人录入不能修改商机或代选其他参与人');
      let opportunity = fields.opportunity_id ? ctx.opportunity(fields.opportunity_id) : null;
      if (opportunity && opportunity.customer_id !== customer.id) ctx.error(422, '商机不属于当前客户');
      const {run, quality} = qualifiedReview(ctx, fields);
      const participantIds = isFde(actor) ? [actor.user_id] : [...new Set(body.fde_participant_ids || [])];
      const actors = ctx.actors || [];
      if (participantIds.some(id => !actors.some(person => person.user_id === id && isFde(person)))) ctx.error(422, '请重新核对实际参与的FDE');
      if (fields._opportunity_mutation) {
        if (typeof ctx.saveOpportunity !== 'function') ctx.error(501, '商机与拜访共同保存尚未接入');
        opportunity = ctx.saveOpportunity(customer.id, fields._opportunity_mutation);
        fields.opportunity_id = opportunity.id;
      }
      const id = nextId(13);
      const row = {...fields, id, fields, customer_id: customer.id, customer_name: customer.name, opportunity_id: opportunity?.id || null, opportunity_name: opportunity?.name || null,
        recorder_id: actor.user_id, recorder_name: actor.display_name, creator_name: actor.display_name, created_at: now(), archived_at: now(), visit_date: String(fields.interaction_at).slice(0, 10),
        ...visitSnapshots(fields, actors, now()), status: 'archived', version_no: 1, can_read_detail: true,
        fde_participant_ids: participantIds, fde_participants: actors.filter(person => participantIds.includes(person.user_id)).map(person => ({id: person.user_id, name: person.display_name})),
        collaborator_ids: fields.collaborator_ids || [], quality_review: quality, follow_up_score: quality.follow_up_score, quality_score: quality.follow_up_score,
        completed_count: Object.values(fields).filter(present).length, source_label: LABEL, demo: true, data_kind: 'demo'};
      if (opportunity && participantIds.length) {
        const saved = state.opportunities.find(item => item.id === opportunity.id);
        saved.fde_member_ids = [...new Set([...(saved.fde_member_ids || []), ...participantIds])];
        saved.fde_members = actors.filter(person => saved.fde_member_ids.includes(person.user_id)).map(person => ({id: person.user_id, name: person.display_name, role: person.role}));
      }
      state.visits.unshift(row); run.archived_visit_id = id;
      return done(row);
    }
    if ((match = path.match(/^\/visits\/([^/]+)$/)) && method === 'PATCH') {
      ctx.requireCap(actor, 'visit.supplement');
      const row = state.visits.find(item => item.id === match[1]);
      if (!row) ctx.error(404, '拜访不存在');
      ctx.customer(row.customer_id);
      if (isFde(actor) && row.recorder_id !== actor.user_id) ctx.error(403, 'FDE 只能补充本人记录');
      if (Number(body.version_no) !== row.version_no) ctx.error(409, '记录已变化，请刷新后重试');
      const permitted = ['collaborator_ids', 'interaction_at', 'created_date', 'partner_name', 'contact_name', 'is_first_visit', ...FIRST.map(([key]) => key)];
      if (Object.keys(body).some(key => !permitted.includes(key) && key !== 'version_no')) ctx.error(422, '补充记录不能重写沟通正文和下一步');
      const values = Object.fromEntries(permitted.filter(key => key in body).map(key => [key, body[key]]));
      Object.assign(row, values, {fields: {...row.fields, ...values}, version_no: row.version_no + 1, updated_at: now()});
      Object.assign(row, visitSnapshots(row, ctx.actors || [], now()));
      return done(row);
    }
    return {handled: false};
  }
  global.SalesPreviewWorkflow = Object.freeze({handle, parseVisit, reviewContent, checkFields});
})(window);
