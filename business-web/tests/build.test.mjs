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
    for (const name of ['scripts', 'web-pages', 'source', 'assets', 'design-system', 'index.html', 'entry.js', 'shell.js', 'desktop.css', 'date-picker.js', 'date-picker.css', 'runtime.js', 'runtime.css', 'browser-platform.js', 'preview-api.js', 'preview-workflow.js', 'crm-preview-ui.js', 'select-components.js', 'select-components.css', 'dashboard-charts.js', 'dashboard-charts.css', 'visual-theme.css', 'design-tokens.css', 'theme.js', 'theme.css', 'home-activity.js', 'home-activity.css', 'detail-workspace.js', 'detail-workspace.css', 'workbench-filters.css', 'workflow-forms.css', 'claim-workspace.css', 'workspace-frame.css', 'insights-workspace.css', 'workbench-columns.css', 'customer-columns.css', 'task-columns.css', 'collection-columns.css', 'customer-detail-columns.css', 'opportunity-detail-columns.css', 'record-detail-columns.css', 'task-workspace.js', 'task-workspace.css', 'task-workspace.wxml', 'review-lists.css', 'review-forms.css', 'review-pages.css', 'review-interactions.css', 'legacy.css', 'review-forms.js', 'review-pages.js', 'review-interactions.js', 'review-lists.js']) await cp(join(root, name), join(stage, name), {recursive: true});
    const run = () => spawnSync('python3', ['scripts/build.py'], {cwd: stage, encoding: 'utf8'});
    let result = run(); assert.equal(result.status, 0, result.stderr);
    const version = JSON.parse(await readFile(join(stage, 'dist/version.json')));
    await access(join(stage, 'dist/design-system/index.html'));
    assert.equal(await readFile(join(stage, 'dist/design-tokens.css'), 'utf8'), await readFile(join(root, 'design-tokens.css'), 'utf8'));
    assert.equal(version.pages, 27); assert.equal(version.components, 14); assert.equal(version.source_files_verified, 259);
    const bundle = JSON.parse((await readFile(join(stage, 'dist/bundle.js'), 'utf8')).replace(/^window.SALES_BUNDLE=/, '').replace(/;\s*$/, ''));
    assert.ok(bundle.pages['pages/workbench/index'].templates['opportunity-list-card']);
    assert.ok(bundle.components['components/fde-projects/index'].templates['opportunity-list-card']);
    assert.ok(bundle.pages['pages/customers/index'].templates['customer-advice-panel']);
    assert.equal(Object.keys(bundle.pages).some(key => key.startsWith('templates/')), false);
    assert.match(bundle.components['components/dashboard-picker/index'].css, /\.sheet-scroll:where\(\[data-wx-style="components\/dashboard-picker\/index"\]\)/);
    await writeFile(join(stage, 'source/miniprogram/extra.js'), 'window.bad=true;');
    assert.match(run().stderr, /Source inventory mismatch/);
    await rm(join(stage, 'source/miniprogram/extra.js'));
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
