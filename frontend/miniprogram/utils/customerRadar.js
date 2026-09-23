// Draw only measured values. A missing axis breaks the outline rather than
// becoming a zero score or an interpolated point.
function buildCustomerRadar(dimensions) {
  const vertices = dimensions.map((dimension, index) => {
    if (!dimension.hasValue) return null;
    const angle = index * Math.PI / 3 - Math.PI / 2;
    const radius = Math.max(0, Math.min(100, dimension.value)) / 2;
    const x = 50 + Math.cos(angle) * radius;
    const y = 50 + Math.sin(angle) * radius;
    return {index, x, y, style: `left:${x.toFixed(2)}%;top:${y.toFixed(2)}%;`};
  });
  const points = vertices.filter(Boolean);
  const segments = [];
  vertices.forEach((start, index) => {
    const end = vertices[(index + 1) % vertices.length];
    if (!start || !end) return;
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    segments.push({
      index, from: index, to: end.index,
      style: `left:${start.x.toFixed(2)}%;top:${start.y.toFixed(2)}%;width:${Math.hypot(dx, dy).toFixed(2)}%;transform:rotate(${(Math.atan2(dy, dx) * 180 / Math.PI).toFixed(2)}deg);`,
    });
  });
  const complete = points.length === 6;
  const polygon = complete ? points.map(point => `${point.x.toFixed(2)}% ${point.y.toFixed(2)}%`).join(',') : '';
  return {
    points, segments,
    fillStyle: complete ? `-webkit-clip-path:polygon(${polygon});clip-path:polygon(${polygon});` : '',
  };
}

module.exports = {buildCustomerRadar};
