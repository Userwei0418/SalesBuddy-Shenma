const test = require('node:test');
const assert = require('node:assert/strict');
const config = require('../miniprogram/config');

test('Shenma credentials and company lookup ignore a stale foreign API override', async () => {
  const storage = new Map([['salesApiBaseUrl', 'https://foreign.example.invalid/api/v1']]);
  const sent = [];
  global.wx = {
    getStorageSync: key => storage.get(key),
    setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key),
    request: options => {
      sent.push(options);
      queueMicrotask(() => options.success({statusCode: 200, data: {
        access_token: 'synthetic', refresh_token: 'synthetic',
        actor: {workspace_id: 'synthetic', user_id: 'synthetic', role: 'sales'},
      }}));
      return {abort() {}};
    },
  };
  delete require.cache[require.resolve('../miniprogram/utils/apiClient')];
  const api = require('../miniprogram/utils/apiClient');
  assert.equal(config.LOCK_API_BASE_URL, true);
  assert.equal(api.getBaseUrl(), 'https://salesbuddy.shenzhoukuntai.com:28899/api/v1');
  await api.getCompanyEntry('synthetic');
  await api.loginWithAccount('synthetic', 'synthetic-password');
  assert.equal(sent.length, 2);
  assert.ok(sent.every(request => request.url.startsWith(config.API_BASE_URL + '/')));
  assert.equal(sent[1].data.password, 'synthetic-password');
  assert.equal(storage.get('salesApiBaseUrl'), 'https://foreign.example.invalid/api/v1');
  delete global.wx;
});
