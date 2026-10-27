const QUADRANT_THRESHOLDS = {
  potential: 70,
  relationship: 70,
};

const QUADRANTS = [
  {
    key: "main_attack",
    name: "主攻区",
    definition: "潜力大 · 关系不熟",
    action: "重点投入，建立关键关系",
    className: "q1",
  },
  {
    key: "customer_asset",
    name: "客户资产",
    definition: "潜力大 · 关系深",
    action: "多线程深耕，增购与共创",
    className: "q2",
  },
  {
    key: "order_driven",
    name: "见单打单",
    definition: "潜力小 · 关系不熟",
    action: "控制投入，有明确订单再跟进",
    className: "q3",
  },
  {
    key: "customer_resource",
    name: "客户资源",
    definition: "潜力小 · 关系深",
    action: "轻量维护，观察成长机会",
    className: "q4",
  },
];

function classifyQuadrant(potential, relationship) {
  const hasHighPotential = Number(potential) >= QUADRANT_THRESHOLDS.potential;
  const hasDeepRelationship = Number(relationship) >= QUADRANT_THRESHOLDS.relationship;
  if (hasHighPotential && !hasDeepRelationship) return "主攻区";
  if (hasHighPotential && hasDeepRelationship) return "客户资产";
  if (!hasHighPotential && !hasDeepRelationship) return "见单打单";
  return "客户资源";
}

function decorateCustomer(customer) {
  return {
    ...customer,
    quadrant: classifyQuadrant(customer.potential, customer.relationship),
  };
}

function savedThresholds(customer) {
  const d = customer.quadrant_policy && customer.quadrant_policy.definition;
  if (!d || !(d.potential_threshold > 0 && d.potential_threshold < 100) ||
      !(d.relationship_threshold > 0 && d.relationship_threshold < 100)) return QUADRANT_THRESHOLDS;
  return {potential:d.potential_threshold, relationship:d.relationship_threshold, inclusive:d.inclusive !== false};
}

function plotAxis(customer, axis, offset = 0, zoom = false) {
  const rule=savedThresholds(customer), threshold=rule[axis];
  const score=Math.max(0,Math.min(100,Number(customer[axis] || 0)));
  const high=rule.inclusive !== false ? score>=threshold : score>threshold;
  if (zoom) {
    const fraction = high ? (score-threshold)/(100-threshold) : score/threshold;
    return Math.max(4, Math.min(96, 6 + fraction*88 + offset));
  }
  const point=high ? 52+(score-threshold)/(100-threshold)*42 : 6+score/threshold*42;
  // Collision offsets must stay on the same side of this saved policy boundary.
  return Math.max(high ? 52 : 4,Math.min(high ? 96 : 48,point+offset));
}

module.exports = { QUADRANT_THRESHOLDS, QUADRANTS, classifyQuadrant, decorateCustomer, savedThresholds, plotAxis };
