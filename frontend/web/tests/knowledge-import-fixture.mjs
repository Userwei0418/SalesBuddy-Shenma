// Synthetic fixtures with the V267 legacy_import response shape; no business data.
export const id = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const option = (code, label = code) => ({code, label, active: true});
export const catalog = {company_id: id(1), revision: 4, general_product_code: 'general', products: [option('general', '通用'), option('current', '当前系统产品')],
  industries: [option('industry', '测试行业')], reporting_positions: [option('position', '测试岗位')], reporting_levels: [option('level', '测试职级')], decision_roles: [option('role', '决策者')]};
export function importedMaterial(n = 10, status = '可用', references = true) {
  const name = `弹药库原名称 ${n} / 保留原文`, source = {provider: 'feishu_base', base_id: 'baseFixture', table_id: 'tblFixture', record_id: `record${n}`, source_revision: 7,
    original_name: name, product_label: '旧产品名称', category_label: '客户方案', scope_label: '可对客', source_status: status, provider_name: '原提供人甲',
    links: references ? ['https://example.test/original-material'] : [], original_content_text: references ? '[原材料说明](https://example.test/original-material)' : '',
    attachments: references ? [{name: '原附件.pdf', source_token: 'fixture_source_token', size_bytes: 1200, media_type: 'application/pdf'}] : [], remarks: '原始备注完整保留',
    validation_issues: [...(status === '待复核' ? ['SOURCE_REVIEW_REQUIRED'] : status === '已停用' ? ['SOURCE_DISABLED'] : []), ...(!references ? ['MATERIAL_REFERENCE_MISSING'] : []), 'INDUSTRY_NOT_MAPPED', 'CUSTOMER_NOT_MAPPED', 'REPORTING_ROLES_NOT_MAPPED']};
  const version = {version_id: id(n + 100), version_no: 1, state: 'saved', files: [], uploaded_at: '2026-10-18T00:00:00Z', contributor_user_id: id(2),
    contribution: {kind: 'legacy_import', company_id: id(1), content_name: name, selection: {product_code: 'legacy_fixture_hash', content_category: 'customer_solution', content_scope: 'customer'}, source}};
  return {material_id: id(n), material_revision: 1, acl_revision: 1, version_id: version.version_id, version_no: 1, state: 'saved', active_version_id: null, active_version_state: null,
    content_name: name, product_code: 'legacy_fixture_hash', content_category: 'customer_solution', content_scope: 'customer', contributor_user_id: id(2),
    can_edit: true, can_manage: false, sharing_label: '公司内授权成员可见', updated_at: '2026-10-18T00:00:00Z', source, versions: [version]};
}
