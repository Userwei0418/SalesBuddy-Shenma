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
import Bi, {supportsBi} from './Bi.jsx';
import {supportsHome} from './Home.jsx';
import Workspace from './Workspace.jsx';
import {identity} from './pc/data.js';
import VisitRecords from './VisitRecords.jsx';
import WorkReport from './work-report/ReportWorkspace.jsx';
import Knowledge from './Knowledge.jsx';
import Customers from './Customers.jsx';
import Tasks from './Tasks.jsx';
import TaskCreate, {supportsTaskCreate} from './TaskCreate.jsx';
import OpportunityWorkspace from './OpportunityWorkspace.jsx';
import CustomerDetail, {supportsCustomerDetail} from './CustomerDetail.jsx';
import OpportunityDetail, {supportsOpportunityDetail} from './OpportunityDetail.jsx';
import Profile, {supportsProfile} from './Profile.jsx';
import TaskDetail, {supportsTaskDetail} from './TaskDetail.jsx';
import CustomerHistory from './CustomerHistory.jsx';
import OpportunityHistory from './OpportunityHistory.jsx';
import CustomerCreate, {supportsCustomerCreate} from './CustomerCreate.jsx';
import VisitEntry, {supportsVisitEntry} from './VisitEntry.jsx';
import VisitConfirm, {supportsVisitConfirm} from './VisitConfirm.jsx';
import CustomerAssets, {supportsCustomerAssets} from './CustomerAssets.jsx';
import OpportunityCreate, {supportsOpportunityCreate} from './OpportunityCreate.jsx';
import DemoCreate, {supportsDemoCreate} from './DemoCreate.jsx';
import {FdeRecords, supportsFdeRecords, FdeBi, supportsFdeBi, FdeMemberGrowth, supportsFdeMemberGrowth, FdeWorkbench, supportsFdeWorkbench, FdeOpportunityBoard, supportsFdeOpportunityBoard} from './FdePages.jsx';
import Login, {supportsLogin} from './Login.jsx';
import {AssignConfirm, supportsAssignConfirm, CustomerClaim, supportsCustomerClaim, Risks, supportsRisks, RiskDetail, supportsRiskDetail, VisitDetail, supportsVisitDetail, CustomerEdit, supportsCustomerEdit, MemberGrowth, supportsMemberGrowth, OpportunityBoard, supportsOpportunityBoard} from './SmallPages.jsx';
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
    case 'pages/knowledge/index': return Knowledge;
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
document.title = innerWidth <= 600 ? 'SalesBuddy' : 'Raccoon SalesBuddy';
const notice = document.querySelector('#preview-notice > span');
if (notice && globalThis.SALES_MODE === 'preview') notice.textContent = '';
