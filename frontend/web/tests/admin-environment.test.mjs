import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createSalesWebServer} from '../server.mjs';
import {localBackendOptions} from '../scripts/start_local_backend.mjs';

const source = fs.readFileSync(new URL('../entry.js', import.meta.url), 'utf8');
const context = {URL};
const helper = source.match(/  function adminLinkFor\(service, page\) \{[\s\S]*?\n  \}/)?.[0];
assert.ok(helper);
vm.runInNewContext(helper + '\nthis.adminLinkFor = adminLinkFor;', context);

test('admin navigation follows local metadata and static deployment origin without a production fallback', () => {
  const root = new URL('http://127.0.0.1:5188/?mode=live');
  assert.equal(context.adminLinkFor({adminUrl: 'http://127.0.0.1:18800/admin'}, root), 'http://127.0.0.1:18800/admin');
  assert.equal(context.adminLinkFor({adminUrl: 'https://company.example/admin'}, root), 'https://company.example/admin');
  for (const path of ['/web', '/web/', '/web/index.html']) {
    assert.equal(context.adminLinkFor({}, new URL('https://company.example' + path)), 'https://company.example/admin');
  }
  for (const service of [{}, {previewOnly: true, adminUrl: 'https://company.example/admin'}, {adminUrl: 'javascript:alert(1)'}, {adminUrl: 'https://user:secret@company.example/admin'}, {adminUrl: 'https://company.example/admin?token=secret'}]) {
    assert.equal(context.adminLinkFor(service, root), '');
  }
});

test('local and configured admin metadata remains available without probing any upstream', async () => {
  const cases = [
    [localBackendOptions(), 'http://127.0.0.1:18800/admin'],
    [{target: 'https://unreachable.example/api/v1'}, 'https://unreachable.example/admin'],
    [{target: 'http://internal-api.invalid/api/v1', adminUrl: 'https://company.example/admin'}, 'https://company.example/admin'],
    [{target: 'https://unreachable.example/api/v1', previewOnly: true}, ''],
  ];
  for (const [options, expected] of cases) {
    const server = createSalesWebServer(options);
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    try {
      const response = await fetch(`http://127.0.0.1:${server.address().port}/web-capabilities`);
      assert.equal(response.status, 200);
      const body = await response.json();
      assert.equal(body.adminUrl, expected);
      assert.deepEqual(Object.keys(body).sort(), ['adminUrl', 'previewOnly']);
    } finally {await new Promise(resolve => {server.close(resolve); server.closeAllConnections();});}
  }
  assert.throws(() => createSalesWebServer({target: 'https://company.example/api/v1', adminUrl: 'https://user:secret@company.example/admin'}), /运营后台/);
});
