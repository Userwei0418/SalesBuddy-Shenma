import {state, esc, api, head, filters, search, options, table, pager, date, badge,
  bindCommon, dialog, details, field, requirePermission, hasPermission, readForDialog} from './core.js';

const businessIdentities = payload => payload.business_identities === undefined ? ['customer'] : payload.business_identities;
const identityDetails = payload => [
  ['业务身份', (businessIdentities(payload) || []).map(value => ({customer: '客户', partner: '合作伙伴'}[value] || value)).join('、')],
  ['自身伙伴档案 ID', payload.partner_profile_id],
  ['所属伙伴档案 ID', payload.partner_id],
];

// Only the explicit pending request selected by the operator is decided.
// The shared dialog prevents duplicate submission and discards stale sessions.
export async function customerCreationRequests() {
  requirePermission('customer.create_review');
  if (!Object.keys(state.filters).length) state.filters = {status: 'pending'};
  const params = new URLSearchParams({status: state.filters.status || 'all', q: state.filters.q || '', limit: 50, offset: state.offset});
  const data = await api('/customer-creation-requests?' + params);
  const rows = data.items || [];
  return {
    html: head('客户新建审批', '核对存量客户与申请资料；批准后才建立正式客户，驳回必须填写原因。') + '<div class="card">' +
      filters(search('搜索客户 / 申请人 / 审批单号') + `<select name="status" aria-label="审批状态">${options([['pending','待审批'],['approved','已通过'],['rejected','已驳回'],['cancelled','已取消']], state.filters.status, '全部状态')}</select>`) +
      table(['申请客户','申请人','提交时间','状态','审核人 / 意见','操作'], rows, row => `<tr>
        <td class="title">${esc(row.reviewed_payload?.name || row.payload?.name || '未命名客户')}<small>${esc(row.payload?.company_reference || '')}</small></td>
        <td>${esc(row.applicant_name || '—')}</td><td>${date(row.requested_at)}</td><td>${badge(row.status)}</td>
        <td>${esc(row.reviewer_name || '—')}<small>${esc(row.decision_reason || '')}</small></td>
        <td><button class="link" data-action="inspect" data-id="${esc(row.id)}">${row.status === 'pending' ? '核对与审批' : '查看申请'}</button></td></tr>`) + pager(data.total) + '</div>',
    bind: root => bindCommon(root, {inspect: async id => {
      requirePermission('customer.create_review');
      const row = rows.find(item => item.id === id);
      if (!row) throw Error('申请已变化，请刷新');
      const payload = row.payload || {}, pending = row.status === 'pending' && hasPermission('customer.create_review');
      const identities = businessIdentities(payload), hasCustomer = identities?.includes('customer');
      const canCreate = pending && hasPermission('customer.create');
      const directory = canCreate ? await readForDialog('/customers/create-options') : {teams: []};
      if (!directory) return;
      const teamId = payload.target_team_id || directory.teams.find(team => team.name === payload.target_team)?.id || '';
      const approvedFields = canCreate ? '<h3>审定资料（批准时可修正）</h3>' + details(identityDetails(payload)) +
        '<p>业务身份和已选伙伴档案保留原申请内容。</p><div class="grid">' +
        field('客户全称', 'name', payload.name) + field('外部编号（选填）', 'company_reference', payload.company_reference) +
        field('行业', 'industry', payload.industry) +
        (hasCustomer ? field('客户类型', 'customer_type', '', {select: options(['潜在客户','商机客户','已成单客户'], payload.customer_type, null)}) : '') +
        field('优先级', 'level_code', '', {select: options(['Tier-1','Tier-2','Tier-3'], payload.level_code, null)}) +
        field('来源', 'source', '', {select: options(['销售自拓','客户转介绍','市场活动','销售线索','合作伙伴','其他'], payload.source, null)}) +
        field('管理部门', 'target_team_id', '', {select: options(directory.teams.map(team => [team.id, team.name]), teamId, '请选择有效部门')}) +
        field('所属伙伴', 'partner_name', payload.partner_name, {disabled: Boolean(payload.partner_id)}) + field('联系人姓名', 'contact_name', payload.contact_name) +
        field('联系人职位', 'contact_title', payload.contact_title) +
        field('联系人角色', 'contact_role', '', {select: options(['使用者','影响者','决策者'], payload.contact_role, null)}) +
        field('联系电话', 'contact_phone', payload.contact_phone) + field('邮箱', 'contact_email', payload.contact_email) + '</div>' : '';
      const reviewed = row.reviewed_payload;
      dialog(pending ? '核对客户新建申请' : '客户新建申请记录', '<h3>原始申请</h3>' + details([
        ['客户全称', payload.name], ['公司编号 / 审批单号', payload.company_reference],
        ...identityDetails(payload),
        ['申请人', row.applicant_name], ['申请时间', date(row.requested_at)],
        ['行业', payload.industry], ['客户类型', payload.customer_type], ['优先级', payload.level_code],
        ['来源', payload.source], ['所属伙伴', payload.partner_name], ['部门', payload.target_team],
        ['联系人', payload.contact_name], ['职位', payload.contact_title], ['联系人角色', payload.contact_role],
        ['联系电话', payload.contact_phone], ['邮箱', payload.contact_email],
        ['审核人', row.reviewer_name], ['处理时间', date(row.reviewed_at)], ['审核意见', row.decision_reason],
      ]) + (reviewed ? '<h3>审定资料</h3>' + details([
        ['客户全称', reviewed.name], ['公司编号 / 审批单号', reviewed.company_reference],
        ...identityDetails(reviewed),
        ['行业', reviewed.industry], ['客户类型', reviewed.customer_type], ['优先级', reviewed.level_code],
        ['来源', reviewed.source], ['所属伙伴', reviewed.partner_name], ['部门', reviewed.target_team], ['联系人', reviewed.contact_name],
        ['职位', reviewed.contact_title], ['联系人角色', reviewed.contact_role],
        ['联系电话', reviewed.contact_phone], ['邮箱', reviewed.contact_email],
      ]) : '') + approvedFields + (pending ? field('审核结果', 'decision', '', {select: options(canCreate ? [['approved', '批准为未认领客户'], ['rejected', '驳回申请']] : [['rejected', '驳回申请']], canCreate ? 'approved' : 'rejected', null)}) +
        field('审核意见', 'reason', '', {type: 'textarea', help: '驳回时必须说明原因，批准后客户名称由后端再次检查是否重复。'}) : ''),
      pending ? {submit: '确认审核', onSubmit: async (form, key) => {
        requirePermission('customer.create_review');
        const decision = form.get('decision'), reason = String(form.get('reason') || '').trim();
        if (decision === 'rejected' && !reason) throw Error('请填写驳回原因');
        const body = {decision, reason, version_no: row.version_no};
        if (decision === 'approved') {
          requirePermission('customer.create');
          const team = directory.teams.find(item => item.id === form.get('target_team_id'));
          if (!team) throw Error('请选择当前管理范围内的有效部门');
          body.approved_payload = Object.fromEntries(['name','company_reference','industry','customer_type','level_code','source',
            'partner_name','contact_name','contact_title','contact_role','contact_phone','contact_email']
            .map(key => [key, String(form.get(key) || '').trim()]));
          Object.assign(body.approved_payload, {
            target_team: team.name, target_team_id: team.id,
            business_identities: identities,
            partner_profile_id: payload.partner_profile_id ?? null,
            partner_id: payload.partner_id ?? null,
            customer_type: hasCustomer ? body.approved_payload.customer_type : null,
          });
          if (payload.partner_id) body.approved_payload.partner_name = payload.partner_name || '';
        }
        await api(`/customer-creation-requests/${encodeURIComponent(id)}/decision`, {method: 'POST', key, body});
      }} : {});
    }}),
  };
}
