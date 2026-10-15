import React from 'react';
import {Segmented, Tooltip} from 'antd';
import {Kpi} from './pc/ui.jsx';

// PC 2.0 layout, with values and metric definitions kept by the Shenma page.
export default function ShenmaMetricStrip({items, loading, periods = [], period, onPeriodChange, caliber, extra}) {
  return <div className="pc-overview sm-overview">
    <div className="pc-kpis">{items.map(item => <Kpi key={item.key} label={item.label} value={item.value}
      sub={item.note} loading={loading} onClick={item.onClick} tone={item.tone} missingText={item.missingText}/>)}</div>
    {(extra || periods.length > 0 || caliber) && <div className="pc-overview-controls">
      {extra}
      {periods.length > 0 && <Segmented size="small" options={periods} value={period} onChange={onPeriodChange}/>}
      {caliber && <Tooltip title={caliber}><button type="button" className="pc-overview-help" aria-label="查看统计口径">ⓘ</button></Tooltip>}
    </div>}
  </div>;
}
