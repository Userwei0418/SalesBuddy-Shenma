import {state, api, dialog, details, esc, statuses, date} from './core.js';
import {customerProfileDetails} from './customer-profile.js';

let generation = 0;
const historyRow = (kind, item) => kind === 'ownership' ? {
  title: ({approved:'认领通过', released:'释放至客户池', legacy_resolved:'历史归属已核对'})[item.event_type] || item.event_type,
  text: `${item.previous_owner || '待认领'} → ${item.owner || '待认领'} · ${item.reason || ''}`,
  meta: `${date(item.occurred_at)} · ${item.operator}`,
} : {
  title: `${item.applicant_name} · ${statuses[item.status] || item.status}`,
  text: item.decision_reason || '等待运营审核',
  meta: `${date(item.requested_at)} · 审核人 ${item.reviewer_name || '—'}`,
};

export async function showCustomerDetail(id, onOpportunities, onEdit) {
  const serial = ++generation, identity = JSON.stringify(state.actor), page = state.page;
  const current = () => serial === generation && identity === JSON.stringify(state.actor) && page === state.page;
  const pendingDialog = document.querySelector('#dialog'), pendingContent = pendingDialog.innerHTML;
  const customer = await api(`/customers/${encodeURIComponent(id)}/overview`);
  if (!current() || document.querySelector('#dialog') !== pendingDialog || pendingDialog.innerHTML !== pendingContent) return;
  const ownership = customer.ownership || {};
  const sections = ['ownership', 'claims'];
  dialog(`客户档案 · ${customer.name}`, customerProfileDetails(customer) + '<h3>客户归属</h3>' + details([
    ['认领状态', statuses[ownership.state]], ['当前认领人', ownership.owner_name || '尚未认领'],
  ]) + sections.map(kind => `<section data-customer-history="${kind}"><h3>${kind === 'ownership' ? '认领与交接记录' : '审批记录'}</h3><div data-history-body>正在加载…</div></section>`).join(''), {
    wide: true,
    footer: '<button type="button" data-detail-opportunities>关联商机</button>' + (onEdit ? '<button type="button" class="primary" data-detail-edit>编辑资料</button>' : ''),
    afterRender(d) {
      if (onEdit) d.querySelector('[data-detail-edit]').onclick = async () => {
        if (!current()) return;
        const button = d.querySelector('[data-detail-edit]');
        button.disabled = true;
        try { await onEdit(id); }
        catch (error) { if (current() && d.open) d.querySelector('.form-error').textContent = error.message; }
        finally { button.disabled = false; }
      };
      d.querySelector('[data-detail-opportunities]').onclick = () => {
        if (!current()) return;
        d.close();
        onOpportunities(id);
      };
      for (const kind of sections) {
        const node = d.querySelector(`[data-customer-history="${kind}"]`);
        const body = node.querySelector('[data-history-body]');
        const live = () => current() && d.open && node.isConnected && d.contains(node);
        let rows = [], offset = 0, hasMore = true, loading = false, total = null, failure = '';
        const paint = () => {
          if (!live()) return;
          const articles = rows.map(item => {
            const row = historyRow(kind, item);
            return `<article><strong>${esc(row.title)}</strong><p>${esc(row.text)}</p><small>${esc(row.meta)}</small></article>`;
          }).join('');
          body.innerHTML = `<div class="history">${articles}</div>${total !== null ? `<p class="help">共 ${total} 条，已加载 ${rows.length} 条</p>` : ''}` +
            (failure ? `<div class="error-box" role="alert">${esc(failure)}</div>` : '') +
            (loading ? '<p class="help" role="status">正在加载…</p>' : hasMore || failure
              ? `<button type="button" data-history-more>${failure ? '重新加载' : '加载更多'}</button>`
              : !rows.length ? '<p class="help">暂无历史记录</p>' : '');
          const button = body.querySelector('[data-history-more]');
          if (button) button.onclick = load;
        };
        const load = async () => {
          if (!live() || loading) return;
          loading = true; failure = ''; paint();
          try {
            const result = await api(`/customers/${encodeURIComponent(id)}/history?kind=${kind}&page_size=20&offset=${offset}`);
            if (!live()) return;
            if (!Array.isArray(result.items) || !Number.isInteger(result.total) || typeof result.has_more !== 'boolean') {
              throw Error('历史记录响应不完整，请重新加载');
            }
            rows = rows.concat(result.items); total = result.total; hasMore = result.has_more;
            offset = result.next_offset;
          } catch (error) {
            if (live()) failure = error.message || '历史记录加载失败';
          } finally {
            loading = false; paint();
          }
        };
        load();
      }
    },
  });
}
