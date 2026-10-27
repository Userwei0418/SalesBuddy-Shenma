import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, cp, writeFile, readFile, rm, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

test('build verifies shared source, rejects untracked code/config drift and removes stale output', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const stage = await mkdtemp(join(tmpdir(), 'sales-web-build-'));
  try {
    for (const name of ['scripts', 'web-pages', 'source', 'assets', 'knowledge', 'design-system', 'index.html', 'knowledge-share.html', 'entry.js', 'shell.js', 'desktop.css', 'date-picker.js', 'date-picker.css', 'runtime.js', 'runtime.css', 'browser-platform.js', 'preview-api.js', 'preview-workflow.js', 'crm-preview-ui.js', 'select-components.js', 'select-components.css', 'dashboard-charts.js', 'dashboard-charts.css', 'visual-theme.css', 'design-tokens.css', 'theme.js', 'theme.css', 'home-activity.js', 'home-activity.css', 'detail-workspace.js', 'detail-workspace.css', 'workbench-filters.css', 'workflow-forms.css', 'claim-workspace.css', 'workspace-frame.css', 'insights-workspace.css', 'workbench-columns.css', 'customer-columns.css', 'task-columns.css', 'collection-columns.css', 'customer-detail-columns.css', 'opportunity-detail-columns.css', 'record-detail-columns.css', 'task-workspace.js', 'task-workspace.css', 'task-workspace.wxml', 'review-lists.css', 'review-forms.css', 'review-pages.css', 'review-interactions.css', 'legacy.css', 'review-forms.js', 'review-pages.js', 'review-interactions.js', 'review-lists.js']) await cp(join(root, name), join(stage, name), {recursive: true});
    await cp(join(root, '../scripts/miniprogram_manifest.py'), join(stage, 'scripts/miniprogram_manifest.py'));
    await cp(join(root, 'privacy-notice.json'), join(stage, 'privacy-notice.json'));
    const run = (output = '') => spawnSync('python3', ['scripts/build.py'], {cwd: stage, encoding: 'utf8', env: {...process.env, SALES_WEB_DIST_DIR: output}});
    let result = run(); assert.equal(result.status, 0, result.stderr);
    const version = JSON.parse(await readFile(join(stage, 'dist/version.json')));
    await access(join(stage, 'dist/design-system/index.html'));
    for (const name of ['preview-api.js', 'preview-workflow.js', 'crm-preview-ui.js']) await assert.rejects(access(join(stage, 'dist', name)), {code: 'ENOENT'});
    assert.equal(await readFile(join(stage, 'dist/design-tokens.css'), 'utf8'), await readFile(join(root, 'design-tokens.css'), 'utf8'));
    assert.equal(version.pages, 33); // 共享业务页面、知识库工作台及 PC 专属列表/历史入口
    assert.equal(version.components, 58); assert.equal(version.source_files_verified, Object.keys(JSON.parse(await readFile(join(root, 'source/manifest.json'))).files).length);
    const originalBundle = await readFile(join(stage, 'dist/bundle.js'), 'utf8');
    assert.equal(run('.runtime/candidate/dist').status, 0);
    assert.equal(await readFile(join(stage, 'dist/bundle.js'), 'utf8'), originalBundle);
    await access(join(stage, '.runtime/candidate/dist/bundle.js'));
    assert.match(run('source/miniprogram').stderr, /Candidate output/);
    const bundle = JSON.parse((await readFile(join(stage, 'dist/bundle.js'), 'utf8')).replace(/^window.SALES_BUNDLE=/, '').replace(/;\s*$/, ''));
    assert.equal(version.web_build.webSourceDirty, true);
    assert.equal(version.web_build.sourceRevision, '');
    assert.deepEqual(JSON.parse(bundle.modules.buildInfo.replace(/^module.exports = /, '').replace(/;\s*$/, '')), version.web_build);
    for (const route of ['pages/workbench/index', 'pages/opportunities/index']) {
      assert.match(bundle.modules[route], /pcRoute: true/);
      assert.deepEqual(bundle.pages[route].templates, {});
      assert.doesNotMatch(bundle.modules[route], /loadAllOpportunities|refreshCustomerMap/);
    }
    assert.ok(bundle.pages['pages/customer-history/index']);
    assert.ok(bundle.pages['pages/opportunity-history/index']);
    const nodes = tree => tree.filter(node => typeof node === 'object').flatMap(node => [node, ...nodes(node.c || [])]);
    const riskTree = nodes(bundle.pages['pages/risk-detail/index'].tree);
    const riskContent = riskTree.find(node => node.a?.class?.split(' ').includes('web-extended-scroll'));
    const riskBranches = riskContent.c.filter(node => typeof node === 'object' && /(?:resolved|resolution|readonly)-card/.test(node.a?.class || ''));
    assert.equal(riskBranches.length, 3);
    assert.equal(riskBranches[0].a['wx:if'], "{{risk.status === 'resolved'}}");
    assert.equal(riskBranches[1].a['wx:elif'], '{{canResolveRisk && risk.canResolve}}');
    assert.ok(Object.hasOwn(riskBranches[2].a, 'wx:else'));
    const branchIndexes = riskBranches.map(node => riskContent.c.indexOf(node));
    assert.deepEqual(branchIndexes, [branchIndexes[0], branchIndexes[0] + 1, branchIndexes[0] + 2]);
    assert.ok(bundle.components['components/fde-projects/index'].templates['opportunity-list-card']);
    // PC portfolio is a read-only controller, not the Mini Program auto-score loader.
    assert.equal(bundle.config.pages.filter(route => route === 'pages/customers/index').length, 1);
    assert.match(bundle.modules['pages/customers/index'], /pcRoute: true/);
    assert.doesNotMatch(bundle.modules['pages/customers/index'], /refreshCustomerMap|loadAssets/);
    assert.ok(bundle.components['ui/sb/sb-summary-card/index']);
    assert.ok(bundle.components['ui/sb/sb-status-tag/index']);
    assert.ok(bundle.components['ui/tdesign/icon/icon']);
    assert.equal(Object.keys(bundle.pages).some(key => key.startsWith('templates/')), false);
    assert.match(bundle.components['components/dashboard-picker/index'].css, /\.sheet-fixed:where\(\[data-wx-style="components\/dashboard-picker\/index"\]\)/);
    await writeFile(join(stage, 'source/miniprogram/extra.js'), 'window.bad=true;');
    assert.match(run().stderr, /Source inventory mismatch/);
    await rm(join(stage, 'source/miniprogram/extra.js'));
    await writeFile(join(stage, 'source/miniprogram/ui/extra.js'), 'window.bad=true;');
    assert.match(run().stderr, /Source inventory mismatch/);
    await rm(join(stage, 'source/miniprogram/ui/extra.js'));
    await writeFile(join(stage, 'source/miniprogram/config.js'), "module.exports={API_BASE_URL:'https://unexpected.invalid'}");
    assert.match(run().stderr, /fixed same-origin/);
    await cp(join(root, 'source/miniprogram/config.js'), join(stage, 'source/miniprogram/config.js'));
    await writeFile(join(stage, 'source/miniprogram/utils/access.js'), 'module.exports={can:()=>true};');
    assert.match(run().stderr, /Shared business source changed/);
    await cp(join(root, 'source/miniprogram/utils/access.js'), join(stage, 'source/miniprogram/utils/access.js'));
    await writeFile(join(stage, 'dist/stale.json'), '{}');
    result = run(); assert.equal(result.status, 0, result.stderr);
    await assert.rejects(access(join(stage, 'dist/stale.json')));
  } finally { await rm(stage, {recursive: true, force: true}); }
});
