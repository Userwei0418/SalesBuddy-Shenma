// Active customers share scope and operating filters; unassessed customers stay out of the plot.
function hasMapScores(customer) {
  return ['potential', 'relationship'].every(key =>
    typeof customer[key] === 'number' && Number.isFinite(customer[key]) && customer[key] >= 0 && customer[key] <= 100);
}
function scopedCustomers(customers, teamId, ownerId, directory = []) {
  const memberTeams = new Map(directory.map(member => [member.id, member.team_ids || [member.team_id]]));
  return customers.filter(customer => {
    const members = Array.isArray(customer.sales_members) ? customer.sales_members : [];
    const ownerIds = members.length ? members.map(member => member.id) : [customer.owner_user_ref_id];
    const matchesTeam = !teamId || teamId === 'all' || customer.owner_team_id === teamId ||
      ownerIds.some(id => (memberTeams.get(id) || []).includes(teamId));
    return matchesTeam && (!ownerId || ownerId === 'all' || ownerIds.includes(ownerId));
  });
}
function filterCustomers(customers, options = {}) {
  const keyword = String(options.keyword || '').trim().toLowerCase();
  return customers.filter(c => {
    if (keyword && !`${c.name} ${c.owner}`.toLowerCase().includes(keyword)) return false;
    if (options.quadrant && options.quadrant !== 'all' && c.quadrant !== options.quadrant) return false;
    if (options.plan && options.plan !== 'all' && c.agentPlanSegment !== options.plan) return false;
    if (options.customerId && options.customerId !== 'all' && c.id !== options.customerId) return false;
    if (Array.isArray(options.levels) && options.levels.length && !options.levels.includes(c.level)) return false;
    if (options.amount && options.amount.value !== 'all' && (c.mapAmount == null || !Number.isFinite(c.mapAmount) || c.mapAmount < options.amount.min || c.mapAmount > options.amount.max)) return false;
    return true;
  });
}
// Each customer appears once; its amount already aggregates the complete RLS-visible
// open opportunity set. A missing amount is unknown, never a confirmed zero.
function mapAcv(customers) {
  if (!Array.isArray(customers)) return null;
  const seen = new Set();
  let total = 0;
  for (const item of customers) {
    if (!item.id) return null;
    if (seen.has(String(item.id))) continue;
    seen.add(String(item.id));
    const value = item.acv_amount;
    if (!['number','string'].includes(typeof value) || String(value).trim() === '' || !Number.isFinite(Number(value)) || Number(value) < 0) return null;
    total += Number(value);
  }
  return total;
}
function amountWan(value, maximumFractionDigits = 6) {
  if (value === null || value === undefined) return '—';
  return (Number(value) / 10000).toLocaleString('en-US', {maximumFractionDigits});
}
function requestId() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const n=Math.floor(Math.random()*16);return(c==='x'?n:(n&3|8)).toString(16); });
}
module.exports = {scopedCustomers, filterCustomers, mapAcv, amountWan, requestId, hasMapScores};

// Use rendered hit rectangles, so zoom, screen width and collision offsets are all included.
function overlappingPlotIds(rects, selectedId) {
  const selected = rects.find(r => String((r.dataset || {}).id) === String(selectedId));
  if (!selected) return [];
  return rects.filter(r => r.left < selected.right && r.right > selected.left &&
    r.top < selected.bottom && r.bottom > selected.top).map(r => String(r.dataset.id));
}
module.exports.overlappingPlotIds = overlappingPlotIds;
