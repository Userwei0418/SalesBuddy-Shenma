import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {createSalesWebServer} from '../server.mjs';

test('preview-only capability is explicit and disables accidentally supplied connection settings', async () => {
  for (const previewOnly of [false, true]) {
    const server = createSalesWebServer({previewOnly, target: previewOnly ? 'not-a-valid-target' : '', localLogin: null});
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const base = `http://127.0.0.1:${server.address().port}`;
    try {
      assert.deepEqual(await (await fetch(base + '/web-capabilities')).json(), {previewOnly});
      const connection = await (await fetch(base + '/connection-status')).json();
      assert.equal(connection.configured, false);
      assert.equal(connection.localQuickLogin.available, false);
      const api = await fetch(base + '/api/v1/customers');
      assert.equal(api.status, 503);
      assert.match((await api.json()).detail, previewOnly ? /不支持企业账号登录/ : /SALES_WEB_API_TARGET/);
    } finally {
      await new Promise(resolve => {server.close(resolve); server.closeAllConnections();});
    }
  }
});

test('cloud review launcher disables the backend even when an API target is inherited', {timeout: 15000}, async () => {
  const child = spawn(process.execPath, ['scripts/start_preview.mjs'], {
    cwd: new URL('..', import.meta.url),
    env: {...process.env, PORT: '0', HOST: '127.0.0.1', SALES_WEB_API_TARGET: 'https://review-disabled.invalid/api/v1'},
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const exited = once(child, 'exit');
  try {
    const base = await new Promise((resolve, reject) => {
      let output = '';
      const timer = setTimeout(() => reject(new Error('preview startup timed out')), 5000);
      child.once('error', error => {clearTimeout(timer); reject(error);});
      child.once('exit', () => {clearTimeout(timer); reject(new Error('preview exited before startup'));});
      child.stdout.on('data', bytes => {
        output += bytes;
        const match = output.match(/http:\/\/127\.0\.0\.1:\d+/);
        if (match) {clearTimeout(timer); resolve(match[0]);}
      });
    });
    const connection = await (await fetch(base + '/connection-status')).json();
    assert.equal(connection.configured, false);
    assert.equal(connection.reachable, false);
    assert.equal(connection.previewOnly, true);
    assert.equal(connection.localQuickLogin.available, false);
    assert.deepEqual(await (await fetch(base + '/web-capabilities')).json(), {previewOnly: true});
    const api = await fetch(base + '/api/v1/customers');
    assert.equal(api.status, 503);
    assert.match((await api.json()).detail, /不支持企业账号登录/);
    assert.equal((await fetch(base + '/?mode=preview')).status, 200);
    assert.equal((await fetch(base + '/design-system/index.html')).status, 200);
  } finally {
    child.kill();
    await exited;
  }
});
