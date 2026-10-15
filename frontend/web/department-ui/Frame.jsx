import React, {useEffect, useMemo, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {Avatar, Breadcrumb, Button, Dropdown, Menu, Select, Tooltip} from 'antd';
import {PcProvider} from './pc/PcProvider.jsx';
import {NavIcon} from './pc/icons.jsx';
import './frame.css';
import './pc/frame.css';

// 工作区外框（PC 视觉 2.0）：浅色侧栏 + 子菜单，紧凑顶栏。
// shell.js 只维护一个 frame 对象（导航树、当前位置、账号、状态），改了就调 renderFrame；
// 对话框（工作区、账号、快捷操作、键盘帮助）仍是 index.html 里的原生 dialog，行为不变。
const NAV_WIDTH = {expanded: 224, collapsed: 64};
const COLLAPSE_KEY = 'sales-web:sidenav-collapsed';
const OPEN_KEY = 'sales-web:sidenav-open';
let sideRoot = null, topRoot = null, collapsed = false;
try { collapsed = localStorage.getItem(COLLAPSE_KEY) === '1'; } catch (_) { collapsed = false; }
const readOpen = () => { try { const value = JSON.parse(localStorage.getItem(OPEN_KEY) || 'null'); return Array.isArray(value) ? value : null; } catch (_) { return null; } };
const saveOpen = keys => { try { localStorage.setItem(OPEN_KEY, JSON.stringify(keys)); } catch (_) {} };

function applyWidth() {
  // 宽度变量写在 body 上，压过 desktop.css 里各档媒体查询的值
  document.body.style.setProperty('--web-sidebar', (collapsed ? NAV_WIDTH.collapsed : NAV_WIDTH.expanded) + 'px');
  document.body.classList.toggle('web-sidenav-collapsed', collapsed);
}

function menuItems(groups) {
  return (groups || []).filter(group => !group.hidden).map(group => {
    const children = (group.children || []).filter(item => !item.hidden);
    if (group.children && !children.length) return null;
    if (!group.children) return {key: group.key, icon: <NavIcon name={group.icon} />, label: group.text, title: group.text};
    return {key: group.key, icon: <NavIcon name={group.icon} />, label: group.text, children: children.map(item => ({key: item.key, label: item.text, title: item.text}))};
  }).filter(Boolean);
}
function leafPath(groups, key) {
  for (const group of groups || []) {
    if (group.key === key && group.path) return group.path;
    for (const item of group.children || []) if (item.key === key) return item.path;
  }
  return null;
}
function parentOf(groups, key) {
  return (groups || []).find(group => (group.children || []).some(item => item.key === key))?.key || null;
}

function SideNav({frame}) {
  const items = useMemo(() => menuItems(frame.groups), [frame.groups]);
  const activeParent = parentOf(frame.groups, frame.activeKey);
  // 首次进入默认展开全部分组，便于找到子菜单；之后记住用户自己的展开状态
  const [openKeys, setOpenKeys] = useState(() => readOpen() || (frame.groups || []).filter(group => group.children).map(group => group.key));
  useEffect(() => {
    // 进到某个子页面时，自动展开它所在的分组；其它分组保持用户自己的展开状态
    if (activeParent && !openKeys.includes(activeParent)) { const next = [...openKeys, activeParent]; setOpenKeys(next); saveOpen(next); }
  }, [activeParent]);
  useEffect(() => {
    // 菜单比屏幕高时，把当前菜单项滚到可见处（等子菜单展开动画结束再量）
    const timer = setTimeout(() => {
      const menu = document.querySelector('#frame-sidenav .pc-nav-menu'), item = menu?.querySelector('.ant-menu-item-selected');
      if (!menu || !item) return;
      const box = menu.getBoundingClientRect(), row = item.getBoundingClientRect();
      if (row.top < box.top) menu.scrollTop -= box.top - row.top + 8;
      else if (row.bottom > box.bottom) menu.scrollTop += row.bottom - box.bottom + 8;
    }, 240);
    return () => clearTimeout(timer);
  }, [frame.activeKey, collapsed]);
  const toggleCollapse = () => {
    collapsed = !collapsed;
    try { localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0'); } catch (_) {}
    applyWidth(); render(frame);
  };
  // sb-sidenav-account 沿用原 SbSideNav 的类名：原有测试与外部脚本按它定位账号入口
  const account = <button type="button" className={`pc-nav-account sb-sidenav-account${frame.accountActive ? ' is-active' : ''}`} onClick={frame.onAccount} aria-label={collapsed ? `账号：${frame.accountName}` : undefined}>
    <Avatar size={28} className="pc-nav-avatar">{frame.accountAvatar || '我'}</Avatar>
    {!collapsed && <span className="pc-nav-account-text"><b>{frame.accountName}</b><small>{[frame.accountRole, frame.accountTeam].filter(Boolean).join(' · ')}</small></span>}
  </button>;
  const workspace = <button type="button" className="pc-nav-workspace" onClick={frame.onWorkspace} aria-label={collapsed ? `工作区：${frame.workspaceName}` : '切换工作空间'}>
    <span className="pc-nav-workspace-icon" aria-hidden="true"><NavIcon name="workspace" /></span>
    {!collapsed && <span className="pc-nav-workspace-text"><b>{frame.workspaceName}</b><small>{frame.workspaceScope}</small></span>}
    {!collapsed && <span className="pc-nav-chevron" aria-hidden="true"><NavIcon name="switch" /></span>}
  </button>;
  return <div className={`pc-nav${collapsed ? ' is-collapsed' : ''}`}>
    <a className="pc-nav-brand" href="#/pages/index/index" aria-label="神码 SalesBuddy 工作台">
      {collapsed ? <img src="assets/brand/favicon.svg" alt="" width="32" height="32" /> : <img src="assets/brand/raccoon-salesbuddy-horizontal-navy-sidebar.svg" alt="神码 SalesBuddy" width="1818" height="368" />}
    </a>
    {collapsed ? <Tooltip placement="right" title={frame.workspaceName}>{workspace}</Tooltip> : workspace}
    <nav className="pc-nav-menu" aria-label="业务导航">
      <Menu mode="inline" inlineCollapsed={collapsed} inlineIndent={14} items={items}
        selectedKeys={frame.activeKey ? [frame.activeKey] : []}
        openKeys={collapsed ? undefined : openKeys}
        onOpenChange={keys => { setOpenKeys(keys); saveOpen(keys); }}
        onClick={({key}) => { const path = leafPath(frame.groups, key); if (path) frame.onSelect?.(path, key); }} />
    </nav>
    <div className="pc-nav-bottom">
      {!collapsed && frame.adminLink && <a className="pc-nav-admin" href={frame.adminLink} target="_blank" rel="noopener noreferrer">运营管理后台 <span aria-hidden="true">↗</span></a>}
      {collapsed ? <Tooltip placement="right" title={frame.accountName}>{account}</Tooltip> : account}
      <button type="button" className="pc-nav-toggle" onClick={toggleCollapse} aria-label={collapsed ? '展开导航' : '收起导航'}>
        <NavIcon name={collapsed ? 'expand' : 'collapse'} />{!collapsed && <span>收起导航</span>}
      </button>
    </div>
  </div>;
}

function TopBar({frame}) {
  const crumbs = (frame.crumbs || [frame.crumbGroup, frame.title]).filter(Boolean);
  const items = (frame.crumbItems || crumbs.map(label => ({label}))).map(({label, path}, index) => ({title: index === crumbs.length - 1
    ? <strong>{label}</strong> : path ? <button type="button" className="pc-crumb-link" onClick={() => frame.onSelect?.(path)}>{label}</button> : label}));
  const actions = frame.createActions || [];
  const status = {ready: '已连接', preview: '演示数据', unavailable: '服务不可用', checking: '正在检查服务'}[frame.status] || '';
  return <header className="pc-topbar">
    <div className="pc-topbar-left">
      {frame.showBack && <Tooltip title="返回上一页；直接打开详情时返回所属列表"><Button type="text" size="small" className="pc-topbar-back" aria-label="返回上一页" icon={<NavIcon name="back" />} onClick={frame.onBack}>返回</Button></Tooltip>}
      <Breadcrumb items={items} separator="/" />
    </div>
    <div className="pc-topbar-right">
      {frame.previewRoles?.length > 0 && <label className="pc-topbar-role"><span>演示身份</span>
        <Select size="small" variant="borderless" value={frame.previewRole} popupMatchSelectWidth={false} aria-label="切换演示身份"
          options={frame.previewRoles} onChange={value => frame.onPreviewRole?.(value)} /></label>}
      {frame.environment && <span className="pc-topbar-env">{frame.environment}</span>}
      <span className={`pc-topbar-status is-${frame.status || 'checking'}`} role="status" title={frame.statusText || status}><i aria-hidden="true" />{frame.statusText || status}</span>
      {frame.date && <span className="pc-topbar-date">{frame.date}</span>}
      {frame.showCreate && actions.length > 0 && <Dropdown trigger={['click']} placement="bottomRight"
        menu={{items: actions.map(action => ({key: action.path, label: action.text, icon: <NavIcon name={action.icon} />})), onClick: ({key}) => frame.onCreateAction?.(key)}}>
        <Button type="primary" className="pc-topbar-create" icon={<NavIcon name="add" />}>新建</Button>
      </Dropdown>}
      <Tooltip title="键盘操作"><Button type="text" className="pc-topbar-icon" aria-label="键盘操作" icon={<NavIcon name="help" />} onClick={frame.onHelp} /></Tooltip>
      <Tooltip title="刷新当前页面"><Button type="text" className="pc-topbar-icon" aria-label="刷新当前页面" icon={<NavIcon name="refresh" />} onClick={frame.onRefresh} /></Tooltip>
    </div>
  </header>;
}

let latest = null;
function render(frame) {
  latest = frame;
  const side = document.getElementById('frame-sidenav'), top = document.getElementById('frame-topbar');
  if (!side || !top) return;
  if (!sideRoot) { sideRoot = createRoot(side); topRoot = createRoot(top); applyWidth(); document.body.classList.add('pc-frame'); }
  flushSync(() => {
    sideRoot.render(<PcProvider><SideNav frame={frame} /></PcProvider>);
    topRoot.render(<PcProvider><TopBar frame={frame} /></PcProvider>);
  });
}

export function renderFrame(frame) { render({...(latest || {}), ...frame}); }
