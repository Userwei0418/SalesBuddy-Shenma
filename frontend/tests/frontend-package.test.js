const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync, spawnSync} = require('node:child_process');

const sourceScript = path.resolve(__dirname, '../scripts/package_frontend.py');
const isolationScript = path.resolve(__dirname, '../../scripts/check_isolation.py');
const customerApi = 'https://salesbuddy.shenzhoukuntai.com:28899/api/v1';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'salegent-handoff-test-'));
  t.after(() => fs.rmSync(root, {recursive:true, force:true}));
  const repo = path.join(root, 'repo');
  fs.mkdirSync(repo);
  const write = (name, content) => {
    const file = path.join(repo, name);
    fs.mkdirSync(path.dirname(file), {recursive:true});
    fs.writeFileSync(file, content);
  };
  const git = (...args) => execFileSync('git', args, {cwd:repo, encoding:'utf8', stdio:['ignore','pipe','pipe']}).trim();
  git('init', '-q');
  git('config', 'user.name', 'Packaging test');
  git('config', 'user.email', 'packaging@example.invalid');
  git('config', 'commit.gpgsign', 'false');
  git('remote', 'add', 'origin', 'https://github.com/Userwei0418/SalesBuddy-Shenma.git');
  write('frontend/scripts/package_frontend.py', fs.readFileSync(sourceScript));
  write('scripts/check_isolation.py', fs.readFileSync(isolationScript));
  write('frontend/README.md', '# 前端\n\n[当前状态](../docs/CURRENT_STATUS.md)\n');
  write('frontend/VERSION.json', JSON.stringify({artifact_kind:'source_tree', source_package_date:'2026-09-12'}));
  write('frontend/project.config.json', JSON.stringify({miniprogramRoot:'miniprogram/', appid:'REPLACE_WITH_SHENMA_APPID'}));
  write('frontend/miniprogram/config.js', `module.exports={API_BASE_URL:${JSON.stringify(customerApi)}};`);
  write('frontend/miniprogram/app.json', JSON.stringify({pages:['pages/index/index']}));
  for (const name of ['app.js','app.wxss','utils/apiClient.js','pages/index/index.js','pages/index/index.wxss','pages/index/index.wxml']) {
    write('frontend/miniprogram/' + name, '');
  }
  write('frontend/miniprogram/pages/index/index.json', '{}');
  write('docs/CURRENT_STATUS.md', '# 当前状态\n');
  write('docs/DEMO.md', '# 演示手册\n');
  write('backend/src/service.py', '# deployed backend');
  write('backend/openapi/openapi.yaml', 'openapi: 3.1.0\ninfo: {title: Packaging fixture, version: v1}\npaths: {}\n');
  write('database/baseline.sql', '-- deployed schema');
  // Even accidentally tracked local artifacts must not be distributed.
  for (const name of ['project.private.config.json', '.env.local', 'node_modules/private.js', 'local.key', 'CHECKSUMS.sha256']) {
    write('frontend/' + name, 'do not distribute this local artifact');
  }
  const commit = () => {git('add', '.');git('commit', '-qm', 'fixture');return git('rev-parse', 'HEAD');};
  const deployed = commit();
  // Exercise the real guard without network or CI account credentials.
  const bin = path.join(root, 'bin');
  fs.mkdirSync(bin);
  const metadata = path.join(root, 'github-fixture.json');
  const setVisibility = isPrivate => fs.writeFileSync(metadata, JSON.stringify({
    full_name:'Userwei0418/SalesBuddy-Shenma', private:isPrivate,
  }));
  setVisibility(true);
  fs.writeFileSync(path.join(bin, 'gh'), '#!/usr/bin/env node\nprocess.stdout.write(require("node:fs").readFileSync(process.env.GH_FIXTURE_JSON, "utf8"));\n', {mode:0o755});
  const run = (...extra) => spawnSync('python3', [path.join(repo,'frontend/scripts/package_frontend.py'),
    '--output-dir', path.join(root,'delivery'), '--release-name','fixture-release',
    '--backend-revision',deployed,'--database-version','V073',...extra], {encoding:'utf8',
      env:{...process.env, PATH:bin+path.delimiter+process.env.PATH, GH_FIXTURE_JSON:metadata}});
  return {root, repo, write, git, commit, deployed, run, setVisibility};
}

function inspectZip(filename) {
  return JSON.parse(execFileSync('python3', ['-c', `
import hashlib,json,sys,zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    assert z.testzip() is None
    names=z.namelist()
    prefix=names[0].split('/')[0]+'/'
    for base in ('','frontend/'):
        for line in z.read(prefix+base+'CHECKSUMS.sha256').decode().splitlines():
            digest,name=line.split('  ',1)
            assert hashlib.sha256(z.read(prefix+base+name)).hexdigest()==digest
    print(json.dumps({'names':[n[len(prefix):] for n in names],
        'version':json.loads(z.read(prefix+'frontend/VERSION.json'))}))
`, filename], {encoding:'utf8'}));
}

test('交付来自最终提交，包含可导入项目及版本；本地缓存和私有配置不进入包', t => {
  const f = fixture(t);
  f.write('frontend/miniprogram/pages/index/index.wxml', '<view>latest committed UI</view>');
  f.write('docs/evidence/final/check.json', '{"passed":true}');
  f.write('docs/evidence/final/local.key', 'must not be distributed');
  const head = f.commit();
  const result = f.run('--include-doc','docs/DEMO.md','--include-evidence','docs/evidence/final');
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  const inspected = inspectZip(output.zip);
  assert.equal(inspected.version.frontend_revision, head);
  assert.equal(inspected.version.backend_revision, f.deployed);
  assert.equal(inspected.version.api_base_url, customerApi);
  assert.equal(inspected.version.artifact_kind, 'frontend_handoff');
  assert.equal(inspected.version.contains_private_account_guide, false);
  for (const name of ['frontend/project.config.json','frontend/miniprogram/pages/index/index.wxml','docs/CURRENT_STATUS.md','docs/DEMO.md','docs/evidence/final/check.json','接口说明/openapi.yaml']) {
    assert.ok(inspected.names.includes(name), name);
  }
  assert.equal(inspected.names.some(name => /private|node_modules|\.env|local\.key/.test(name)), false);
  assert.equal(output.sha256,crypto.createHash('sha256').update(fs.readFileSync(output.zip)).digest('hex'));
  assert.equal(f.git('status','--porcelain'), '');
  assert.notEqual(f.run().status,0, 'must not overwrite an existing release');
});

test('仅显式授权传入仓库外账号说明时随本地包交付，校验覆盖但不输出内容', t => {
  const f = fixture(t);
  const privateFile = path.join(f.root,'accounts.md');
  const privateContent = 'packaging-fixture-private-instructions';
  fs.writeFileSync(privateFile, privateContent);
  const result = f.run('--private-accounts-file',privateFile);
  assert.equal(result.status,0,result.stderr);
  assert.equal((result.stdout+result.stderr).includes(privateContent),false);
  const output = JSON.parse(result.stdout);
  const inspected = inspectZip(output.zip);
  assert.ok(inspected.names.includes('交付说明/账号登录说明.md'));
  assert.equal(inspected.version.contains_private_account_guide,true);
  assert.equal(fs.readFileSync(path.join(output.directory,'交付说明/账号登录说明.md'),'utf8'),privateContent);
  assert.equal(f.git('status','--porcelain'),'');
});

test('未提交源码或未部署的后端变化不能冒充最终交付版本', t => {
  const f = fixture(t);
  f.write('frontend/miniprogram/pages/index/index.wxml','uncommitted UI');
  const dirty = f.run();
  assert.notEqual(dirty.status,0);assert.match(dirty.stderr,/未提交/);
  f.commit();
  f.write('backend/src/service.py','# not deployed yet');f.commit();
  const incompatible = f.run();
  assert.notEqual(incompatible.status,0);assert.match(incompatible.stderr,/运行后端/);
  assert.equal(fs.existsSync(path.join(f.root,'delivery')),false);
});

test('拒绝仓库内交付、仓库内账号说明、越界文档和不完整小程序源码', t => {
  const f = fixture(t);
  for (const args of [
    ['--output-dir',path.join(f.repo,'delivery')],
    ['--private-accounts-file',path.join(f.repo,'docs/DEMO.md')],
    ['--include-doc','docs/../../outside.md'],
    ['--include-evidence','docs/evidence/../../backend'],
    ['--include-evidence','backend'],
  ]) assert.notEqual(f.run(...args).status,0);
  f.git('rm','frontend/miniprogram/pages/index/index.wxml');f.commit();
  const result=f.run();assert.notEqual(result.status,0);assert.match(result.stderr,/缺少/);
});

test('公开仓库不能打包，私有状态核验后保留纯 JSON 输出', t => {
  const f = fixture(t);
  f.setVisibility(false);
  const rejected = f.run();
  assert.notEqual(rejected.status, 0);
  assert.match(rejected.stderr, /must be PRIVATE/);
  assert.equal(fs.existsSync(path.join(f.root, 'delivery')), false);
  f.setVisibility(true);
  const accepted = f.run();
  assert.equal(accepted.status, 0, accepted.stderr);
  assert.equal(JSON.parse(accepted.stdout).backend_revision, f.deployed);
});
