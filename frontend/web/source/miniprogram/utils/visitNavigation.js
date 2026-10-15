// A list may be reached through a linked opportunity whose customer is not the
// visit's primary subject. Only pass a customer explicitly returned on the visit.
function visitDetailUrl(visit = {}) {
  if (!visit.id) return '';
  const customerId = Object.prototype.hasOwnProperty.call(visit, 'customer_id') ? visit.customer_id : visit.customerId;
  const query = customerId ? `customer_id=${encodeURIComponent(customerId)}&` : '';
  return `/pages/visit-detail/index?${query}visit_id=${encodeURIComponent(visit.id)}`;
}

module.exports = {visitDetailUrl};
