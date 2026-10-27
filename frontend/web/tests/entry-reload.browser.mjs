import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '/Users/zhouwei5/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');

test('retry after a script failure reloads the same hash route and preserves session storage', async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    const source = readFileSync(process.env.ENTRY_SOURCE || new URL('../entry.js', import.meta.url), 'utf8');
    let documents = 0;
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      assert.equal(url.origin, 'https://entry-reload.test');
      if (request.isNavigationRequest()) {
        documents++;
        return route.fulfill({contentType:'text/html', body:'<!doctype html><meta charset="utf-8"><body><script src="entry.js"></script></body>'});
      }
      if (url.pathname.endsWith('/entry.js')) return route.fulfill({contentType:'application/javascript',body:source});
      if (documents === 1 && url.pathname.endsWith('/bundle.js')) return route.abort();
      return route.fulfill({contentType:'application/javascript',body:url.pathname.endsWith('/shell.js') ? 'document.body.dataset.ready="true";' : ''});
    });
    const url = 'https://entry-reload.test/web/?mode=live#/pages/review/index?period=day';
    await page.goto(url);
    await page.getByRole('heading', {name:'工作空间暂时无法打开'}).waitFor();
    await page.evaluate(() => sessionStorage.setItem('acceptance-session', 'preserved'));
    const retry = page.getByRole('link', {name:'重新打开 →'});
    assert.equal(await retry.getAttribute('href'), url);
    await retry.click();
    await page.waitForFunction(() => document.body.dataset.ready === 'true', null, {timeout:3000});
    assert.equal(documents, 2);
    assert.equal(page.url(), url);
    assert.equal(await page.evaluate(() => sessionStorage.getItem('acceptance-session')), 'preserved');
    assert.equal(await page.getByRole('heading', {name:'工作空间暂时无法打开'}).count(), 0);
  } finally { await browser.close(); }
});
