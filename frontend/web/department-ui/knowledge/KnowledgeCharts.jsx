import React from 'react';
import {Empty2} from '../pc/ui.jsx';
const COLORS = ['#346bd9','#36a2a0','#8b70c6','#e7a54d','#c96d95','#27805e','#708ca6'];
const n = value => Math.max(0, Number(value) || 0);
export function DonutChart({rows, title, onSelect}) {
  const total = rows.reduce((sum, row) => sum + n(row.count), 0);
  if (!total) return <Empty2 title={`暂无${title}`} />;
  let offset = 0;
  return <div className="kb-chart-donut"><svg viewBox="0 0 200 200" role="img" aria-label={`${title}，共 ${total} 份`}><circle cx="100" cy="100" r="72" fill="none" stroke="var(--ui-line)" strokeWidth="30" />{rows.map((row,index) => {const share=n(row.count)/total*100, start=offset;offset+=share;return <circle key={row.code} cx="100" cy="100" r="72" pathLength="100" fill="none" stroke={COLORS[index%COLORS.length]} strokeWidth="30" strokeDasharray={`${share} ${100-share}`} strokeDashoffset={-start} transform="rotate(-90 100 100)"><title>{`${row.label}：${row.count} 份`}</title></circle>;})}<text x="100" y="98" textAnchor="middle" className="kb-chart-total">{total}</text><text x="100" y="121" textAnchor="middle" className="kb-chart-caption">份材料</text></svg><div className="kb-chart-legend">{rows.map((row,index)=><button key={row.code} type="button" onClick={()=>onSelect?.(row)} disabled={!onSelect}><i style={{background:COLORS[index%COLORS.length]}}/><span>{row.label}</span><small>{row.count} · {Math.round(n(row.count)/total*100)}%</small></button>)}</div></div>;
}
export function TrendChart({rows}) {
  if (!rows.length) return <Empty2 title="暂无收录趋势" />;
  const max=Math.max(1,...rows.map(row=>n(row.count))), x=index=>46+index/Math.max(1,rows.length-1)*450,y=value=>210-n(value)/max*170;
  const external=rows.every(row=>Number.isFinite(Number(row.external_count)) && row.external_count != null);
  const line=key=>rows.map((row,index)=>`${x(index)},${y(row[key])}`).join(' ');
  const ticks=[...new Set([0,Math.floor((rows.length-1)/2),rows.length-1])];
  return <><svg viewBox="0 0 530 250" className="kb-chart-trend" role="img" aria-label="近30天材料累计收录趋势">{[0,max/2,max].map((v,index)=><g key={index}><line x1="46" x2="496" y1={y(v)} y2={y(v)} stroke="var(--ui-line)"/><text x="35" y={y(v)+4} textAnchor="end">{Number.isInteger(v)?v:v.toFixed(1)}</text></g>)}<polyline points={line('count')} fill="none" stroke={COLORS[0]} strokeWidth="3"/>{external&&<polyline points={line('external_count')} fill="none" stroke={COLORS[1]} strokeWidth="3" strokeDasharray="6 5"/>}{ticks.map(index=><g key={index}><circle cx={x(index)} cy={y(rows[index].count)} r="4" fill={COLORS[0]}><title>{`${rows[index].date}：累计 ${rows[index].count} 份${external?`，可对客或公开 ${rows[index].external_count} 份`:''}`}</title></circle><text x={x(index)} y="238" textAnchor="middle">{rows[index].date.slice(5).replace('-','.')}</text></g>)}</svg><div className="kb-chart-key"><span><i style={{background:COLORS[0]}}/>累计材料</span>{external&&<span><i style={{background:COLORS[1]}}/>其中可对客 / 公开</span>}</div></>;
}
export function ProductChart({rows,onSelect}) {
  if (!rows.length) return <Empty2 title="暂无产品资料" />;
  const max=Math.max(1,...rows.map(row=>n(row.count)));
  return <div className="kb-chart-products">{rows.map((row,index)=><button key={row.code} type="button" onClick={()=>onSelect(row)} aria-label={`${row.label} ${row.count} 份`}><div className="kb-chart-column"><b>{row.count}</b><span style={{height:`${Math.max(2,n(row.count)/max*170)}px`,background:COLORS[index%COLORS.length]}}/></div><small>{row.label}</small></button>)}</div>;
}
