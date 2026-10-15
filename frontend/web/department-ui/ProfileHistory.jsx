import React from 'react';
import {historyView} from './profile-history.js';

export default function ProfileHistory({history = [], code = 'overall', unit = '分', factRatios = false, onReview}) {
  const view = historyView(history, code, factRatios);
  if (!view.rows.length) return <p className="ds-muted ds-pf-empty">暂无已保存的历史评估</p>;
  const width = 600, height = 164, left = 32, right = 14, top = 12, bottom = 28;
  const x = p => left + p.x * (width - left - right);
  const y = score => height - bottom - score / 100 * (height - top - bottom);
  return <div className="ds-pf-history">
    {view.points.length > 0 ? <svg className="ds-pf-spark" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`历史${factRatios ? '事实比例' : '评分'}，按实际日期排列，缺失评估处不连线`}>
      {[0, 50, 100].map(value => <g key={value}><line x1={left} x2={width - right} y1={y(value)} y2={y(value)} className="ds-pf-spark-grid" /><text x={left - 7} y={y(value)} textAnchor="end" dominantBaseline="middle" className="ds-pf-spark-label">{value}</text></g>)}
      {view.segments.map(([a, b], index) => <path key={index} d={`M${x(a)},${y(a.score)} L${x(b)},${y(b.score)}`} className="ds-pf-spark-line" />)}
      {view.points.map(point => <circle key={point.key} cx={x(point)} cy={y(point.score)} r="4" tabIndex={0} className="ds-pf-spark-dot" aria-label={`${point.date}：${point.score}${unit}${factRatios ? '' : `，评估版本 ${point.ruleLabel}`}`}><title>{point.date} · {point.score}{unit}{factRatios ? '' : ` · 评估版本 ${point.ruleLabel}`}</title></circle>)}
      <text x={left} y={height - 6} className="ds-pf-spark-label">{view.firstDate}</text>
      {view.lastDate !== view.firstDate && <text x={width - right} y={height - 6} textAnchor="end" className="ds-pf-spark-label">{view.lastDate}</text>}
    </svg> : <p className="ds-muted ds-pf-empty">暂无日期和评分均完整的历史评估</p>}
    <p className="ds-muted ds-pf-meta">{factRatios ? '展示各次保存的事实指标。' : '展示当时保存的评分，不用当前规则重算历史。'}{view.multipleRules ? '规则有变化，不跨规则连线。' : ''}{view.unknownRules ? '部分历史口径未知，仅展示点位。' : ''}</p>
    <details className="ds-pf-history-details"><summary>查看历史记录（{view.rows.length}）</summary><table><thead><tr><th>评估日期</th><th>{factRatios ? '事实指标' : '历史评分'}</th><th>评估规则</th>{onReview && <th>依据</th>}</tr></thead><tbody>{view.rows.map(row => <tr key={row.key}><td>{row.date || '未记录'}{row.generation != null && <span className="ds-muted"> · 第 {row.generation} 次</span>}{row.reviewedAt && <div className="ds-muted">完成于 {new Date(row.reviewedAt).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}</div>}</td><td>{row.score === null ? '未评估' : `${row.score}${unit}`}</td><td>{row.ruleLabel}</td>{onReview && <td>{row.reviewId ? <button type="button" className="ds-pf-evidence-link" onClick={()=>onReview(row.reviewId)}>查看依据</button> : '未记录'}</td>}</tr>)}</tbody></table></details>
  </div>;
}
