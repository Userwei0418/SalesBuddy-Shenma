(async () => {
  'use strict';
  function entryMessage(title, description, linkText, href) {
    document.body.classList.add('web-entry-message');
    const panel = document.createElement('main');
    panel.className = 'web-entry-card';
    const heading = document.createElement('h1'); heading.textContent = title;
    const copy = document.createElement('p'); copy.textContent = description;
    const link = document.createElement('a'); link.textContent = linkText; link.href = href;
    panel.append(heading, copy, link); document.body.append(panel);
  }
  if (location.protocol === 'file:') {
    entryMessage('启动神码 SalesBuddy Web', '请先双击交付目录中的「启动神码SalesBuddy.command」，再进入工作空间。启动程序会构建页面并打开本地服务。', '进入本地工作空间 →', 'http://127.0.0.1:5186/');
    return;
  }
  function adminLinkFor(service, page) {
    if (service.previewOnly) return '';
    // Static production /web has no Node metadata endpoint; its API and admin
    // are served on the same origin. Root Node entries require explicit metadata.
    const value = /^\/web(?:\/|$)/.test(page.pathname) ? '/admin' : service.adminUrl;
    if (!value) return '';
    try {
      const url = new URL(value, page.origin);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash ? url.href : '';
    } catch (_) { return ''; }
  }
  try {
    // Only the local Node entry exposes capabilities. Static /web deployments
    // use the same-origin API and must not probe a nonexistent root endpoint.
    let service = {};
    if (!/^\/web(?:\/|$)/.test(location.pathname)) {
      try {
        const response = await fetch('/web-capabilities', {cache: 'no-store', signal: AbortSignal.timeout(2000)});
        if (response.ok && response.headers.get('content-type')?.includes('application/json')) service = await response.json() || {};
      } catch (_) {}
    }
    const previewOnly = service.previewOnly === true;
    window.SALES_SERVICE = Object.freeze({previewOnly, adminUrl: adminLinkFor(service, location)});
    // The address is the single source of mode. A normal link always opens
    // enterprise mode, even after this tab previously visited the demo.
    const url = new URL(location.href);
    window.SALES_MODE = url.searchParams.get('mode') === 'preview' ? 'preview' : 'live';
    if (previewOnly && window.SALES_MODE !== 'preview') {
      url.searchParams.set('mode', 'preview');
      url.hash = '/pages/index/index';
      entryMessage('销售工作区', '选择身份后进入工作区。企业账号登录请使用企业工作区地址。', '进入工作区 →', url.href);
      return;
    }
    sessionStorage.removeItem('sales-web:mode');
    url.searchParams.set('mode', window.SALES_MODE);
    history.replaceState(history.state, '', url.href);
    const sources = ['assets/vendor/flatpickr-4.6.13/flatpickr.min.js', 'assets/vendor/flatpickr-4.6.13/zh.js', 'date-picker.js', 'assets/vendor/tom-select-2.6.2/tom-select.complete.min.js', 'select-components.js', 'assets/vendor/echarts-6.1.0/echarts.min.js', 'dashboard-charts.js', 'task-workspace.js', 'home-activity.js', 'detail-workspace.js', 'bundle.js', 'preview-workflow.js', 'preview-api.js', 'crm-preview-ui.js', 'browser-platform.js', 'review-forms.js', 'review-pages.js', 'runtime.js', 'department-ui/app.js', 'review-lists.js', 'review-interactions.js', 'shell.js'];
    // Download concurrently, but keep dependency execution and fail-stop order.
    // The URLs and credentials match the subsequent classic script requests.
    for (const source of sources) {
      const preload = document.createElement('link');
      preload.rel = 'preload'; preload.as = 'script'; preload.href = source;
      document.head.append(preload);
    }
    const deadline = Date.now() + 60000;
    for (const source of sources) {
      await new Promise((resolve, reject) => {
        const script = document.createElement('script'); script.src = source;
        let settled = false;
        const finish = error => {
          if (settled) return;
          settled = true; clearTimeout(timeout);
          script.onload = script.onerror = null;
          if (error) { script.remove(); reject(error); } else resolve();
        };
        const timeout = setTimeout(() => finish(new Error('业务文件加载超时')), Math.max(0, deadline - Date.now()));
        script.onload = () => finish(); script.onerror = () => finish(new Error('业务文件加载失败'));
        document.body.append(script);
      });
    }
  } catch (_) {
    entryMessage('工作空间暂时无法打开', '页面文件未能完成加载，请检查网络后重新打开。', '重新打开 →', location.href);
  }
})();
