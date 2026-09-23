const {numeric} = require('./fdePresentation');

// Same six-axis canvas geometry as the sales profile. Missing facts are not zero.
function drawRadar(ctx, width, height, dimensions) {
  if (!ctx || dimensions.length !== 6 || !width || !height) return;
  const cx = width / 2, cy = height / 2, radius = Math.min(width * .26, height * .28);
  const point = (index, scale) => {
    const angle = -Math.PI / 2 + index * Math.PI / 3;
    return [cx + Math.cos(angle) * radius * scale, cy + Math.sin(angle) * radius * scale];
  };
  ctx.clearRect(0, 0, width, height);
  ctx.setLineWidth(1);
  for (let level = 1; level <= 5; level++) {
    ctx.beginPath();
    for (let i = 0; i < 6; i++) { const p = point(i, level / 5); if (!i) ctx.moveTo(...p); else ctx.lineTo(...p); }
    ctx.closePath(); ctx.setStrokeStyle('rgba(61,100,146,.18)'); ctx.stroke();
  }
  dimensions.forEach((item, i) => { const p = point(i, 1); ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(...p); ctx.setStrokeStyle('rgba(61,100,146,.13)'); ctx.stroke(); });
  const scores = dimensions.map(item => numeric(item.score));
  if (scores.every(value => value !== null)) {
    ctx.beginPath();
    scores.forEach((value, i) => { const p = point(i, Math.max(0, Math.min(100, value)) / 100); if (!i) ctx.moveTo(...p); else ctx.lineTo(...p); });
    ctx.closePath(); ctx.setFillStyle('rgba(22,119,255,.18)'); ctx.fill(); ctx.setLineWidth(2); ctx.setStrokeStyle('#1677ff'); ctx.stroke();
  } else {
    // Only connect adjacent measured axes. A missing dimension is a gap.
    scores.forEach((value, i) => {
      const next = (i + 1) % 6;
      if (value === null || scores[next] === null) return;
      ctx.beginPath(); ctx.moveTo(...point(i, Math.max(0, Math.min(100, value)) / 100)); ctx.lineTo(...point(next, Math.max(0, Math.min(100, scores[next])) / 100)); ctx.setLineWidth(2); ctx.setStrokeStyle('#1677ff'); ctx.stroke();
    });
  }
  dimensions.forEach((item, i) => {
    const score = scores[i];
    if (score !== null) { const p = point(i, Math.max(0, Math.min(100, score)) / 100); ctx.beginPath(); ctx.arc(p[0], p[1], 3, 0, Math.PI * 2); ctx.setFillStyle('#1677ff'); ctx.fill(); }
    // Side labels grow away from the plot; bottom text needs its own baseline
    // below the lower vertex so a full-valued axis cannot cross the label.
    const p = point(i, 1.32), textX = Math.max(52, Math.min(width - 52, p[0]));
    const nameY = i === 3 ? Math.min(height - 24, p[1] + 10) : i === 0 ? p[1] - 6 : p[1] - 2;
    ctx.setTextAlign(p[0] < cx - 5 ? 'right' : p[0] > cx + 5 ? 'left' : 'center'); ctx.setFillStyle('#53657a'); ctx.setFontSize(11);
    ctx.fillText(item.shortName || item.name, textX, nameY);
    ctx.setFillStyle(score === null ? '#9aa6b5' : '#2479c5'); ctx.setFontSize(10);
    ctx.fillText(score === null ? '未评估' : String(Math.round(score * 10) / 10), textX, nameY + 15);
  });
  ctx.draw();
}
module.exports = {drawRadar};
