import test from 'node:test';
import assert from 'node:assert/strict';
import {catalog, id, importedMaterial} from './knowledge-import-fixture.mjs';
import {activeOptions, contributionForm, contributionLinks, contributionPayload, importedSource, importIssueText, materialStateLabel, productFilterOptions, sourceStatusLabel, validateContribution} from '../department-ui/knowledge/model.js';
import {authorizeMaterialLink, createKnowledgeService} from '../department-ui/knowledge/service.js';

test('V267 import remains a catalog entry; original status is not parsing or archived-file status', () => {
  for (const status of ['可用', '待复核', '已停用']) {
    const material = importedMaterial(10, status), version = material.versions[0];
    assert.equal(materialStateLabel(version.state, importedSource(version.contribution)), '目录已导入');
    assert.equal(sourceStatusLabel(status), status);
    assert.equal(material.active_version_id, null); assert.deepEqual(version.files, []);
  }
  assert.equal(importIssueText('CUSTOMER_NOT_MAPPED'), '客户尚未关联 CRM');
  assert.equal(importIssueText('future_code'), '原始资料需要复核');
  assert.equal(materialStateLabel('saved', null), '文件已保存');
  assert.equal(importedSource({kind: 'future_kind', source: {provider: 'feishu_base'}}), null);
});

test('legacy form preserves links and remarks, never invents archived files or CRM fields, and requires a current product', () => {
  const material = importedMaterial(), before = structuredClone(material), form = contributionForm(material.versions[0]);
  assert.equal(form.product_code, ''); assert.equal(form.content_category, 'customer_solution'); assert.equal(form.content_scope, 'customer');
  assert.equal(form.supplementary_text, ''); assert.equal(form.remarks, '原始备注完整保留');
  assert.deepEqual(form.content, {file_ids: [], links: ['https://example.test/original-material']});
  const errors = validateContribution(form, catalog);
  for (const key of ['product_code', 'industry_code', 'customer_id', 'reporting_position_code', 'reporting_level_code', 'decision_role_code']) assert.ok(errors[key], key);
  assert.equal(errors.content, undefined);
  assert.throws(() => contributionPayload(form, catalog), /当前有效的产品/);
  const payload = contributionPayload({...form, product_code: 'current', industry_code: 'industry', customer_id: id(3), reporting_position_code: 'position', reporting_level_code: 'level', decision_role_code: 'role'}, catalog);
  assert.equal(payload.remarks, before.source.remarks); assert.deepEqual(payload.content.links, before.source.links);
  for (const key of ['source', 'source_token', 'content_name', 'kind']) assert.equal(key in payload, false);
  assert.deepEqual(material, before);
});

test('legacy attachment description alone cannot satisfy new-version content requirements', () => {
  const material = importedMaterial(); material.versions[0].contribution.source.links = [];
  const form = contributionForm(material.versions[0]);
  assert.deepEqual(form.content, {file_ids: [], links: []});
  assert.equal(validateContribution(form, catalog).content, '请上传附件或填写至少一个链接');
  const missing = contributionForm(importedMaterial(11, '可用', false).versions[0]);
  assert.equal(validateContribution(missing, catalog).content, '请上传附件或填写至少一个链接');
});

test('normal version prefill remains unchanged and copied content arrays cannot mutate the receipt', () => {
  const version = {contribution: {selection: {content_category: 'product_intro', content_scope: 'internal', product_code: 'current', supplementary_text: '新材料', remarks: '新备注', content: {file_ids: [id(5)], links: ['https://example.test/native']}}}};
  const form = contributionForm(version); assert.equal(form.product_code, 'current'); assert.equal(form.remarks, '新备注');
  form.content.file_ids.push(id(6)); form.content.links.push('https://example.test/new');
  assert.deepEqual(version.contribution.selection.content, {file_ids: [id(5)], links: ['https://example.test/native']});
});

test('legacy links recheck current ACL, exact version membership, actor and URL; source tokens never become links', async () => {
  const material = importedMaterial(), version_id = material.version_id, material_id = material.material_id, link = material.source.links[0];
  let revoked = false, reads = 0;
  const service = {getMaterial: async () => {reads++; if (revoked) throw Error('权限已撤回'); return material;}};
  assert.equal(await authorizeMaterialLink(service, {material_id, version_id, link}), link);
  await assert.rejects(authorizeMaterialLink(service, {material_id, version_id: id(999), link}), /不可访问/);
  await assert.rejects(authorizeMaterialLink(service, {material_id, version_id, link: 'fixture_source_token'}), /不可访问/);
  await assert.rejects(authorizeMaterialLink(service, {material_id, version_id, link}, () => false), /身份或权限已变化/);
  material.source.links.push('javascript:alert(1)');
  await assert.rejects(authorizeMaterialLink(service, {material_id, version_id, link: 'javascript:alert(1)'}), /不受支持/);
  material.versions[0].state = 'deleted';
  await assert.rejects(authorizeMaterialLink(service, {material_id, version_id, link}), /不可访问/);
  revoked = true; await assert.rejects(authorizeMaterialLink(service, {material_id, version_id, link}), /权限已撤回/);
  assert.equal(reads, 7); assert.deepEqual(contributionLinks({kind: 'legacy_import', source: {provider: 'feishu_base', links: null}}), []);
});

test('production service adapter preserves legacy source and authoritative totals with existing permission gates', async () => {
  const row = importedMaterial(), service = createKnowledgeService({request: async ({path}) => path.includes('?') ? {items: [row], total: 1, limit: 20, offset: 0, capabilities: {create: false}} : row});
  const page = await service.listMaterials(), detail = await service.getMaterial(row.material_id);
  assert.equal(page.total, 1); assert.equal(page.capabilities.create, false); assert.deepEqual(page.items[0].source, row.source);
  assert.equal(detail.capabilities.edit, true); assert.equal(detail.capabilities.manage, false); assert.deepEqual(detail.versions[0].contribution, row.versions[0].contribution);
});

test('product filters combine authorized source products by code without expanding editable catalog', () => {
  const source = [{code: 'current', label: '当前系统产品', count: 1}, {code: 'legacy_outside_page', label: '分页外导入产品'}];
  const result = productFilterOptions(catalog.products, source, [{code: 'legacy_outside_page', label: '重复产品'}]);
  assert.equal(result.filter(row => row.value === 'current').length, 1);
  assert.deepEqual(result.find(row => row.value === 'legacy_outside_page'), {value: 'legacy_outside_page', label: '分页外导入产品'});
  assert.equal(activeOptions(catalog.products).some(row => row.value.startsWith('legacy_')), false);
});
