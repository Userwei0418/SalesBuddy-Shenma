// Display projections only: keep stored facts, input values and write payloads intact.
const DEMO_WORKSPACE_ID = '00000000-0000-0000-0000-000000000001';
const DEMO_LABELS = ['【演示数据，全部业务情节为虚构】', '【验收演示数据，非真实经营事实】', '【演示】'];

function activeWorkspaceId() {
  const app = typeof getApp === 'function' ? getApp() : null;
  return app && app.globalData && app.globalData.session && app.globalData.session.workspaceId;
}

function displayText(value, workspaceId) {
  if (workspaceId !== DEMO_WORKSPACE_ID || typeof value !== 'string') return value;
  return DEMO_LABELS.reduce((text, label) => text.split(label).join(''), value);
}

function displayFields(value, keys, workspaceId) {
  if (!value || typeof value !== 'object') return value;
  const result = { ...value };
  keys.forEach(key => {
    if (Object.prototype.hasOwnProperty.call(value, key)) result[key] = displayText(value[key], workspaceId);
  });
  return result;
}

// These two GET models are consumed as display cards. Never traverse arbitrary payloads:
// source, snapshots, synthetic flags and audit/change evidence remain unchanged.
function notificationDisplayResponse(response, workspaceId) {
  if (workspaceId !== DEMO_WORKSPACE_ID || !response || !Array.isArray(response.items)) return response;
  return { ...response, items: response.items.map(item => {
    const result = displayFields(item, ['title', 'body'], workspaceId);
    if (item && item.payload) result.payload = displayFields(item.payload, ['customer_name', 'opportunity_name'], workspaceId);
    return result;
  }) };
}

function timelineDisplayResponse(response, workspaceId) {
  if (workspaceId !== DEMO_WORKSPACE_ID || !response || !Array.isArray(response.items)) return response;
  return { ...response, items: response.items.map(item => displayFields(item, ['title', 'detail'], workspaceId)) };
}

module.exports = { DEMO_WORKSPACE_ID, activeWorkspaceId, displayText, notificationDisplayResponse, timelineDisplayResponse };
