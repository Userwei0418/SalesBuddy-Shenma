import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawnSync} from 'node:child_process';

function fixture(run) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sales-web-import-'));
  try {
    const web = path.join(root, 'web'), front = path.join(root, 'frontend'), target = path.join(web, 'source/miniprogram');
    for (const dir of [web + '/scripts', target + '/utils', front + '/miniprogram/utils']) fs.mkdirSync(dir, {recursive: true});
    fs.copyFileSync(new URL('../scripts/import_source.py', import.meta.url), web + '/scripts/import_source.py');
    fs.writeFileSync(front + '/VERSION.json', JSON.stringify({registered_pages: []}));
    fs.writeFileSync(front + '/miniprogram/config.js', 'module.exports={HOME_CHATBI_ENABLED:false,HOME_MESSAGE_ORDER:"desc"};');
    fs.writeFileSync(front + '/miniprogram/utils/old.js', 'new shared bytes');
    fs.writeFileSync(front + '/miniprogram/utils/new.js', 'new reviewed module');
    fs.writeFileSync(target + '/utils/old.js', 'preserved before validation');
    fs.writeFileSync(web + '/source/manifest.json', JSON.stringify({files: {'utils/old.js': 'old-hash'}, feature_flags: {HOME_CHATBI_ENABLED: false, HOME_MESSAGE_ORDER: 'desc'}, adaptations: {config: 'preserve adapter'}}));
    const invoke = args => spawnSync('python3', [web + '/scripts/import_source.py', front, ...args], {encoding: 'utf8'});
    run({invoke, web, front, target});
  } finally { fs.rmSync(root, {recursive: true, force: true}); }
}

test('selected source import validates every path before modifying any generated file', () => fixture(({invoke, target}) => {
  for (const args of [
    ['--files', 'utils/old.js', 'utils/new.js'],
    ['--files', 'utils/old.js', '--new-files', 'utils/new.js'],
    ['--files', 'utils/old.js', '--new-files', '../private.js', '--revision', 'a'.repeat(40)],
    ['--files', 'utils/old.js', '--new-files', 'config.js', '--revision', 'a'.repeat(40)],
  ]) {
    const result = invoke(args); assert.notEqual(result.status, 0);
    assert.equal(fs.readFileSync(target + '/utils/old.js', 'utf8'), 'preserved before validation');
    assert.equal(fs.existsSync(target + '/utils/new.js'), false);
  }
}));

test('reviewed shared additions are imported and attributed without replacing Web adapters', () => fixture(({invoke, web, target}) => {
  const result = invoke(['--files', 'utils/old.js', '--new-files', 'utils/new.js', '--revision', 'b'.repeat(40), '--purpose', 'finance-shared-model']);
  assert.equal(result.status, 0, result.stderr);
  const manifest = JSON.parse(fs.readFileSync(web + '/source/manifest.json'));
  assert.equal(manifest.adaptations.config, 'preserve adapter');
  assert.deepEqual(manifest.incremental_imports[0].new_files, ['utils/new.js']);
  assert.equal(manifest.incremental_imports[0].revision, 'b'.repeat(40));
  assert.equal(manifest.files['utils/new.js'], crypto.createHash('sha256').update(fs.readFileSync(target + '/utils/new.js')).digest('hex'));
  assert.equal(fs.readFileSync(target + '/utils/old.js', 'utf8'), 'new shared bytes');
}));
