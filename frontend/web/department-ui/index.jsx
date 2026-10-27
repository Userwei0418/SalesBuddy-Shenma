import {InputSyncContext} from './ImeInput.jsx';
import './upgrade-110.css';
import React from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {App} from 'antd';
import {PcProvider} from './pc/PcProvider.jsx';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import '@shandiant/tokens/css';
import '@shandiant/ui-react/style.css';
import './shared.css';
import {resolveComponent} from './advice-bridge.js';
import {PresentationSwitch} from './WorkflowCompletion.jsx';
import {renderFrame} from './Frame.jsx';
import {identity} from './pc/data.js';
let Bi, supportsBi, supportsHome, Workspace, VisitRecords, WorkReport, Knowledge, Customers, Tasks, TaskCreate, supportsTaskCreate, OpportunityWorkspace, CustomerDetail, supportsCustomerDetail, OpportunityDetail, supportsOpportunityDetail, Profile, supportsProfile, TaskDetail, supportsTaskDetail, CustomerHistory, OpportunityHistory, CustomerCreate, supportsCustomerCreate, VisitEntry, supportsVisitEntry, VisitConfirm, supportsVisitConfirm, CustomerAssets, supportsCustomerAssets, OpportunityCreate, supportsOpportunityCreate, DemoCreate, supportsDemoCreate, FdeRecords, supportsFdeRecords, FdeBi, supportsFdeBi, FdeMemberGrowth, supportsFdeMemberGrowth, FdeWorkbench, supportsFdeWorkbench, FdeOpportunityBoard, supportsFdeOpportunityBoard, Login, supportsLogin, AssignConfirm, supportsAssignConfirm, CustomerClaim, supportsCustomerClaim, Risks, supportsRisks, RiskDetail, supportsRiskDetail, VisitDetail, supportsVisitDetail, CustomerEdit, supportsCustomerEdit, MemberGrowth, supportsMemberGrowth, OpportunityBoard, supportsOpportunityBoard;
const pageModules = {
  './Bi.jsx': () => import('./Bi.jsx').then(module => { Bi=module.default; supportsBi=module.supportsBi; }),
  './Home.jsx': () => import('./Home.jsx').then(module => { supportsHome=module.supportsHome; }),
  './Workspace.jsx': () => import('./Workspace.jsx').then(module => { Workspace=module.default; }),
  './VisitRecords.jsx': () => import('./VisitRecords.jsx').then(module => { VisitRecords=module.default; }),
  './work-report/ReportWorkspace.jsx': () => import('./work-report/ReportWorkspace.jsx').then(module => { WorkReport=module.default; }),
  './Knowledge.jsx': () => import('./Knowledge.jsx').then(module => { Knowledge=module.default; }),
  './Customers.jsx': () => import('./Customers.jsx').then(module => { Customers=module.default; }),
  './Tasks.jsx': () => import('./Tasks.jsx').then(module => { Tasks=module.default; }),
  './TaskCreate.jsx': () => import('./TaskCreate.jsx').then(module => { TaskCreate=module.default; supportsTaskCreate=module.supportsTaskCreate; }),
  './OpportunityWorkspace.jsx': () => import('./OpportunityWorkspace.jsx').then(module => { OpportunityWorkspace=module.default; }),
  './CustomerDetail.jsx': () => import('./CustomerDetail.jsx').then(module => { CustomerDetail=module.default; supportsCustomerDetail=module.supportsCustomerDetail; }),
  './OpportunityDetail.jsx': () => import('./OpportunityDetail.jsx').then(module => { OpportunityDetail=module.default; supportsOpportunityDetail=module.supportsOpportunityDetail; }),
  './Profile.jsx': () => import('./Profile.jsx').then(module => { Profile=module.default; supportsProfile=module.supportsProfile; }),
  './TaskDetail.jsx': () => import('./TaskDetail.jsx').then(module => { TaskDetail=module.default; supportsTaskDetail=module.supportsTaskDetail; }),
  './CustomerHistory.jsx': () => import('./CustomerHistory.jsx').then(module => { CustomerHistory=module.default; }),
  './OpportunityHistory.jsx': () => import('./OpportunityHistory.jsx').then(module => { OpportunityHistory=module.default; }),
  './CustomerCreate.jsx': () => import('./CustomerCreate.jsx').then(module => { CustomerCreate=module.default; supportsCustomerCreate=module.supportsCustomerCreate; }),
  './VisitEntry.jsx': () => import('./VisitEntry.jsx').then(module => { VisitEntry=module.default; supportsVisitEntry=module.supportsVisitEntry; }),
  './VisitConfirm.jsx': () => import('./VisitConfirm.jsx').then(module => { VisitConfirm=module.default; supportsVisitConfirm=module.supportsVisitConfirm; }),
  './CustomerAssets.jsx': () => import('./CustomerAssets.jsx').then(module => { CustomerAssets=module.default; supportsCustomerAssets=module.supportsCustomerAssets; }),
  './OpportunityCreate.jsx': () => import('./OpportunityCreate.jsx').then(module => { OpportunityCreate=module.default; supportsOpportunityCreate=module.supportsOpportunityCreate; }),
  './DemoCreate.jsx': () => import('./DemoCreate.jsx').then(module => { DemoCreate=module.default; supportsDemoCreate=module.supportsDemoCreate; }),
  './FdePages.jsx': () => import('./FdePages.jsx').then(module => { FdeRecords=module.FdeRecords; supportsFdeRecords=module.supportsFdeRecords; FdeBi=module.FdeBi; supportsFdeBi=module.supportsFdeBi; FdeMemberGrowth=module.FdeMemberGrowth; supportsFdeMemberGrowth=module.supportsFdeMemberGrowth; FdeWorkbench=module.FdeWorkbench; supportsFdeWorkbench=module.supportsFdeWorkbench; FdeOpportunityBoard=module.FdeOpportunityBoard; supportsFdeOpportunityBoard=module.supportsFdeOpportunityBoard; }),
  './Login.jsx': () => import('./Login.jsx').then(module => { Login=module.default; supportsLogin=module.supportsLogin; }),
  './SmallPages.jsx': () => import('./SmallPages.jsx').then(module => { AssignConfirm=module.AssignConfirm; supportsAssignConfirm=module.supportsAssignConfirm; CustomerClaim=module.CustomerClaim; supportsCustomerClaim=module.supportsCustomerClaim; Risks=module.Risks; supportsRisks=module.supportsRisks; RiskDetail=module.RiskDetail; supportsRiskDetail=module.supportsRiskDetail; VisitDetail=module.VisitDetail; supportsVisitDetail=module.supportsVisitDetail; CustomerEdit=module.CustomerEdit; supportsCustomerEdit=module.supportsCustomerEdit; MemberGrowth=module.MemberGrowth; supportsMemberGrowth=module.supportsMemberGrowth; OpportunityBoard=module.OpportunityBoard; supportsOpportunityBoard=module.supportsOpportunityBoard; }),
};
const routeModules = {"pages/index/index": ["./Home.jsx", "./Workspace.jsx"], "pages/visit-records/index": ["./VisitRecords.jsx"], "pages/work-report/index": ["./work-report/ReportWorkspace.jsx"], "pages/weekly-report/index": ["./work-report/ReportWorkspace.jsx"], "pages/customer-history/index": ["./CustomerHistory.jsx"], "pages/opportunity-history/index": ["./OpportunityHistory.jsx"], "pages/knowledge/index": ["./Knowledge.jsx"], "pages/knowledge-webview/index": ["./Knowledge.jsx"], "pages/customers/index": ["./Customers.jsx"], "pages/tasks/index": ["./Tasks.jsx"], "pages/workbench/index": ["./OpportunityWorkspace.jsx"], "pages/management-task-create/index": ["./TaskCreate.jsx"], "pages/customer-detail/index": ["./CustomerDetail.jsx"], "pages/customer-assets/index": ["./OpportunityDetail.jsx", "./CustomerAssets.jsx"], "pages/profile/index": ["./Profile.jsx"], "pages/task-detail/index": ["./TaskDetail.jsx"], "pages/customer-create/index": ["./CustomerCreate.jsx"], "pages/visit-entry/index": ["./VisitEntry.jsx"], "pages/visit-confirm/index": ["./VisitConfirm.jsx"], "pages/customer-assign-confirm/index": ["./SmallPages.jsx"], "pages/customer-claim/index": ["./SmallPages.jsx"], "pages/risks/index": ["./SmallPages.jsx"], "pages/risk-detail/index": ["./SmallPages.jsx"], "pages/visit-detail/index": ["./SmallPages.jsx"], "pages/customer-edit/index": ["./SmallPages.jsx"], "pages/member-growth/index": ["./FdePages.jsx", "./SmallPages.jsx"], "pages/opportunities/index": ["./OpportunityWorkspace.jsx"], "pages/opportunity-create/index": ["./OpportunityCreate.jsx"], "pages/demo-create/index": ["./DemoCreate.jsx"], "pages/fde-records/index": ["./FdePages.jsx"], "pages/bi/index": ["./Bi.jsx", "./FdePages.jsx"], "pages/login/index": ["./Login.jsx"]};
const moduleLoads = new Map();
const readyRoutes = new Set();
const routeErrors = new Map();
function ensureRoute(page, host) {
  const route = page.route, paths = routeModules[route];
  if (!paths || readyRoutes.has(route)) return true;
  if (owner !== page) unmount();
  host.classList.add('department-host');
  host.replaceChildren();
  const panel = document.createElement('div'); panel.className='department-route-loading'; panel.setAttribute('role','status');
  panel.style.cssText='padding:32px;color:#64748b';
  panel.textContent=routeErrors.has(route)?'页面未能加载，请重试。':'正在打开页面…';
  host.append(panel);
  if (routeErrors.has(route)) {
    const button=document.createElement('button'); button.textContent='重新加载';
    // Browsers cache rejected module fetches in the current document. A full
    // reload retries the same route with a fresh module map and keeps saved drafts.
    button.onclick=()=>location.reload(); panel.append(button); return false;
  }
  Promise.all(paths.map(path => {
    if (!moduleLoads.has(path)) moduleLoads.set(path,pageModules[path]());
    return moduleLoads.get(path);
  })).then(()=>{readyRoutes.add(route); if(page===globalThis.SalesRuntime.current&&!page._destroyed)page.setData({});})
  .catch(error=>{routeErrors.set(route,error);if(page===globalThis.SalesRuntime.current&&!page._destroyed)page.setData({});});
  return false;
}
// PC 视觉 2.0：设计变量与全局密度放在最后，覆盖部门组件库与各页面样式里的旧值
import './pc/tokens.css';
import './pc/pc-theme.css';
import './pc/workspace-layout.css';
import './pc/overview-filters.css';

dayjs.locale('zh-cn');

let mounted = null, owner = null;
function unmount() {
  if (mounted) flushSync(() => mounted.unmount());
  mounted = null; owner = null;
  document.getElementById('page-root')?.classList.remove('department-host', 'department-pending');
}
// 子组件接线：原页面的子组件（opportunity-form、fde-dashboard 这些）只在原模板走一遍时才会被创建。
// 适配器用 Component.subcomponents(page, data) 声明它要的子组件与属性；第一次发现缺少时让原模板渲染一帧把组件建出来，
// 之后每次渲染把所有组件标成 _seen 免于被回收，并把页面数据里对应的属性同步给组件（等价于模板上的 attr="{{data}}"）。
const equal = (a, b) => a === b || (a && b && typeof a === 'object' && typeof b === 'object' && JSON.stringify(a) === JSON.stringify(b));
function walk(owner, fn) { for (const instance of owner._components?.values() || []) { fn(instance); walk(instance, fn); } }
function resolve(page, entry) {
  return resolveComponent(page, entry);
}
function syncProps(instance, props = {}) {
  const patch = {};
  for (const key of Object.keys(props)) if (instance.properties && key in instance.properties && !equal(instance.properties[key], props[key])) patch[key] = props[key];
  if (Object.keys(patch).length) instance.setData(patch);
}
function prepareSubcomponents(page, data, host, Component) {
  const entries = typeof Component.subcomponents === 'function' ? Component.subcomponents(page, data) || [] : [];
  const missing = entries.filter(entry => entry.required !== false && !resolve(page, entry)).map(entry => [entry.parent || '', entry.selector, entry.analysisId || '', entry.suggestionId || ''].join('>'));
  if (missing.length) {
    const key = missing.join(',');
    if (page._departmentRetry !== key) {
      page._departmentRetry = key;
      unmount(); host.classList.add('department-pending');
      queueMicrotask(() => { if (page === globalThis.SalesRuntime.current && !page._destroyed) page.setData({}); });
      return false;
    }
  } else page._departmentRetry = null;
  host.classList.remove('department-pending');
  walk(page, instance => { instance._seen = true; });
  for (const entry of entries) { const instance = resolve(page, entry); if (instance && entry.props) syncProps(instance, entry.props); }
  return true;
}
function view(page, data) {
  if (data.accessBlocked) return null;
  switch (page.route) {
    // 总览改为 PC 工作台；录音、ChatBI 等原页面模式仍交回原模板
    case 'pages/index/index': return supportsHome(page, data) ? Workspace : null;
    case 'pages/visit-records/index': return VisitRecords;
    case 'pages/work-report/index': return WorkReport;
    // 旧周报链接由同一工作报告模块承接，默认选择自然周。
    case 'pages/weekly-report/index': return WorkReport;
    case 'pages/customer-history/index': return CustomerHistory;
    case 'pages/opportunity-history/index': return OpportunityHistory;
    case 'pages/knowledge/index':
    case 'pages/knowledge-webview/index': return Knowledge;
    case 'pages/customers/index': return data.selectedCustomer || data.selectedBattleCustomer ? null : Customers;
    case 'pages/tasks/index': return Tasks;
    case 'pages/workbench/index': return OpportunityWorkspace;
    case 'pages/management-task-create/index': return supportsTaskCreate(page, data) ? TaskCreate : null;
    case 'pages/customer-detail/index': return supportsCustomerDetail(page, data) ? CustomerDetail : null;
    case 'pages/customer-assets/index': return supportsOpportunityDetail(page, data) ? OpportunityDetail : supportsCustomerAssets(page, data) ? CustomerAssets : null;
    case 'pages/profile/index': return supportsProfile(page, data) ? Profile : null;
    case 'pages/task-detail/index': return supportsTaskDetail(page, data) ? TaskDetail : null;
    case 'pages/customer-create/index': return supportsCustomerCreate(page, data) ? CustomerCreate : null;
    case 'pages/visit-entry/index': return supportsVisitEntry(page, data) ? VisitEntry : null;
    case 'pages/visit-confirm/index': return supportsVisitConfirm(page, data) ? VisitConfirm : null;
    case 'pages/customer-assign-confirm/index': return supportsAssignConfirm(page, data) ? AssignConfirm : null;
    case 'pages/customer-claim/index': return supportsCustomerClaim(page, data) ? CustomerClaim : null;
    case 'pages/risks/index': return supportsRisks(page, data) ? Risks : null;
    case 'pages/risk-detail/index': return supportsRiskDetail(page, data) ? RiskDetail : null;
    case 'pages/visit-detail/index': return supportsVisitDetail(page, data) ? VisitDetail : null;
    case 'pages/customer-edit/index': return supportsCustomerEdit(page, data) ? CustomerEdit : null;
    case 'pages/member-growth/index': return supportsFdeMemberGrowth(page, data) ? FdeMemberGrowth : supportsMemberGrowth(page, data) ? MemberGrowth : null;
    case 'pages/opportunities/index': return OpportunityWorkspace;
    case 'pages/opportunity-create/index': return supportsOpportunityCreate(page, data) ? OpportunityCreate : null;
    case 'pages/demo-create/index': return supportsDemoCreate(page, data) ? DemoCreate : null;
    case 'pages/fde-records/index': return supportsFdeRecords(page, data) ? FdeRecords : null;
    case 'pages/bi/index': return supportsFdeBi(page, data) ? FdeBi : supportsBi(page, data) ? Bi : null;
    case 'pages/login/index': return supportsLogin(page, data) ? Login : null;
    default: return null;
  }
}
globalThis.SalesDepartmentUI = Object.freeze({
  renderFrame,
  render(page, data, host) {
    if (!ensureRoute(page, host)) return true;
    const Component = view(page, data);
    if (!Component) {unmount(); return false;}
    if (!prepareSubcomponents(page, data, host, Component)) return false;
    if (!mounted || owner !== page) {
      unmount(); host.replaceChildren(); mounted = createRoot(host); owner = page;
    }
    host.classList.add('department-host');
    const fail = error => {
      if (page === globalThis.SalesRuntime.current && !page._destroyed) globalThis.SalesRuntime.wx.showToast({title: error.message || '没能完成。点重试'});
    };
    // invokeOn：对子组件实例调方法，事件形状与原模板 bind 一致（currentTarget.dataset + detail）。
    const invokeOn = (instance, name, {dataset = {}, detail = {}} = {}) => {
      if (!instance || instance._destroyed || page !== globalThis.SalesRuntime.current || page._destroyed) return;
      if (typeof instance[name] !== 'function') throw new Error(`Missing original action: ${name}`);
      try {
        return Promise.resolve(instance[name]({currentTarget: {dataset}, target: {dataset}, detail})).catch(fail);
      } catch (error) { fail(error); }
    };
    const invoke = (name, payload) => invokeOn(page, name, payload);
    const select = (selector, parent) => resolve(page, {selector, parent});
    flushSync(() => mounted.render(<PcProvider><App><main className="department-ui" data-department-page={page.route}>{['pages/bi/index','pages/profile/index'].includes(page.route) && <PresentationSwitch data={data} invoke={invoke}/>}<InputSyncContext.Provider value={{}}><Component key={`${page._id}:${identity()}`} page={page} data={data} invoke={invoke} invokeOn={invokeOn} select={select}/></InputSyncContext.Provider></main></App></PcProvider>));
    return true;
  },
  unmount,
});
document.body.classList.add('department-design');
document.title = innerWidth <= 600 ? 'SalesBuddy' : '神码 SalesBuddy';
