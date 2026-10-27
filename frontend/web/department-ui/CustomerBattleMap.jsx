import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
// 服务端渲染没有布局阶段，用 useEffect 代替，避免告警
const useIsoLayoutEffect = typeof window !== 'undefined' ? useLayoutEffect : useEffect;
import { Button, Skeleton } from 'antd';
/** Web viewport renderer based on @shandiant/ui-react 0.7.0 SbBattleMap.
 * The package fixes a square viewBox. This adapter measures the plot frame in
 * CSS pixels so both axes fill a rectangular workspace without stretching text
 * or circles. Keep package interactions/styles and shared quadrant semantics.
 */
import {quadrantOf} from '@shandiant/ui-react';

const QUADRANTS = [
  { key: 'asset', name: '客户资产', short: '资产', desc: '潜力大 · 关系深', high: [true, true], fill: 'var(--ui-quadrant-asset, color-mix(in srgb, var(--ui-primary) 5%, var(--ui-surface)))' },
  { key: 'attack', name: '主攻区', short: '主攻', desc: '潜力大 · 关系浅', high: [true, false], fill: 'var(--ui-quadrant-attack, color-mix(in srgb, var(--ui-primary) 5%, var(--ui-surface)))' },
  { key: 'resource', name: '客户资源', short: '资源', desc: '潜力小 · 关系深', high: [false, true], fill: 'var(--ui-quadrant-resource, var(--ui-surface))' },
  { key: 'spot', name: '见单打单', short: '见单', desc: '潜力小 · 关系浅', high: [false, false], fill: 'var(--ui-quadrant-spot, var(--ui-surface))' },
];
const QUADRANT_ORDER = ['attack', 'asset', 'spot', 'resource']; // 手机底部一行的顺序：左上、右上、左下、右下
// 颜色出现就有意义（14 章）：向好用主色，只有需关注黄、转差红两种提示色；待评估空心灰边
const TONE_FILL = { good: 'var(--ui-primary)', watch: 'var(--ui-warning)', bad: 'var(--ui-danger)', pending: 'var(--ui-surface)' };
const TONE_STROKE = { pending: 'var(--ui-neutral)' };
const TONE_TEXT = { good: '向好', watch: '需关注', bad: '转差', pending: '待评估' };
const BAND_DIAMETER = { small: 12, medium: 16, large: 22 };
const MIN = 1, MAX = 10;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/**
 * 聚合：按屏幕像素贪心合并。距离小于两点直径中较大者的点并进同一簇（簇心取平均）；点数超过 clusterAfter 时合并半径翻倍，聚得更积极。
 * 返回 [{ key, x, y, points, single }]
 */
function clusterPoints(placed, aggressive) {
  const clusters = [];
  for (const p of placed) {
    let hit = null;
    for (const c of clusters) {
      const limit = Math.max(c.maxDiameter, p.diameter) * (aggressive ? 2 : 1);
      const dx = c.x - p.x, dy = c.y - p.y;
      if (Math.sqrt(dx * dx + dy * dy) < limit) { hit = c; break; }
    }
    if (hit) {
      hit.points.push(p);
      hit.x = hit.points.reduce((s, q) => s + q.x, 0) / hit.points.length;
      hit.y = hit.points.reduce((s, q) => s + q.y, 0) / hit.points.length;
      hit.maxDiameter = Math.max(hit.maxDiameter, p.diameter);
    } else clusters.push({ key: p.id, x: p.x, y: p.y, points: [p], maxDiameter: p.diameter });
  }
  return clusters.map((c) => ({ ...c, single: c.points.length === 1 }));
}

export default function CustomerBattleMap({
  points = [],
  thresholds: thresholdsProp,
  unrated = 0,
  clusterAfter = 30,
  zoomQuadrant,
  defaultZoomQuadrant = null,
  onZoomChange,
  onPointClick,
  onClusterClick,
  onUnratedClick,
  selectedId = null,
  loading = false,
  emptyText = '还没有客户进入作战地图。先在客户列表登记关系和潜力。',
  emptyAction,
  className,
  style,
}) {
  const thresholds = { potential: 5.5, relationship: 5.5, ...(thresholdsProp || {}) };
  const frameRef = useRef(null);
  const [{width, height}, setSize] = useState({width: 600, height: 400});
  const [hoverKey, setHoverKey] = useState(null);
  const [innerZoom, setInnerZoom] = useState(defaultZoomQuadrant);
  const zoom = zoomQuadrant === undefined ? innerZoom : zoomQuadrant;
  const setZoom = (next) => { if (zoomQuadrant === undefined) setInnerZoom(next); onZoomChange?.(next); };

  useIsoLayoutEffect(() => {
    const el = frameRef.current;
    if (!el) return undefined;
    const read = () => {
      const width = el.clientWidth, height = el.clientHeight;
      if (width > 0 && height > 0) setSize(previous => previous.width === width && previous.height === height ? previous : {width, height});
    };
    read();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const k = 1; // SVG units are CSS pixels; typography and dots never stretch.
  const mobile = width < 380;
  const inset = { left: 40 * k, bottom: 36 * k, top: 8 * k, right: 8 * k };
  const plot = { x0: inset.left, y0: inset.top, x1: width - inset.right, y1: height - inset.bottom };
  const zoomed = QUADRANTS.find((q) => q.key === zoom) || null;
  // 当前画的数值范围：全图 1～10；放大时只画那个格子的一半
  const domain = zoomed
    ? { px: zoomed.high[0] ? [thresholds.potential, MAX] : [MIN, thresholds.potential], py: zoomed.high[1] ? [thresholds.relationship, MAX] : [MIN, thresholds.relationship] }
    : { px: [MIN, MAX], py: [MIN, MAX] };
  const pad = 14 * k; // 点不贴边
  const sx = (v) => plot.x0 + pad + (clamp(v, domain.px[0], domain.px[1]) - domain.px[0]) / (domain.px[1] - domain.px[0]) * (plot.x1 - plot.x0 - pad * 2);
  const sy = (v) => plot.y1 - pad - (clamp(v, domain.py[0], domain.py[1]) - domain.py[0]) / (domain.py[1] - domain.py[0]) * (plot.y1 - plot.y0 - pad * 2);
  const divX = sx(thresholds.potential), divY = sy(thresholds.relationship);

  const valid = useMemo(() => points.filter((p) => Number.isFinite(p.potential) && Number.isFinite(p.relationship)), [points]);
  const counts = useMemo(() => {
    const c = { asset: 0, attack: 0, resource: 0, spot: 0 };
    for (const p of valid) c[quadrantOf(p, thresholds)] += 1;
    return c;
  }, [valid, thresholds.potential, thresholds.relationship]);
  const shown = useMemo(() => (zoomed ? valid.filter((p) => quadrantOf(p, thresholds) === zoomed.key) : valid), [valid, zoomed, thresholds.potential, thresholds.relationship]);
  const clusters = useMemo(() => {
    const placed = shown.map((p) => ({ ...p, x: sx(p.potential), y: sy(p.relationship), diameter: (BAND_DIAMETER[p.amountBand] || BAND_DIAMETER.medium) * k }));
    return clusterPoints(placed, shown.length > clusterAfter);
  }, [shown, clusterAfter, width, height, zoom, thresholds.potential, thresholds.relationship]);

  const summary = `作战地图：${QUADRANTS.map((q) => `${q.name} ${counts[q.key]} 家`).join('，')}${unrated > 0 ? `，待评估 ${unrated} 家` : ''}${zoomed ? `；当前放大${zoomed.name}` : ''}`;
  const font = (px) => px * k;
  const cellLabel = (q) => (mobile ? q.short : q.name);

  // 格子：全图四个各占四分之一；放大时只画一个撑满
  const cells = (zoomed ? [zoomed] : QUADRANTS).map((q) => {
    const x = zoomed || !q.high[0] ? plot.x0 : divX, w = zoomed ? plot.x1 - plot.x0 : (q.high[0] ? plot.x1 - divX : divX - plot.x0);
    const y = zoomed || q.high[1] ? plot.y0 : divY, h = zoomed ? plot.y1 - plot.y0 : (q.high[1] ? divY - plot.y0 : plot.y1 - divY);
    const right = q.high[0], top = q.high[1];
    const tx = right ? x + w - 12 * k : x + 12 * k;
    const ty = top ? y + 12 * k + font(14) : y + h - 12 * k - (mobile ? 0 : font(12) + 4 * k);
    return (
      <g key={q.key} className="sb-bmap-cell" role={zoomed ? undefined : 'button'} tabIndex={zoomed ? undefined : 0} aria-label={zoomed ? undefined : `放大${q.name}，${counts[q.key]} 家`}
        onClick={zoomed ? undefined : () => setZoom(q.key)} onKeyDown={zoomed ? undefined : (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setZoom(q.key); } }}>
        <rect x={x} y={y} width={w} height={h} fill={q.fill} />
        <text x={tx} y={ty} textAnchor={right ? 'end' : 'start'} fontSize={font(14)} fontWeight="600" fill="var(--ui-ink)" fontFamily="var(--ui-font)"><title>{q.name}</title>{cellLabel(q)}</text>
        {!mobile && <text x={tx} y={ty + font(12) + 4 * k} textAnchor={right ? 'end' : 'start'} fontSize={font(12)} fill="var(--ui-muted)" fontFamily="var(--ui-font)">{q.desc}</text>}
      </g>
    );
  });

  const hovered = clusters.find((c) => c.key === hoverKey) || (selectedId != null ? clusters.find((c) => c.single && c.points[0].id === selectedId) : null);
  const tipText = hovered ? (hovered.single ? { title: hovered.points[0].name, note: hovered.points[0].summary || TONE_TEXT[hovered.points[0].tone] || '' } : { title: `${hovered.points.length} 家客户重叠`, note: hovered.points.slice(0, 3).map((p) => p.name).join('、') + (hovered.points.length > 3 ? '…' : '') + ' · 点开展开列表' }) : null;

  const handleCluster = (c) => { if (c.single) onPointClick?.(c.points[0]); else onClusterClick?.(c.points); };

  if (loading) {
    return <div className={`sb-bmap${className ? ` ${className}` : ''}`} style={style}><div ref={frameRef} className="sb-bmap-square" role="status" aria-label="作战地图正在加载"><Skeleton active paragraph={{ rows: 6 }} /></div></div>;
  }

  return (
    <div className={`sb-bmap${mobile ? ' sb-bmap-mobile' : ''}${className ? ` ${className}` : ''}`} style={style}>
      <div ref={frameRef} className="sb-bmap-square">
        <svg className="sb-bmap-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={summary} onMouseLeave={() => setHoverKey(null)}>
          {cells}
          {!zoomed && (
            <g className="sb-bmap-dividers" stroke="var(--ui-line)" strokeWidth={1 * k} strokeDasharray={`${6 * k} ${4 * k}`} pointerEvents="none">
              <line x1={divX} y1={plot.y0} x2={divX} y2={plot.y1} />
              <line x1={plot.x0} y1={divY} x2={plot.x1} y2={divY} />
            </g>
          )}
          <g className="sb-bmap-axes" pointerEvents="none" fontFamily="var(--ui-font)" fontSize={font(12)} fill="var(--ui-muted)">
            <line x1={plot.x0} y1={plot.y0} x2={plot.x0} y2={plot.y1} stroke="var(--ui-line)" strokeWidth={1 * k} />
            <line x1={plot.x0} y1={plot.y1} x2={plot.x1} y2={plot.y1} stroke="var(--ui-line)" strokeWidth={1 * k} />
            <text x={plot.x0} y={plot.y1 + font(12) + 6 * k} textAnchor="start">小</text>
            <text x={plot.x1} y={plot.y1 + font(12) + 6 * k} textAnchor="end">大</text>
            <text x={(plot.x0 + plot.x1) / 2} y={plot.y1 + font(12) + 6 * k} textAnchor="middle" fill="var(--ui-secondary)">客户潜力</text>
            <text x={plot.x0 - 6 * k} y={plot.y1} textAnchor="end">浅</text>
            <text x={plot.x0 - 6 * k} y={plot.y0 + font(12)} textAnchor="end">深</text>
            <text x={plot.x0 - 6 * k - font(12) - 2 * k} y={(plot.y0 + plot.y1) / 2} textAnchor="middle" fill="var(--ui-secondary)" transform={`rotate(-90 ${plot.x0 - 6 * k - font(12) - 2 * k} ${(plot.y0 + plot.y1) / 2})`}>关系深度</text>
          </g>
          <g className="sb-bmap-points">
            {clusters.map((c) => {
              const single = c.single;
              const p = c.points[0];
              const r = single ? c.maxDiameter / 2 : Math.max(14 * k, c.maxDiameter / 2 + 4 * k);
              const active = hoverKey === c.key || (single && selectedId != null && p.id === selectedId);
              const label = single ? `${p.name}${p.summary ? ` · ${p.summary}` : ''}` : `${c.points.length} 家客户重叠，点开展开列表`;
              return (
                <g key={c.key} className={`sb-bmap-point${active ? ' is-active' : ''}${!single ? ' sb-bmap-cluster' : ''}`} role="button" tabIndex={0} aria-label={label} aria-pressed={single && selectedId != null && p.id === selectedId ? true : undefined}
                  onMouseEnter={() => setHoverKey(c.key)} onMouseLeave={() => setHoverKey(null)} onFocus={() => setHoverKey(c.key)} onBlur={() => setHoverKey(null)}
                  onClick={(e) => { e.stopPropagation(); handleCluster(c); }} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); handleCluster(c); } }}>
                  <circle cx={c.x} cy={c.y} r={Math.max(r, 14 * k)} fill="transparent" />
                  {active && <circle cx={c.x} cy={c.y} r={r + 4 * k} fill="none" stroke="var(--ui-focus)" strokeWidth={2 * k} />}
                  {single
                    ? <circle cx={c.x} cy={c.y} r={r} fill={TONE_FILL[p.tone] || TONE_FILL.pending} stroke={TONE_STROKE[p.tone] || 'var(--ui-surface)'} strokeWidth={2 * k} />
                    : <><circle cx={c.x} cy={c.y} r={r} fill="var(--ui-primary)" stroke="var(--ui-surface)" strokeWidth={2 * k} /><text x={c.x} y={c.y + font(12) * 0.36} textAnchor="middle" fontSize={font(12)} fontWeight="600" fill="var(--ui-on-primary)" fontFamily="var(--ui-font)" pointerEvents="none">{c.points.length}</text></>}
                </g>
              );
            })}
          </g>
        </svg>
        {zoomed && <Button size="small" className="sb-bmap-back" onClick={() => setZoom(null)}>返回全部象限</Button>}
        {tipText && (
          <div className="sb-bmap-tip" role="tooltip" style={{ left: `${hovered.x / width * 100}%`, top: `${(hovered.y - (hovered.single ? hovered.maxDiameter / 2 : 14 * k) - 8 * k) / height * 100}%` }}>
            <b>{tipText.title}</b>{tipText.note && <span>{tipText.note}</span>}
          </div>
        )}
        {valid.length === 0 && (
          <div className="sb-bmap-empty">
            <p>{emptyText}</p>
            {emptyAction && <Button type="primary" size="small" onClick={emptyAction.onClick}>{emptyAction.label || '去客户列表'}</Button>}
          </div>
        )}
      </div>
      {mobile && (
        <div className="sb-bmap-counts" role="group" aria-label="各格子客户数">
          {QUADRANT_ORDER.map((key) => { const q = QUADRANTS.find((x) => x.key === key); return (
            <button key={key} type="button" className="sb-bmap-count" aria-pressed={zoom === key} onClick={() => setZoom(zoom === key ? null : key)} aria-label={`${q.name} ${counts[key]} 家`}>
              <b>{counts[key]}</b><span>{q.short}</span>
            </button>); })}
        </div>
      )}
      {unrated > 0 && (
        <button type="button" className="sb-bmap-unrated" onClick={onUnratedClick} disabled={!onUnratedClick}>待评估 {unrated} 家，还没登记潜力{onUnratedClick ? ' ›' : ''}</button>
      )}
    </div>
  );
}
