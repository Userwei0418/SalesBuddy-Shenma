(async () => {
  'use strict';
  const $ = id => document.getElementById(id);
  function clearLoginError() { $('login-error').textContent = ''; $('login-error').hidden = true; }
  window.addEventListener('sales:toast', event => {
    if (SalesRuntime.current?.route !== 'pages/login/index' || !event.detail?.message || event.detail.source === 'privacy') return;
    $('login-error').textContent = event.detail.message;
    $('login-error').hidden = false;
  });
  $('page-root').addEventListener('input', () => {
    if (SalesRuntime.current?.route === 'pages/login/index') clearLoginError();
  }, true);
  $('page-root').addEventListener('click', event => {
    if (SalesRuntime.current?.route === 'pages/login/index' && event.target.closest('[data-handler="submitLogin"], [data-handler="toggleAgreement"]')) clearLoginError();
  }, true);
  const icons = [
    '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    '<path d="M3 5l6-2 6 2 6-2v16l-6 2-6-2-6 2V5zM9 3v16M15 5v16"/>',
    '<rect x="3" y="6" width="18" height="15" rx="3"/><path d="M8 6V3h8v3M3 12h18M10 12v3h4v-3"/>',
    '<path d="M4 3v18h17M8 16v-5M13 16V6M18 16v-8"/>',
    '<circle cx="12" cy="8" r="4"/><path d="M4 22v-3a8 8 0 0116 0v3"/>'
  ];
  const svg = content => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + content + '</svg>';
  const tabs = SALES_BUNDLE.config.tabBar.list;
  // 外框（侧导航、顶栏）由部门组件画；这里只维护状态，改了就 paintFrame
  const frame = {groups: [], activeKey: '', title: '工作空间', crumbGroup: '销售管理', showBack: false, showCreate: false, status: 'checking', statusText: '', date: '', environment: '', workspaceName: '企业工作空间', workspaceScope: '客户经营与销售协作', accountName: '尚未登录', accountRole: '请登录工作空间', accountTeam: '', accountAvatar: '我', accountActive: false};
  // 「成长与评估」沿用个人中心的销售画像数据。原 member-growth 页面只支持主管/经理查看指定成员，
  // 一线销售进入后会被 guard 带回工作台；这里保留菜单语义，但把销售自己的入口切到 profile 的画像页。
  let profileAlias = '';
  const paintFrame = () => globalThis.SalesDepartmentUI?.renderFrame?.(frame);
  const taskIcon = '<rect x="4" y="4" width="16" height="17" rx="3"/><path d="M9 3h6v4H9zM8 12l2 2 5-5M8 18h8"/>';
  const visitIcon = '<path d="M8 3h8l4 4v14H4V3h4zM15 3v5h5M8 12h8M8 16h5"/>';
  // PC 主导航（视觉 2.0）：一级模块 + 子菜单。key 用于选中态，path 是原页面或 PC 专属页面的路由（可带查询参数）。
  // capability 只控制入口显隐；页面本身仍由原页面的 guardPage 与后端权限把关。
  const navigationTree = [
    {key: 'workspace', text: '工作台', icon: 'grid', path: 'pages/index/index'},
    {key: 'customers', text: '客户', icon: 'customer', children: [
      {key: 'customers.list', text: '客户经营', path: 'pages/customers/index'},
      {key: 'customers.history', text: '申请与认领记录', path: 'pages/customer-history/index', capability: 'customer.read'},
      {key: 'customers.claim', text: '客户认领', path: 'pages/customer-claim/index', capability: 'customer.claim'},
      {key: 'customers.create', text: '客户建档', path: 'pages/customer-create/index', capability: ['customer.create', 'customer.create_request']} ]},
    {key: 'opportunities', text: '商机', icon: 'opportunity', children: [
      {key: 'opportunities.board', text: '商机看板', path: 'pages/opportunities/index'},
      {key: 'opportunities.list', text: '商机经营', path: 'pages/workbench/index'},
      {key: 'opportunities.create', text: '商机创建', path: 'pages/opportunity-history/index', capability: 'opportunity.read'}]},
    {key: 'visits', text: '拜访与跟进', icon: 'visit', children: [
      {key: 'visits.records', text: '跟进记录', path: 'pages/visit-records/index'},
      {key: 'visits.entry', text: '记录拜访', path: 'pages/visit-entry/index', capability: 'visit.create'}]},
    {key: 'tasks', text: '任务', icon: 'task', children: [
      {key: 'tasks.list', text: '任务列表', path: 'pages/tasks/index'},
      {key: 'tasks.create', text: '创建任务', path: 'pages/management-task-create/index', capability: 'task.create'}]},
    {key: 'reports', text: '工作报告', icon: 'report', children: [
      {key: 'reports.day', text: '日报 · 今日', path: 'pages/work-report/index?period=day'},
      {key: 'reports.week', text: '周报 · 本周', path: 'pages/work-report/index?period=week'},
      {key: 'reports.biweek', text: '双周报 · 上周＋本周', path: 'pages/work-report/index?period=biweek'},
      {key: 'reports.month', text: '月报 · 本月', path: 'pages/work-report/index?period=month'},
      {key: 'reports.quarter', text: '季度报 · 本季度', path: 'pages/work-report/index?period=quarter'}]},
    {key: 'insights', text: '经营分析', icon: 'chart', children: [
      {key: 'insights.bi', text: '经营看板', path: 'pages/bi/index'},
      {key: 'insights.growth', text: '成长与评估', path: 'pages/member-growth/index'},
      {key: 'insights.risks', text: '风险预警', path: 'pages/risks/index'}]},
    {key: 'knowledge', text: '知识库', icon: 'book', children: [
      {key: 'knowledge.catalog', text: '方案与定价', path: 'pages/knowledge/index?tab=catalog'},
      {key: 'knowledge.playbook', text: '客户档案 Playbook', path: 'pages/knowledge/index?tab=playbook'},
      {key: 'knowledge.proposal', text: '方案生成', path: 'pages/knowledge/index?tab=proposal'}]},
  ];
  // 详情、表单等二级页面挂到所属的导航项上，用于选中态与面包屑。
  const routeLeaf = {
    index: 'workspace', risks: 'insights.risks', 'risk-detail': 'insights.risks',
    customers: 'customers.list', 'customer-detail': 'customers.list', 'customer-edit': 'customers.list', 'customer-assign-confirm': 'customers.create', 'customer-assets': 'customers.list',
    'customer-history': 'customers.history', 'customer-claim': 'customers.claim', 'customer-create': 'customers.create',
    workbench: 'opportunities.list', opportunities: 'opportunities.board', 'opportunity-history': 'opportunities.create', 'opportunity-create': 'opportunities.create', 'demo-create': 'opportunities.list',
    'visit-records': 'visits.records', 'visit-detail': 'visits.records', 'visit-entry': 'visits.entry', 'visit-confirm': 'visits.entry',
    tasks: 'tasks.list', 'task-detail': 'tasks.list', 'management-task-create': 'tasks.create',
    'work-report': 'reports.day', 'weekly-report': 'reports.week',
    bi: 'insights.bi', 'report-detail': 'insights.bi', 'fde-records': 'insights.bi', 'member-growth': 'insights.growth',
    knowledge: 'knowledge.catalog',
  };
  const navLeaves = navigationTree.flatMap(group => group.children ? group.children.map(item => ({...item, group})) : [{...group, group: null}]);
  function leafFor(path, query = {}, current = null) {
    const name = path.split('/')[1] || '';
    if (name === 'work-report') return ['day', 'week', 'biweek', 'month', 'quarter'].includes(query.period) ? 'reports.' + query.period : 'reports.day';
    if (name === 'knowledge') return 'knowledge.' + (['catalog', 'playbook', 'proposal'].includes(query.tab) ? query.tab : 'catalog');
    if (name === 'customer-assets' && current?.data?.opportunityId) return 'opportunities.list';
    return routeLeaf[name] || '';
  }
  // 手机宽度仍用原底部五个入口（与小程序一致）；图标沿用原来的路径
  const mobileIcons = {'pages/index/index': icons[0], 'pages/customers/index': icons[1], 'pages/workbench/index': icons[2], 'pages/bi/index': icons[3], 'pages/profile/index': icons[4]};
  function navButton(t, i, small = false) {
    const b = document.createElement('button');
    b.className = 'web-nav-item';
    b.innerHTML = svg(t.icon || icons[i]) + '<span></span>';
    b.querySelector('span').textContent = t.text;
    b.dataset.path = t.pagePath;
    if (t.capability) b.dataset.capability = t.capability;
    b.onclick = () => SalesRuntime.userRoute('/' + t.pagePath, {tab: tabs.some(tab => tab.pagePath === t.pagePath)});
    if (small) b.setAttribute('aria-label', t.text);
    return b;
  }
  frame.groups = navigationTree;
  // 侧栏是一级导航：进入新模块时收起原页面栈（原 tab 页保留缓存），返回按钮只在二级页面出现。
  frame.onSelect = path => {
    if (path === 'pages/member-growth/index') {
      profileAlias = 'growth';
      const page = SalesRuntime.userRoute('/pages/profile/index', {tab: true});
      queueMicrotask(() => {
        if (SalesRuntime.current === page && typeof page?.selectProfileTab === 'function') page.selectProfileTab({currentTarget: {dataset: {tab: 'profile'}}});
      });
      return page;
    }
    profileAlias = '';
    return SalesRuntime.userRoute('/' + path, {tab: true});
  };
  frame.adminLink = window.SALES_SERVICE?.adminUrl || '';
  const authAdminLink = $('auth-admin-link');
  if (frame.adminLink) { authAdminLink.href = frame.adminLink; authAdminLink.hidden = false; }
  tabs.forEach((t, i) => {
    $('mobile-nav').append(navButton({...t, icon: mobileIcons[t.pagePath] || icons[i]}, i, true));
  });
  const quick = [
    {text: '记录客户拜访', path: 'pages/visit-entry/index', capability: 'visit.create', name: 'mic', icon: '<path d="M12 3a3 3 0 013 3v6a3 3 0 01-6 0V6a3 3 0 013-3zM5 10v2a7 7 0 0014 0v-2M12 19v3M8 22h8"/>'},
    {text: '新增商机', path: 'pages/opportunity-create/index', capability: 'opportunity.create', name: 'opportunity', icon: icons[2]},
    {text: '创建任务', path: 'pages/management-task-create/index', capability: 'task.create', name: 'task', icon: '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 12h8M12 8v8"/>'},
    {text: '客户建档', path: 'pages/customer-create/index', capability: ['customer.create', 'customer.create_request'], name: 'building', icon: '<path d="M3 21V5h12v16M7 9h4M7 13h4M7 17h4M18 5v8M14 9h8"/>'},
    {text: '建档并下发', path: 'pages/customer-assign-confirm/index', capability: 'customer.create', name: 'send', icon: '<path d="M3 21V5h12v16M7 9h4M7 13h4M16 14h6m-3-3l3 3-3 3"/>'}
  ];
  const availableQuickActions = () => {
    const session = SalesRuntime.app?.globalData?.session;
    return session && !session.mustChangePassword ? quick.filter(t => !t.capability || SalesRuntime.app.can(t.capability)) : [];
  };
  function renderQuickActions() {
    const actions = $('mobile-quick-actions'), available = availableQuickActions();
    const signature = available.map(t => t.path).join('|');
    if (actions.dataset.actions === signature) return;
    actions.dataset.actions = signature; actions.replaceChildren();
    available.forEach(t => {
      const button = document.createElement('button'); button.className = 'web-nav-item web-quick-item';
      button.innerHTML = svg(t.icon) + '<span></span>'; button.querySelector('span').textContent = t.text; button.dataset.path = t.path;
      button.onclick = () => {
        if (!availableQuickActions().some(action => action.path === t.path)) return;
        $('quick-dialog').close(); SalesRuntime.userRoute('/' + t.path);
      };
      actions.append(button);
    });
  }
  frame.date = new Date().toLocaleDateString('zh-CN', {month: 'long', day: 'numeric', weekday: 'long', timeZone: 'Asia/Shanghai'});
  const preview = SALES_MODE === 'preview';
  const previewOnly = window.SALES_SERVICE?.previewOnly === true;
  if (previewOnly) {
    for (const id of ['enter-live', 'choose-live', 'preview-enter-live']) { $(id).hidden = true; $(id).disabled = true; }
    document.querySelector('#preview-entry-choice > p').textContent = '选择身份后进入工作区。';
  }
  let crmData = null;
  document.body.dataset.mode = SALES_MODE;
  $('preview-notice').hidden = !preview;
  $('preview-visit-example').textContent = '沟通内容：今天与客户讨论试点范围，对方希望先评估两条产线。\n下一步计划：明天由我整理试点范围并发送验收清单给客户。\n跟进日期：今天\n对接人：张晓静';
  frame.environment = preview ? '' : '企业工作区';
  const loginUiKey = `sales-web:login-ui:${SALES_MODE}`;
  let connection = null, localLoginBusy = false, loginTransportBusy = 0, manualLoginOpen = !preview && sessionStorage.getItem(loginUiKey) === 'manual';
  if (preview) sessionStorage.removeItem(loginUiKey);
  let exitIntent = '', loginNotice = sessionStorage.getItem(`sales-web:login-notice:${SALES_MODE}`) || '';
  const roleLabels = {sales: '一线销售', supervisor: '销售主管', manager: '销售总经理', fde: 'FDE', fde_lead: 'FDE主管'};
  for (const option of $('preview-role').options) if (roleLabels[option.value]) option.textContent = roleLabels[option.value];
  if (preview) document.querySelector('.web-auth-form-heading > h2').textContent = '销售工作区';
  for (const option of $('preview-role').options) $('preview-entry-role').append(option.cloneNode(true));
  function setPreviewRole(value) {
    const role = Object.hasOwn(roleLabels, value) ? value : 'sales';
    sessionStorage.setItem('sales-web:preview-role', role);
    $('preview-role').value = role;
    $('preview-entry-role').value = role;
    return role;
  }
  setPreviewRole(sessionStorage.getItem('sales-web:preview-role'));
  // Both entry methods share the original Page consent state, which starts unchecked.
  const consent = document.createElement('div'); consent.id = 'local-login-consent'; consent.className = 'web-local-consent';
  const consentLabel = document.createElement('label');
  const consentInput = document.createElement('input'); consentInput.type = 'checkbox'; consentInput.id = 'local-login-agreement';
  consentLabel.append(consentInput, document.createTextNode('我已阅读并同意'));
  const privacyButton = document.createElement('button'); privacyButton.type = 'button'; privacyButton.id = 'local-login-privacy'; privacyButton.textContent = '隐私与数据使用说明';
  consent.append(consentLabel, privacyButton); $('local-login-description').after(consent);
  consentInput.onchange = () => {
    const page = SalesRuntime.current;
    if (page?.route !== 'pages/login/index' || localLoginBusy || loginTransportBusy || page.data.loading) return;
    page.setData({agreed: consentInput.checked}); clearLoginError();
  };
  privacyButton.onclick = () => {
    const page = SalesRuntime.current;
    if (page?.route === 'pages/login/index') page.openPrivacy?.();
  };
  let loginFocusRequest = 0;
  function focusEmptyLoginAccount() {
    const owner = SalesRuntime.current, trigger = document.activeElement, request = ++loginFocusRequest;
    requestAnimationFrame(() => {
      if (request !== loginFocusRequest || SalesRuntime.current !== owner || owner?.route !== 'pages/login/index' || owner._destroyed || !manualLoginOpen) return;
      const root = $('page-root'), account = root.querySelector('.form-area .field-group:first-child input'), active = document.activeElement;
      if (!account || account.disabled || !account.getClientRects().length || active === account) return;
      // A newer focus or any input means the user already started; never redirect their keystrokes.
      if (root.contains(active) || active !== trigger && active !== document.body && active !== document.documentElement) return;
      if (owner.data.account || owner.data.password || owner.data.newPassword || [...root.querySelectorAll('input:not([type="checkbox"]):not([type="radio"]), textarea')].some(input => input.value)) return;
      account.focus();
    });
  }
  function setManualLogin(open) {
    manualLoginOpen = open; sessionStorage.setItem(loginUiKey, open ? 'manual' : 'quick'); updateLocalLogin();
    if (open) focusEmptyLoginAccount();
  }
  function localLoginAvailable() {
    return !preview && ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname) && connection?.localQuickLogin?.available === true && connection.localQuickLogin.role === 'sales';
  }
  function updateLocalLogin() {
    const page = SalesRuntime.current;
    const available = localLoginAvailable() && page?.route === 'pages/login/index' && !page.data.mustChangePassword;
    const busy = localLoginBusy || loginTransportBusy > 0 || !!page?.data.loading;
    $('local-login-choice').hidden = !available;
    document.body.classList.toggle('web-local-login-available', available);
    document.body.classList.toggle('web-manual-login-open', manualLoginOpen);
    $('local-login-button').disabled = busy;
    $('local-login-button').hidden = manualLoginOpen;
    $('local-login-description').hidden = manualLoginOpen;
    consent.hidden = !available || manualLoginOpen;
    consentInput.checked = page?.data.agreed === true;
    consentInput.disabled = busy;
    privacyButton.disabled = busy;
    $('local-login-button').textContent = localLoginBusy ? '正在登录…' : '一键进入 · 一线销售';
    $('local-login-button').setAttribute('aria-busy', String(localLoginBusy));
    $('show-account-login').disabled = busy;
    $('show-account-login').textContent = manualLoginOpen ? '改用本机快捷账号（一线销售）' : '使用其他企业账号';
    $('cancel-password-change').hidden = !page?.data.mustChangePassword || page.route !== 'pages/login/index';
    $('cancel-password-change').disabled = busy;
    $('login-session-notice').hidden = page?.route !== 'pages/login/index' || !loginNotice;
    $('login-session-notice').textContent = loginNotice;
    document.body.classList.toggle('web-has-login-notice', page?.route === 'pages/login/index' && !!loginNotice);
  }
  window.addEventListener('sales:login-transport', event => {
    loginTransportBusy = Math.max(0, loginTransportBusy + (event.detail?.pending ? 1 : -1));
    updateLocalLogin();
    // Original Promise handlers update Page loading / mustChangePassword after transport completion.
    if (!event.detail?.pending) setTimeout(updateLocalLogin, 0);
  });
  $('show-account-login').onclick = () => setManualLogin(!manualLoginOpen);
  $('local-login-button').onclick = async () => {
    const page = SalesRuntime.current, app = SalesRuntime.app;
    if (!localLoginAvailable() || manualLoginOpen || page?.route !== 'pages/login/index' || page.data.mustChangePassword || localLoginBusy || loginTransportBusy || page.data.loading) return;
    if (page.data.agreed !== true) { SalesRuntime.wx.showToast({title: '请先同意隐私与数据使用说明'}); consentInput.focus(); return; }
    clearLoginError(); localLoginBusy = true; page.setData({loading: true}); updateLocalLogin();
    try {
      const session = await SalesPlatform.withLocalLogin(() => app.loginWithApi(null, '', ''));
      if (!session) throw new Error('登录账号无效');
      if (SalesRuntime.current !== page) return;
      if (session.mustChangePassword) {
        manualLoginOpen = true;
        page.setData({loading: false, mustChangePassword: true, account: session.account || '', password: '', newPassword: ''});
        SalesRuntime.wx.showToast({title: '该账号需要首次修改密码，请先完成页面中的密码修改。'});
      } else {
        page.setData({loading: false, password: ''});
        SalesRuntime.wx.switchTab({url: '/pages/index/index'});
      }
    } catch (error) {
      if (SalesRuntime.current === page) { page.setData({loading: false}); SalesRuntime.wx.showToast({title: error.message || '一键登录失败，请稍后重试'}); }
    } finally {
      localLoginBusy = false;
      if (SalesRuntime.current === page && !page._destroyed) page.setData({loading: false});
      updateLocalLogin();
    }
  };
  function updateConnection() {
    const session = SalesRuntime.app?.globalData?.session;
    // Public readiness describes the service, not this company's effective models.
    // Company-owned text/ASR connections may work without a global model gateway.
    const text = preview ? '合成数据演示' : connection?.reachable ? ('服务已连接' + (!session ? ' · 请登录' : '')) : connection?.configured === false ? '服务尚未配置' : connection ? '服务暂不可用' : '正在检查服务';
    frame.statusText = text; frame.status = preview ? 'preview' : connection?.reachable ? 'ready' : connection ? 'unavailable' : 'checking'; paintFrame();
    for (const id of ['login-connection-status']) {
      $(id).textContent = text; $(id).dataset.state = frame.status;
      $(id).title = [typeof connection?.label === 'string' ? connection.label : typeof connection?.environment === 'string' ? connection.environment : '', connection?.checkedAt ? '检查时间：' + new Date(connection.checkedAt).toLocaleTimeString('zh-CN') : ''].filter(Boolean).join(' · ');
    }
    updateLocalLogin();
  }
  // Production Web is served from /web/ by Nginx, where the local Node
  // server's /connection-status route does not exist. Reuse the same-origin
  // API readiness endpoint there so the status badge reflects the service
  // that the page already uses for business requests.
  function connectionCheckPath() {
    return /^\/web(?:\/|$)/.test(location.pathname) ? '/api/v1/health/ready' : '/connection-status';
  }
  async function checkConnection() {
    if (preview) return updateConnection();
    try {
      const path = connectionCheckPath();
      const response = await fetch(path, {cache: 'no-store'});
      if (!response.ok) throw new Error('Connection check failed');
      const payload = await response.json();
      connection = path === '/connection-status' ? payload : {
        configured: true,
        reachable: payload?.status === 'ok',
        status: response.status,
        checks: payload?.checks || {},
        label: 'SalesBuddy 业务服务',
        environment: 'SalesBuddy 业务服务',
        detail: payload?.status === 'ok' ? '业务服务已响应，登录后读取授权数据' : '业务服务暂时不可用',
        checkedAt: new Date().toISOString()
      };
    }
    catch (_) { connection = {reachable: false}; }
    updateConnection();
  }
  void checkConnection();
  sessionStorage.removeItem('sales-web:customer-view');
  function setMode(mode) {
    if (!['preview', 'live'].includes(mode)) return;
    if (previewOnly && mode === 'live') return;
    sessionStorage.removeItem('sales-web:pending-path:' + mode);
    if (mode === 'preview') sessionStorage.removeItem('sales-web:signed-out:preview');
    // Object IDs and deferred deep links belong to one data source. Explicit
    // mode entry starts at overview instead of carrying a record across modes.
    const url = new URL(location.href); url.searchParams.set('mode', mode);
    url.hash = '/pages/index/index';
    history.replaceState(null, '', url.href); location.reload();
  }
  const dialog = $('workspace-dialog');
  frame.onWorkspace = () => dialog.showModal();
  $('close-workspace').onclick = () => dialog.close();
  $('choose-live').onclick = () => setMode('live');
  $('choose-preview').onclick = () => setMode('preview');
  $('login-preview').onclick = () => setMode('preview');
  $('enter-live').onclick = () => setMode('live');
  $('preview-enter-live').onclick = () => setMode('live');
  $('preview-enter').onclick = () => {
    setPreviewRole($('preview-entry-role').value);
    setMode('preview');
  };
  const accountDialog = $('account-dialog'); let accountTrigger = null, accountFocusAfterClose = null, exitBusy = false;
  function openAccount(event) {
    const session = SalesRuntime.app?.globalData?.session;
    if (!session || session.mustChangePassword) return;
    accountTrigger = event?.currentTarget?.closest?.('button') || document.querySelector('#frame-sidenav button') || document.body;
    $('account-dialog-name').textContent = session.userName || '当前账号';
    $('account-dialog-title').textContent = '账号与登录';
    $('account-dialog-detail').textContent = [session.roleName, session.team || session.scope].filter(Boolean).join(' · ');
    $('account-switch').firstChild.textContent = '切换账号 ';
    $('account-logout').textContent = '退出登录';
    accountTrigger.setAttribute('aria-expanded', 'true'); accountDialog.showModal();
    $('account-profile').focus();
  }
  frame.onAccount = openAccount; $('mobile-account-button').onclick = openAccount;
  $('close-account').onclick = () => accountDialog.close();
  accountDialog.addEventListener('close', () => {
    accountTrigger?.setAttribute('aria-expanded', 'false');
    const target = accountFocusAfterClose || accountTrigger; accountFocusAfterClose = null;
    if (target?.isConnected && !target.hidden) target.focus();
  });
  accountDialog.addEventListener('cancel', () => accountTrigger?.setAttribute('aria-expanded', 'false'));
  accountDialog.addEventListener('click', event => { const box = accountDialog.getBoundingClientRect(); if (event.target === accountDialog && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) accountDialog.close(); });
  accountDialog.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const buttons = [...accountDialog.querySelectorAll('button:not([disabled])')];
    const index = buttons.indexOf(document.activeElement);
    event.preventDefault(); buttons[(index + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length].focus();
  });
  $('account-profile').onclick = () => { accountDialog.close(); profileAlias = ''; SalesRuntime.userRoute('/pages/profile/index', {tab: true}); };
  async function requestAccountExit(intent) {
    if (exitBusy || localLoginBusy || loginTransportBusy || SalesRuntime.current?.data.loading && SalesRuntime.current?.route === 'pages/login/index') return;
    if (accountDialog.open) accountDialog.close();
    const session = SalesRuntime.app?.globalData?.session;
    if (!session) return;
    exitBusy = true;
    try {
      const result = await SalesRuntime.wx.showModal({title: intent === 'switch' ? '切换到其他账号？' : '退出当前账号？',
        content: `当前账号：${session.userName || session.account || '已登录账号'}。请先保存需要保留的内容，尚未保存的输入可能丢失。`,
        confirmText: intent === 'switch' ? '切换账号' : '退出登录', cancelText: '继续使用'});
      const latest = SalesRuntime.app.globalData.session;
      if (!result.confirm || !latest || latest.userId !== session.userId || latest.workspaceId !== session.workspaceId || latest.loginAt !== session.loginAt) return;
      exitIntent = intent; SalesRuntime.signOut();
      focusEmptyLoginAccount();
    } finally { exitBusy = false; exitIntent = ''; }
  }
  $('account-switch').onclick = () => {
    // 桌面宽度的演示身份在顶栏（原提示条收起），关闭对话框后焦点落到顶栏的同一个切换入口
    if (preview) { accountFocusAfterClose = (getComputedStyle($('preview-notice')).display === 'none' && document.querySelector('#frame-topbar .pc-topbar-role [role="combobox"]')) || $('preview-role'); accountDialog.close(); SalesRuntime.wx.showToast({title: '请在页面顶部选择账号'}); }
    else void requestAccountExit('switch');
  };
  $('account-logout').onclick = () => requestAccountExit('logout');
  $('cancel-password-change').onclick = () => requestAccountExit('switch');
  window.addEventListener('sales:session-ended', () => {
    const page = SalesRuntime.current;
    const explicit = exitIntent || page?.route === 'pages/profile/index' && page.data.logoutNavigating;
    if (!preview) { manualLoginOpen = true; sessionStorage.setItem(loginUiKey, 'manual'); }
    loginNotice = explicit ? (preview ? '已退出登录。' : exitIntent === 'switch' ? '已退出原账号，请登录其他企业账号，身份将由账号自动识别。' : '已退出登录，你可以重新登录或使用其他企业账号。') : '';
    sessionStorage.setItem(`sales-web:login-notice:${SALES_MODE}`, loginNotice);
    clearLoginError(); if (accountDialog.open) accountDialog.close();
  });
  frame.onBack = () => SalesRuntime.back(1, frame.backPath);
  frame.onRefresh = () => { void checkConnection(); SalesRuntime.refresh(); };
  frame.onHelp = () => $('shortcut-help-button').click();
  const openQuickActions = () => {
    if (!availableQuickActions().length) return;
    renderQuickActions();
    $('quick-dialog').showModal();
  };
  $('mobile-actions').onclick = openQuickActions;
  frame.onCreate = openQuickActions;
  frame.onCreateAction = path => { if (availableQuickActions().some(action => action.path === path)) SalesRuntime.userRoute('/' + path); };
  // 演示身份并入顶栏：沿用原生选择框的切换流程（退出当前演示账号后按新身份进入）。
  frame.onPreviewRole = value => {
    const select = $('preview-role');
    if (!preview || select.value === value || !roleLabels[value]) return;
    select.value = value; select.onchange({target: select});
  };
  $('close-quick').onclick = () => $('quick-dialog').close();
  $('preview-role').onchange = async e => {
    setPreviewRole(e.target.value);
    const app = SalesRuntime.app;
    if (app?.logout) app.logout();
    // Old in-flight requests finish in the old document and cannot update the new role.
    setMode('preview');
  };
  function navigation(detail = {}) {
    const current = SalesRuntime.current;
    const path = detail.path || current?.route || 'pages/login/index';
    if (path !== 'pages/login/index') clearLoginError();
    const session = detail.session || SalesRuntime.app?.globalData?.session;
    if (session && !session.mustChangePassword && path !== 'pages/login/index') {
      loginNotice = ''; sessionStorage.removeItem(`sales-web:login-notice:${SALES_MODE}`);
    }
    document.body.dataset.route = path.split('/')[1];
    $('preview-visit-guide').hidden = true;
    document.body.classList.toggle('web-login', path === 'pages/login/index');
    const companyFlow=preview && path==='pages/login/index' && Boolean(SalesRuntime.current?.data.workspace);
    document.body.classList.toggle('web-company-entry',companyFlow);
    $('preview-entry-choice').hidden = !preview || path !== 'pages/login/index' || companyFlow;
    const query = Object.fromEntries(new URL((current?._url || '/' + path), 'https://sales.local/').searchParams);
    const profileAliasActive = path === 'pages/profile/index' && profileAlias === 'growth';
    if (path !== 'pages/profile/index') profileAlias = '';
    const leafKey = profileAliasActive ? 'insights.growth' : leafFor(path, query, current);
    const leaf = navLeaves.find(item => item.key === leafKey);
    const leafParams = leaf?.path?.includes('?') ? [...new URLSearchParams(leaf.path.split('?')[1])] : [];
    // 旧的 pages/weekly-report/index 书签等同「周报 · 本周」菜单项
    const isLeafPage = profileAliasActive || (!!leaf && (path === 'pages/weekly-report/index' || leaf.path.split('?')[0] === path && leafParams.every(([name, value]) => query[name] === value)));
    frame.title = isLeafPage ? leaf.text : detail.title || leaf?.text || '工作空间';
    frame.crumbGroup = profileAliasActive ? '经营分析' : leaf?.group?.text || (path === 'pages/profile/index' ? '个人中心' : '');
    frame.crumbs = profileAliasActive ? ['经营分析', '成长与评估'] : path === 'pages/profile/index' ? ['个人中心'] : [leaf?.group?.text, leaf && !isLeafPage ? leaf.text : null, frame.title].filter((value, index, list) => value && list.indexOf(value) === index);
    frame.crumbItems = frame.crumbs.map((label, index) => ({label,
      path: index < frame.crumbs.length - 1 ? (label === leaf?.text ? leaf.path : leaf?.group?.children?.find(item => !item.capability || SalesRuntime.app?.can(item.capability))?.path) : null}));
    // 深链/刷新没有页面栈时回所属列表；正常进入保留原列表、筛选和翻页。
    frame.backPath = leaf?.path || 'pages/index/index';
    frame.workspaceName = crmData ? (crmData.scope === 'full' ? 'CRM 全量工作空间' : 'CRM 样本工作空间') : preview ? '渠道销售工作区' : session?.companyName || session?.team || '企业工作空间';
    frame.workspaceScope = session?.scope || '客户经营与销售协作';
    frame.showBack = !(isLeafPage || tabs.find(t => t.pagePath === path) || path === 'pages/login/index');
    frame.accountName = session?.userName || '尚未登录';
    frame.accountRole = session ? (session.roleName || '') : '请使用企业账号';
    frame.accountTeam = session ? (session.team || session.scope || '') : '';
    frame.accountAvatar = session?.userName?.slice(-2) || '我';
    $('mobile-account-button').hidden = !session || path === 'pages/login/index';
    if (accountDialog.open && (!session || path === 'pages/login/index')) accountDialog.close();
    const hasActions = !!session && path !== 'pages/login/index' && availableQuickActions().length > 0;
    $('mobile-actions').hidden = !hasActions;
    frame.showCreate = hasActions;
    frame.createActions = hasActions ? availableQuickActions().map(action => ({text: action.text, path: action.path, icon: action.name})) : [];
    const modulePath = {customers: 'pages/customers/index', opportunities: 'pages/workbench/index', insights: 'pages/bi/index'}[leaf?.group?.key] || (leafKey === 'workspace' ? 'pages/index/index' : path);
    const mobileModulePath = tabs.some(tab => tab.pagePath === modulePath) ? modulePath : 'pages/index/index';
    frame.activeKey = leafKey;
    frame.previewRoles = preview ? [...$('preview-role').options].map(option => ({value: option.value, label: option.textContent})) : [];
    frame.previewRole = preview ? $('preview-role').value : '';
    document.querySelectorAll('#mobile-nav [data-path]').forEach(b => {
      const active = b.dataset.path === mobileModulePath;
      b.classList.toggle('active', active);
      if (active) b.setAttribute('aria-current', b.dataset.path === path ? 'page' : 'location'); else b.removeAttribute('aria-current');
    });
    frame.accountActive = path === 'pages/profile/index' && !profileAliasActive;
    const hiddenFor = item => !session || (!!item.capability && !SalesRuntime.app?.can(item.capability));
    frame.groups = navigationTree.map(group => group.children ? {...group, children: group.children.map(item => ({...item, hidden: hiddenFor(item)}))} : {...group, hidden: hiddenFor(group)});
    if ($('quick-dialog').open) {
      if (!hasActions) $('quick-dialog').close();
      else renderQuickActions();
    }
    updateConnection();
    document.title = (frame.title || '工作空间') + ' · ' + (innerWidth <= 600 ? 'SalesBuddy' : '神码 SalesBuddy');
    paintFrame();
  }
  window.addEventListener('sales:navigation', e => navigation(e.detail));
  matchMedia('(max-width: 600px)').addEventListener('change', () => {
    document.title = (frame.title || '工作空间') + ' · ' + (innerWidth <= 600 ? 'SalesBuddy' : '神码 SalesBuddy');
  });
  // Source labels remain faithful in live mode; preview must never imply backend verification.
  if (preview) {
    let queued = false;
    const labelPreview = () => {
      queued = false;
      const walker = document.createTreeWalker($('page-root'), NodeFilter.SHOW_TEXT);
      const changes = [];
      while (walker.nextNode()) {
        const n = walker.currentNode;
        let value = crmData ? n.textContent.replace(/数据库/g, 'CRM 本地数据').replace(/真实业务数据/g, 'CRM 数据') : n.textContent;
        if (crmData && SalesRuntime.current?.route === 'pages/index/index') {
          value = ({'拜访记录录入成功': 'CRM 历史跟进记录', '字段已归档': '来源字段已填写', '待执行': '原文计划'})[value] || value;
        }
        if (['visit-entry', 'visit-confirm'].includes(document.body.dataset.route)) {
          const labels = {
            'AI 评分': '字段完整度', '质量等级': '字段检查结果', 'AI 质量审核': '本地字段检查', '修改前评分': '修改前检查',
            '下一步审核': '下一步字段检查',
            '拜访正文已修改，请重新 AI 审核': '拜访正文已修改，请重新执行本地字段检查',
            '需重新审核': '需重新检查',
            '下一步审核未通过，即使质量评分通过也不能提交。': '下一步日期或行动未补齐，不能提交。',
            '评估本次拜访录入质量，并给出评分与改进建议': '仅检查字段是否填写和下一步日期；未调用真实 Agent，不评估业务质量',
            '执行 AI 审核': '执行本地字段检查', 'AI 质量审核未通过，请按建议完善内容后重新审核。': '本地字段检查未通过，请补齐后重新检查。',
            '先记下拜访内容，AI 帮你整理沟通内容和下一步计划。': '输入拜访文字，按标签整理字段。',
            'AI 将识别必填信息，并在确认页提示补充': '请按字段标签填写，确认页会提示补充',
            'AI 已尝试识别，未识别到的内容请人工补充': '仅提取明确标签的内容，其余请人工补充',
            'AI 已识别': '标签已提取', 'AI/系统': '系统字段', 'AI 整理': '按标签整理',
            '转写后可以修改文字，确认无误后再交给 AI 整理。': '请直接输入文字；语音转写待接入。',
            '文件中的文字会自动提取，录音会自动转写；核对后再交给 AI 整理。': '文件提取和语音转写待接入；请直接输入文字。'
          };
          value = labels[value] || value;
          if (n.parentElement?.classList.contains('metric-number') && /^\d+分$/.test(value)) value = value.replace('分', '%');
          if (n.parentElement?.classList.contains('score-label')) value = value.replace(/^分 · /, '% · ');
          if (value.startsWith('提交标准：质量评分须')) value = '提交标准：必填字段完整，下一步计划包含明确日期和行动。';
          if (value.startsWith('AI 质量审核须')) value = '本地字段检查尚未通过，请补齐字段后重新检查。';
        }
        if (value !== n.textContent) changes.push([n, value]);
      }
      changes.forEach(([n, v]) => { n.textContent = v; });
      if (crmData) window.SalesCrmPreview.decorate($('page-root'), SalesRuntime.current);
    };
    new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(labelPreview); } }).observe($('page-root'), {childList: true, subtree: true, characterData: true});
  }
  try {
    if (preview) crmData = await SalesPreview.loadLocal();
    if (crmData) {
      document.body.dataset.previewSource = 'crm';
      $('preview-notice').firstElementChild.textContent = `${crmData.label || 'CRM 真实样本 · 本地预览'}；操作仅保存在本机，未调用真实 Agent`;
      frame.environment = crmData.scope === 'full' ? 'CRM 全量数据' : 'CRM 真实样本';
      const first = crmData.state.visits[0];
      $('preview-visit-example').textContent = `真实跟进原文（第 ${first.source_ref.row} 行，重录仅用于本地演示）\n沟通内容：${first.follow_up_record || ''}\n下一步计划：${first.next_action || ''}\n跟进日期：${first.visit_date}\n对接人：${first.contact_name_snapshot || '原表未填写'}`;
      if (sessionStorage.getItem('sales-web:preview-dataset') !== crmData.dataset_id) {
        // Clear only preview identity/drafts when switching dataset, never live sessions.
        Object.keys(sessionStorage).filter(key => key.startsWith('sales-web:preview:')).forEach(key => sessionStorage.removeItem(key));
        setPreviewRole('manager');
        sessionStorage.setItem('sales-web:preview-dataset', crmData.dataset_id);
        sessionStorage.removeItem('sales-web:signed-out:preview');
      }
      window.SalesCrmPreview.mount(crmData);
    }
    await SalesRuntime.boot();
    if (preview) {
      const role = $('preview-role').value;
      const session = SalesRuntime.app.globalData.session;
      if ((!session || session.role !== role) && !sessionStorage.getItem(`sales-web:signed-out:${SALES_MODE}`)) {
        await SalesRuntime.app.loginWithApi(role, 'PREVIEW_' + role.toUpperCase(), 'preview');
        // switchTab lets the runtime restore a protected deep link captured before login.
        SalesRuntime.route('/pages/index/index', {tab: true});
      }
    }
    navigation();
  } catch (error) {
    console.error(error);
    const box = document.createElement('div'); box.className = 'web-startup-error'; box.textContent = '工作空间暂时未能打开：' + error.message; $('page-root').append(box);
  }
})();
