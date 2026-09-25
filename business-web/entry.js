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
    entryMessage('启动商汤销售小浣熊 Web', '请先双击交付目录中的「启动小浣熊SalesBuddy.command」，再进入工作空间。启动程序会构建页面并打开本地服务。', '进入本地工作空间 →', 'http://127.0.0.1:5186/');
    return;
  }
  try {
    // Older / static deployments have no capability endpoint and retain their normal modes.
    // This local metadata request never probes the business backend.
    let previewOnly = false;
    try {
      const response = await fetch('/web-capabilities', {cache: 'no-store', signal: AbortSignal.timeout(2000)});
      if (response.ok && response.headers.get('content-type')?.includes('application/json')) previewOnly = (await response.json()).previewOnly === true;
    } catch (_) {}
    window.SALES_SERVICE = Object.freeze({previewOnly});
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
    for (const source of ['assets/vendor/flatpickr-4.6.13/flatpickr.min.js', 'assets/vendor/flatpickr-4.6.13/zh.js', 'date-picker.js', 'assets/vendor/tom-select-2.6.2/tom-select.complete.min.js', 'select-components.js', 'assets/vendor/echarts-6.1.0/echarts.min.js', 'dashboard-charts.js', 'task-workspace.js', 'home-activity.js', 'detail-workspace.js', 'bundle.js', 'preview-workflow.js', 'preview-api.js', 'crm-preview-ui.js', 'browser-platform.js', 'review-forms.js', 'review-pages.js', 'runtime.js', 'department-ui/app.js', 'review-lists.js', 'review-interactions.js', 'shell.js']) {
      await new Promise((resolve, reject) => {
        const script = document.createElement('script'); script.src = source;
        script.onload = resolve; script.onerror = () => reject(new Error('业务文件加载失败'));
        document.body.append(script);
      });
    }
  } catch (_) {
    entryMessage('工作空间暂时无法打开', '请检查本地服务是否已启动，或重新运行「启动小浣熊SalesBuddy.command」后重试。', '重新打开 →', location.href);
  }
})();
