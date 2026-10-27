// Editable business input mirrors the frozen V122 knowledge domain contract.
export const CATEGORIES = [
  ['overall_solution', '总方案'], ['product_intro', '产品介绍'], ['industry_solution', '行业方案'],
  ['customer_solution', '客户方案'], ['sales_support', '销售支持'], ['training_assessment', '培训考核'], ['conference_speech', '会议演讲'],
].map(([value, label]) => ({value, label}));
// Category visibility is separate from material ACL: only a current workspace
// catalog-management grant opens the five maintained categories for writing.
export const contributionCategoryAllowed = (category, canManageCatalog = false) =>
  CATEGORIES.some(row => row.value === category) && (canManageCatalog === true || ['conference_speech', 'customer_solution'].includes(category));
export function contributionCategoryOptions(canManageCatalog = false, selectedCategory = '') {
  const options = CATEGORIES.filter(row => contributionCategoryAllowed(row.value, canManageCatalog));
  const historical = CATEGORIES.find(row => row.value === selectedCategory);
  return historical && !contributionCategoryAllowed(selectedCategory, canManageCatalog)
    ? [{...historical, disabled: true}, ...options] : options;
}
export const SCOPES = [['internal', '仅内部'], ['customer', '对客'], ['partner', '对伙伴'], ['public', '完全公开']].map(([value, label]) => ({value, label}));
export const STATES = {saved: '文件已保存', syncing: '同步中', parsing: '解析中', retrievable: '可检索', failed: '处理失败', deleted: '已停用'};
export const optionLabel = (rows, code) => (rows || []).find(row => (row.code ?? row.value) === code)?.label || code || '—';
export const activeOptions = rows => (rows || []).filter(row => row.active === true).map(row => ({value: row.code, label: row.label}));
// Filtering can include authorized imported products. Editing still uses only
// activeOptions(catalog.products); source hashes are never contribution choices.
export function productFilterOptions(...groups) {
  const options = new Map();
  for (const rows of groups) for (const row of rows || []) {
    if (typeof row.code === 'string' && row.code && typeof row.label === 'string' && row.label && !options.has(row.code)) options.set(row.code, {value: row.code, label: row.label});
  }
  return [...options.values()];
}
export const needsIndustry = category => ['industry_solution', 'customer_solution'].includes(category);
export const emptyContribution = () => ({content_category: '', content_scope: '', product_code: '', industry_code: null, customer_id: null,
  reporting_position_code: null, reporting_level_code: null, decision_role_code: null, opportunity_id: null,
  supplementary_text: '', content: {file_ids: [], links: []}, remarks: ''});

// Imported catalog references remain distinct from archived files and normal
// contribution fields. A source token is never a file ID or a download URL.
export const importedSource = contribution => contribution?.kind === 'legacy_import' && contribution.source?.provider === 'feishu_base' ? contribution.source : null;
export const contributionLinks = contribution => {
  const links = importedSource(contribution)?.links || contribution?.selection?.content?.links;
  return Array.isArray(links) ? links : [];
};
export const sourceStatusLabel = status => ({'可用': '可用', '待复核': '待复核', '已停用': '已停用'}[status] || '状态待确认');
export function materialStateLabel(state, source) {
  if (source?.provider === 'feishu_base' && state === 'saved') return '目录已导入';
  if (source?.provider === 'feishu_base' && state === 'failed') return '待补齐 / 复核';
  return stateLabel(state);
}
export function importIssueText(code) {
  return ({SOURCE_REVIEW_REQUIRED: '原表资料待复核', SOURCE_DISABLED: '原表资料已停用', MATERIAL_REFERENCE_MISSING: '尚未提供材料链接或附件',
    INDUSTRY_NOT_MAPPED: '行业尚未关联系统目录', CUSTOMER_NOT_MAPPED: '客户尚未关联 CRM', REPORTING_ROLES_NOT_MAPPED: '汇报对象岗位、职级和决策角色尚未补齐'})[code] || '原始资料需要复核';
}
export function contributionForm(version) {
  const source = importedSource(version?.contribution), selection = version?.contribution?.selection || {};
  return {...emptyContribution(), ...selection,
    // Legacy product hashes belong to the source catalog, not the editable
    // product catalog. Require an explicit current-product selection.
    ...(source ? {product_code: '', supplementary_text: '', remarks: source.remarks || ''} : {}),
    content: {file_ids: source ? [] : [...(selection.content?.file_ids || [])], links: [...contributionLinks(version?.contribution)]}};
}

export function changeCategory(form, category) {
  return {...form, content_category: category, ...(!needsIndustry(category) ? {industry_code: null} : {}),
    ...(category !== 'customer_solution' ? {customer_id: null, reporting_position_code: null, reporting_level_code: null, decision_role_code: null} : {})};
}

export function validateCatalog(catalog) {
  if (!catalog?.company_id || !Number.isInteger(catalog.revision) || catalog.revision < 1) throw Error('知识库目录版本不可用，请重新读取');
  for (const group of ['products', 'industries', 'reporting_positions', 'reporting_levels', 'decision_roles']) {
    if (!Array.isArray(catalog[group]) || catalog[group].some(row => !row.code || !row.label || typeof row.active !== 'boolean')
      || new Set(catalog[group].map(row => row.code)).size !== catalog[group].length) throw Error('知识库枚举目录不完整，请联系管理员');
  }
  if (!catalog.products.some(row => row.code === catalog.general_product_code && row.active)) throw Error('通用产品尚未配置，请联系管理员');
  return catalog;
}

export function previewName(form, catalog, customer) {
  return [form.product_code && form.product_code !== catalog?.general_product_code ? optionLabel(catalog?.products, form.product_code) : '',
    form.content_category && optionLabel(CATEGORIES, form.content_category), form.content_scope && optionLabel(SCOPES, form.content_scope),
    needsIndustry(form.content_category) && form.industry_code ? optionLabel(catalog?.industries, form.industry_code) : '',
    form.content_category === 'customer_solution' && customer?.id === form.customer_id ? customer.name : '', form.supplementary_text.trim()].filter(Boolean).join('｜');
}

const uuid = value => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value || '') && !/^0{8}-0{4}-0{4}-0{4}-0{12}$/.test(value);
export function validateContribution(form, catalog) {
  const errors = {};
  try { validateCatalog(catalog); } catch (error) { errors.catalog = error.message; return errors; }
  if (!CATEGORIES.some(row => row.value === form.content_category)) errors.content_category = '请选择内容类别';
  if (!SCOPES.some(row => row.value === form.content_scope)) errors.content_scope = '请选择内容范围';
  const check = (field, group, label) => { if (!catalog[group].some(row => row.active && row.code === form[field])) errors[field] = `请选择当前有效的${label}`; };
  check('product_code', 'products', '产品');
  if (needsIndustry(form.content_category)) check('industry_code', 'industries', '行业');
  if (form.content_category === 'customer_solution') {
    if (!uuid(form.customer_id)) errors.customer_id = '请搜索并选择 CRM 标准客户';
    check('reporting_position_code', 'reporting_positions', '岗位'); check('reporting_level_code', 'reporting_levels', '职级'); check('decision_role_code', 'decision_roles', '决策角色');
  }
  if (form.opportunity_id && !uuid(form.opportunity_id)) errors.opportunity_id = '请选择 CRM 标准商机';
  const {file_ids = [], links = []} = form.content || {};
  if (!file_ids.length && !links.length) errors.content = '请上传附件或填写至少一个链接';
  if (file_ids.some(id => !uuid(id))) errors.content = '附件尚未成功归档，请重新上传';
  if (links.some(link => {try { const u = new URL(link); return !['http:', 'https:'].includes(u.protocol) || Boolean(u.username || u.password); } catch { return true; }})) errors.content = '仅支持不含账号密码的 HTTP(S) 链接';
  if (new Set(file_ids).size !== file_ids.length || new Set(links.map(link => {try {return new URL(link).href;} catch {return link;}})).size !== links.length) errors.content = '请移除重复的附件或链接';
  if (file_ids.length > 100 || links.length > 100) errors.content = '附件和链接分别最多 100 项';
  if ([...form.supplementary_text].length > 300) errors.supplementary_text = '人工补充不能超过 300 字';
  if ([...form.remarks].length > 2000) errors.remarks = '备注不能超过 2000 字';
  return errors;
}

export function contributionPayload(form, catalog) {
  const clean = changeCategory(form, form.content_category);
  const errors = validateContribution(clean, catalog);
  if (Object.keys(errors).length) throw Object.assign(Error(Object.values(errors)[0]), {fields: errors});
  // No generated name, company, actor, ACL, date or CRM display values are sent.
  return {expected_catalog_revision: catalog.revision, ...Object.fromEntries(Object.keys(emptyContribution()).map(key => [key, clean[key]])),
    supplementary_text: clean.supplementary_text.trim(), remarks: clean.remarks.trim(), content: {file_ids: [...clean.content.file_ids], links: [...clean.content.links]}};
}

export function countText(value) {
  return value?.status === 'available' && Number.isInteger(value.count) && value.count >= 0 && value.source && value.observed_at ? String(value.count) : '—';
}
export function formatObservedAt(value) {
  if (!value) return '观测时间待确认';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '观测时间待确认';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date).replace(/\//g, '-');
}
export function stateLabel(state) { return STATES[state] || '状态待确认'; }
export function newRequestKey() { return globalThis.crypto.randomUUID(); }

// Grants always name every permitted action. Content audience is never an input.
export function aclGrants(kind, ids, actions = ['read']) {
  if (kind === 'personal') return [];
  if (!['team', 'user', 'company'].includes(kind) || (kind !== 'company' && (!ids.length || ids.some(id => !uuid(id))))) throw Error('请选择有效的共享对象');
  if (!actions.length || actions.some(action => !['read', 'edit', 'manage'].includes(action))) throw Error('请选择共享权限');
  if (kind === 'company') return [{subject_kind: 'company', subject_id: null, actions: [...actions]}];
  return [...new Set(ids)].map(subject_id => ({subject_kind: kind, subject_id, actions: [...actions]}));
}
