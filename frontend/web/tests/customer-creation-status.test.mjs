import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));

test('customer creation request list uses the mine endpoint and bounded pagination', () => {
  const source = fs.readFileSync(path.join(root, 'source/miniprogram/utils/apiClient.js'), 'utf8');
  const match = source.match(/function listCustomerCreationRequests\([\s\S]*?\n}\n\nfunction updateCustomer/);
  assert.ok(match, 'listCustomerCreationRequests source is present');
  const context = {request: value => value};
  vm.runInNewContext(`${match[0].replace(/\n\nfunction updateCustomer[\s\S]*$/, '')}\nthis.listCustomerCreationRequests = listCustomerCreationRequests;`, context);
  const request = context.listCustomerCreationRequests({status: 'rejected', limit: 999, offset: -2});
  assert.equal(request.path, '/customers/creation-requests/mine?status=rejected&limit=100&offset=0');
});

test('customer create page loads request statuses and Web renders all approval states', () => {
  const page = fs.readFileSync(path.join(root, 'source/miniprogram/pages/customer-create/index.js'), 'utf8');
  const component = fs.readFileSync(path.join(root, 'department-ui/CustomerCreate.jsx'), 'utf8');
  assert.match(page, /apiClient\.listCustomerCreationRequests\(\{limit: 20\}\)/);
  assert.match(page, /creationRequests: Array\.isArray\(result\?\.items\)/);
  for (const status of ['pending', 'approved', 'rejected']) assert.match(component, new RegExp(`${status}:`));
  assert.match(component, /我的新建申请/);
  assert.match(component, /decision_reason/);
});
