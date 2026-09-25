import React from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {SbProvider, SbSideNav, SbTopBar} from '@shandiant/ui-react';
import './frame.css';

// 工作区外框：左侧 SbSideNav、顶部 SbTopBar，都由 shell.js 喂状态。
// shell.js 只维护一个 frame 对象，改了就调 renderFrame；对话框（工作区、账号、快捷操作、键盘帮助）仍是原生 dialog。
const NAV_WIDTH = {expanded: '232px', collapsed: '64px'};
const COLLAPSE_KEY = 'sales-web:sidenav-collapsed';
let sideRoot = null, topRoot = null, collapsed = false;
try { collapsed = localStorage.getItem(COLLAPSE_KEY) === '1'; } catch (_) { collapsed = false; }

// shell.js 里的图标只有路径，这里补上 svg 外壳
const Icon = ({svg}) => <span className="ds-frame-icon" aria-hidden="true" dangerouslySetInnerHTML={{__html: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">${svg}</svg>`}} />;

function applyWidth() {
  // 宽度变量写在 body 上，压过 desktop.css 里各档媒体查询的值；随后由 watchWidth 按侧栏实际宽度校正
  document.body.style.setProperty('--web-sidebar', collapsed ? NAV_WIDTH.collapsed : NAV_WIDTH.expanded);
  document.body.classList.toggle('web-sidenav-collapsed', collapsed);
}
// 容器宽度跟着侧栏实际画出来的宽度走，收起展开的动画过程中也不会裁掉内容
let watcher = null;
function watchWidth(host) {
  if (watcher || typeof ResizeObserver === 'undefined') return;
  const sync = () => {
    const sider = host.querySelector('.ant-layout-sider');
    if (!sider || getComputedStyle(host).display === 'none') return;
    const width = Math.round(sider.getBoundingClientRect().width);
    if (width > 0) document.body.style.setProperty('--web-sidebar', width + 'px');
  };
  watcher = new ResizeObserver(sync); watcher.observe(host);
  const sider = host.querySelector('.ant-layout-sider'); if (sider) watcher.observe(sider);
  sync();
}

function SideNav({frame}) {
  const groups = (frame.groups || []).map((group, i) => ({
    key: group.title || `group-${i}`, title: group.title,
    items: group.items.filter(item => !item.hidden).map(item => ({key: item.pagePath, label: item.text, icon: <Icon svg={item.icon} />, path: item.pagePath})),
  }));
  return <SbSideNav
    brand={{logoSrc: 'assets/brand/raccoon-salesbuddy-horizontal-white-sidebar.svg', markSrc: 'assets/brand/raccoon-white.svg', alt: '商汤销售小浣熊 · Raccoon SalesBuddy', href: '#/pages/index/index'}}
    workspace={{name: frame.workspaceName, scope: frame.workspaceScope, onClick: frame.onWorkspace}}
    groups={groups} activeKey={frame.activeKey} onSelect={(key, item) => frame.onSelect?.(item?.path || key)}
    collapsed={collapsed} onCollapse={value => { collapsed = value; try { localStorage.setItem(COLLAPSE_KEY, value ? '1' : '0'); } catch (_) {} applyWidth(); render(frame); }}
    adminLink={{label: '运营管理后台', href: '/admin'}}
    account={{name: frame.accountName, role: frame.accountRole, team: frame.accountTeam, avatar: frame.accountAvatar, active: frame.accountActive, onClick: frame.onAccount}} />;
}

function TopBar({frame}) {
  const items = [frame.crumbGroup && {label: frame.crumbGroup}, {label: frame.title}].filter(Boolean);
  return <SbTopBar items={items} onBack={frame.showBack ? frame.onBack : undefined}
    status={frame.status} statusText={frame.statusText} date={frame.date}
    onCreate={frame.onCreate} createHidden={!frame.showCreate} createLabel="新建"
    onHelp={frame.onHelp} onRefresh={frame.onRefresh}
    extra={frame.environment ? <span className="ds-frame-environment">{frame.environment}</span> : null} />;
}

let latest = null;
function render(frame) {
  latest = frame;
  const side = document.getElementById('frame-sidenav'), top = document.getElementById('frame-topbar');
  if (!side || !top) return;
  if (!sideRoot) { sideRoot = createRoot(side); topRoot = createRoot(top); applyWidth(); }
  flushSync(() => {
    sideRoot.render(<SbProvider><SideNav frame={frame} /></SbProvider>);
    topRoot.render(<SbProvider><TopBar frame={frame} /></SbProvider>);
  });
  watchWidth(side);
}

export function renderFrame(frame) { render({...(latest || {}), ...frame}); }
