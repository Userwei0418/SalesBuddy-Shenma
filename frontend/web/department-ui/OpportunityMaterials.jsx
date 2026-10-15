import React from 'react';
import {Button, Checkbox, Descriptions, Table} from 'antd';
import {SbDatePicker, SbField, SbLabeledSelect, SbListRow} from '@shandiant/ui-react';
import {Input, SbSearch, SbTextarea} from './ImeInput.jsx';

const relations = [{value: 'direct', label: '直销'}, {value: 'distributor', label: '渠道商采购'}, {value: 'downstream', label: '渠道下游终端'}];
const ruleLabels = {manual: '人工核对', delivery: '交付', monthly: '按月服务', consumption: '按消耗'};

// Product-line membership comes from the shared projection, including legacy
// labels and cross-line materials. Do not infer membership from names here.
export function OpportunityProductLines({form, invokeOn, disabled}) {
  const d = form.data || {}, selected = d.opportunityProductLines || [], options = d.productLineOptions || [];
  const readonly = Boolean(d.productLineReadonly);
  const blocked = Boolean(disabled || d.productLinesLoading || d.productLinesError || d.materialLinesLoading || d.materialLoadError);
  return <section className="ds-oc-product-lines" aria-label="商机产品线" aria-busy={Boolean(d.productLinesLoading)}>
    <div className="ds-oc-material-head"><h3>{readonly ? '物料所属产品线' : '意向产品线'}</h3>
      {!readonly && <Button size="small" disabled={Boolean(disabled)} loading={Boolean(d.productLinesLoading)} onClick={() => invokeOn(form, 'loadProductLines')}>刷新目录</Button>}
    </div>
    {readonly ? <>
      <div className="ds-oc-tags">{selected.map(row => <span className="ds-oc-tag" key={row.id}>{row.name}{row.is_active === false ? ' · 已停用' : ''}</span>)}</div>
      <p className="ds-muted ds-oc-note">随当前物料展示，可包含多条产品线。</p>
      {!!d.productLineMissingCount && <p className="ds-oc-error ds-oc-note">{d.productLineMissingCount} 条物料尚无产品线归属，请联系运营维护。</p>}
    </> : <>
      <div className="ds-oc-product-options">{options.map(row => <Checkbox key={row.id} checked={Boolean(row.selected)} disabled={blocked || row.is_active === false && !row.selected} onChange={() => invokeOn(form, 'intentProductLine', {dataset: {id: row.id}})}>{row.name}{row.is_active === false ? row.selected ? ' · 已停用，可移除' : ' · 已停用' : ''}</Checkbox>)}</div>
      {d.productLinesLoading ? <p className="ds-muted ds-oc-note" role="status">正在读取产品线…</p> : !d.productLinesError && !options.length ? <p className="ds-muted ds-oc-note">暂无可选产品线，请联系运营维护目录。</p> : <p className="ds-muted ds-oc-note">尚未添加物料时可多选；添加物料后按物料归属展示。</p>}
    </>}
    {d.productLinesError && <p className="ds-oc-error ds-oc-note" role="alert">{d.productLinesError} <Button size="small" disabled={Boolean(disabled)} onClick={() => invokeOn(form, 'loadProductLines')}>重试</Button></p>}
    {d.legacyProductLine && <p className="ds-muted ds-oc-note">历史填写：{d.legacyProductLine}（原值保留）</p>}
  </section>;
}

// Consume the shared read-only display model; never derive money or fill old
// descriptions from the live catalog here.
export function MaterialCalculationTrace({display, onToggle}) {
  return <div aria-label="物料规则与计算过程">
    {(display.sourceLabel || display.periodLabel) && <p className="ds-oc-reference-caption">{display.sourceLabel}{display.sourceLabel && display.periodLabel ? ' · ' : ''}{display.periodLabel}</p>}
    <p><b>{display.summary}</b></p>
    <p className="ds-muted ds-oc-note">后端计算参考 · 不是实际确收，也不会自动填写销售预测。</p>
    {(display.reasons || []).map((reason, index) => <p key={index} className="ds-muted ds-oc-note">{reason}</p>)}
    {display.explanation && <p className="ds-muted ds-oc-note">{display.explanation}</p>}
    {display.versionText && <p className="ds-muted ds-oc-note">{display.versionText}</p>}
    {(display.lines || []).map(line => <section key={line.key} className="ds-oc-material-row" aria-label={`${line.name}的计算依据`}>
      <div className="ds-oc-material-head"><div><b>{line.name} · {line.statusText}</b><p>{line.amountText}</p></div><Button size="small" aria-expanded={Boolean(line.expanded)} onClick={()=>onToggle(line.key)}>{line.expanded ? '收起计算依据' : '查看计算依据'}</Button></div>
      {(line.reasons || []).map((reason, index) => <p key={index} className="ds-muted ds-oc-note">{reason}</p>)}
      {line.expanded && <>
        <p className="ds-muted ds-oc-note">{line.versionText}</p>
        <h4>{line.definition.statusText}</h4>
        <Descriptions bordered size="small" column={1} items={[
          ['规则说明', line.definition.description], ['适用条件', line.definition.applicability], ['例外情况', line.definition.exceptions],
        ].filter(([, value])=>value).map(([label, children])=>({key:label,label,children}))}/>
        {(line.definition.examples || []).map((example,index)=><p key={index}><b>示例：{example.scenario}</b><br/>{example.explanation}</p>)}
        {(line.sections || []).map((section,index)=><section key={index}>
          <h4>{section.title}</h4>
          {!!section.rows.length && <Descriptions bordered size="small" column={2} items={section.rows.map((row,rowIndex)=>({key:rowIndex,label:row.label,children:row.value}))}/>}
          {section.note && <p className="ds-muted ds-oc-note">{section.note}</p>}
        </section>)}
        {!!line.months?.length && <section><h4>逐月分配（{line.months.length} 个月）</h4><Table size="small" pagination={false} rowKey="month" dataSource={line.months} scroll={{x:720,y:360}} columns={[
          {title:'月份',dataIndex:'month'}, {title:'分配含税额',dataIndex:'amountText'}, {title:'基础分值（分）',dataIndex:'base'},
          {title:'尾部余分（分）',dataIndex:'tail'}, {title:'所选季度计入情况',dataIndex:'statusText'},
        ]}/></section>}
      </>}
    </section>)}
  </div>;
}

// The shared form owns freshness and requests. Rendering a reference never
// saves a draft or copies the calculated amount into the sales forecast.
export function MaterialForecastReference({form, invokeOn, disabled}) {
  const d = form.data || {}, preview = d.materialPreview || {}, options = d.quarterOptions || [];
  const on = (name, payload = {}) => invokeOn(form, name, payload);
  const waiting = Boolean(preview.loading || preview.pending);
  const draft = preview.result?.reference_kind === 'sales_forecast_draft_reference' && preview.result?.persisted === false;
  const current = !waiting && !preview.error && (!d.form?.materials_dirty || draft);
  const blocked = Boolean(disabled || d.materialLinesLoading || d.materialLoadError || !options[d.quarterIndex]);
  return <section className="ds-oc-material-reference" aria-label="季度计算参考" aria-busy={waiting}>
    <div className="ds-oc-material-head">
      <div><h3>季度计算参考</h3><p className="ds-muted ds-oc-note">按本次输入试算，确认保存商机后才提交交易信息。</p></div>
      <div className="ds-oc-reference-controls">
        <SbLabeledSelect label="参考季度" allowClear={false} disabled={Boolean(disabled) || !options.length} value={d.quarterIndex} options={options.map((q, i) => ({value: i, label: q.label}))} onChange={value => on('quarter', {detail: {value}})} width={180} />
        <Button size="small" disabled={blocked} loading={Boolean(preview.loading)} onClick={() => on('previewMaterials')}>{preview.error ? '重试计算' : '重新计算'}</Button>
      </div>
    </div>
    {waiting && <p className="ds-muted ds-oc-note" role="status">{preview.loading ? '正在计算本次输入的季度参考…' : '输入已更新，正在准备计算…'}</p>}
    {preview.error && <p className="ds-oc-error ds-oc-note" role="alert">{preview.error}</p>}
    {current && preview.display && <MaterialCalculationTrace display={preview.display} onToggle={key => on('toggleMaterialTrace', {dataset: {key}})} />}
    {!waiting && !preview.error && (!current || !preview.display) && <p className="ds-muted ds-oc-note">填写物料和交易信息后，在这里查看季度参考；缺项会逐项提示。</p>}
  </section>;
}

// Shared form owns stable identities, drafts, validation and mutations. PC only
// renders its values; tax and period allocation remain server calculations.
export default function OpportunityMaterials({form, invokeOn, disabled}) {
  const d = form.data || {}, v = d.form || {}, lines = v.material_lines || [];
  // Detail reads may retain the relationship ID without a display name. Keep
  // that saved relationship visible; resolving a label must not edit the draft.
  const upstreamLabel = v.upstream_opportunity_id
    ? v.upstream_opportunity_name || (d.upstreamOptions || []).find(row => row.id === v.upstream_opportunity_id)?.name || '已关联上游商机'
    : '选择上游商机';
  disabled = Boolean(disabled || d.materialLinesLoading || d.materialLoadError);
  const on = (name, payload = {}) => invokeOn(form, name, payload);
  const input = (row, field, value, method = 'materialInput') => on(method, {dataset: {key: row.client_key, field}, detail: {value}});
  const numeric = (row, field, label, required = false, integer = false) => <SbField label={label} required={required} key={field}><Input aria-label={label} inputMode={integer ? 'numeric' : 'decimal'} value={row[field] ?? ''} disabled={disabled} onChange={e => input(row, field, e.target.value)} /></SbField>;
  const date = (row, field, label) => <SbField label={label} key={field}>
    <div onChangeCapture={event => {
      // The picker only commits parsed dates. Keyboard deletion must also clear
      // the shared draft before Tab/blur or a recalculation can reuse its value.
      if (!disabled && row[field] && event.target.tagName === 'INPUT' && event.target.value === '') input(row, field, '');
    }}><SbDatePicker value={row[field] || null} disabled={disabled} width="100%" onChange={value => input(row, field, value || '')} /></div>
  </SbField>;
  return <section className="ds-oc-materials" aria-label="交易物料">
    <OpportunityProductLines form={form} invokeOn={invokeOn} disabled={disabled} />
    <div className="ds-oc-material-head"><h3>交易物料</h3><Button disabled={disabled || Boolean(d.materialLoadError)} onClick={() => on('openMaterials')}>添加物料</Button></div>
    <p className="ds-muted ds-oc-note">选择运营维护的目录，填写本次成交信息。同一物料分批交易可添加多行。物料调整不改变运营已确认的实际。</p>
    <div className="ds-oc-grid">
      <SbField label="交易关系" required={lines.length > 0}><SbLabeledSelect value={v.trade_relation || undefined} options={relations} allowClear={false} placeholder="请选择交易关系" disabled={disabled} width="100%" onChange={value => on('tradeRelation', {detail: {value}})} /></SbField>
      {v.trade_relation === 'downstream' && <SbField label="上游渠道商商机" required><button type="button" className="ds-oc-picker-btn" disabled={disabled} onClick={() => on('openUpstreams')}><span>{upstreamLabel}</span><span className="ds-oc-link">{v.upstream_opportunity_id ? '重新选择' : '选择'}</span></button></SbField>}
    </div>
    {d.showUpstreamSearch && <div className="ds-oc-material-picker">
      <div className="ds-oc-material-head"><b>选择上游商机</b><Button size="small" onClick={() => on('closeUpstreams')}>关闭</Button></div>
      <SbSearch value={d.upstreamQuery || ''} placeholder="搜索渠道商商机名称" disabled={disabled} loading={Boolean(d.upstreamLoading)} onChange={value => on('upstreamSearch', {detail: {value}})} />
      {d.upstreamError ? <p className="ds-oc-error" role="alert">{d.upstreamError} <Button size="small" onClick={() => on('loadUpstreams')}>重试</Button></p> : <div className="ds-oc-results">{(d.upstreamOptions || []).map(row => <SbListRow key={row.id} name={row.name} summary={row.customer_name || ''} selected={v.upstream_opportunity_id === row.id} onClick={disabled ? undefined : () => on('chooseUpstream', {dataset: {id: row.id}})} />)}{d.upstreamLoading ? <p className="ds-muted">正在加载上游商机…</p> : !(d.upstreamOptions || []).length ? <p className="ds-muted">暂无可选择的上游商机</p> : d.upstreamOptions.length < d.upstreamTotal && <Button size="small" disabled={disabled} onClick={() => on('loadUpstreams', {dataset: {more: true}})}>加载更多上游商机</Button>}</div>}
    </div>}
    {d.showMaterialSearch && <div className="ds-oc-material-picker">
      <div className="ds-oc-material-head"><b>物料目录</b><Button size="small" onClick={() => on('closeMaterials')}>关闭</Button></div>
      <div className="ds-oc-grid"><SbSearch value={d.materialQuery || ''} placeholder="搜索物料名称" disabled={disabled} loading={Boolean(d.materialsLoading)} onChange={value => on('materialSearch', {detail: {value}})} /><SbLabeledSelect label="产品线" value={d.materialProductLineId || undefined} placeholder="全部产品线" disabled={disabled} width="100%" options={(d.materialProductLines || []).map(row => ({value: row.id, label: row.name}))} onChange={value => on('materialProductLine', {detail: {value: value || ''}})} /></div>
      {d.materialsError ? <p className="ds-oc-error" role="alert">{d.materialsError} <Button size="small" onClick={() => on('loadMaterials')}>重试</Button></p> : <div className="ds-oc-results">
        {(d.materialCatalog || []).map(row => <SbListRow key={row.id} name={row.name} summary={`${row.product_line_name || '未标注产品线'} · 目录价 ${row.catalog_price ?? '未填'} 元 / ${row.unit || '单位未标注'}`} onClick={disabled ? undefined : () => on('chooseMaterial', {dataset: {id: row.id}})} />)}
        {d.materialsLoading ? <p className="ds-muted">正在加载物料…</p> : !(d.materialCatalog || []).length ? <p className="ds-muted">目录暂无匹配物料，请联系运营维护。</p> : (d.materialCatalog || []).length < d.materialTotal && <Button size="small" disabled={disabled} onClick={() => on('loadMaterials', {dataset: {more: true}})}>加载更多物料</Button>}
      </div>}
    </div>}
    {d.materialLinesLoading && <p className="ds-muted ds-oc-note" role="status">正在读取交易物料…</p>}
    {d.materialLoadError && <p className="ds-oc-error" role="alert">{d.materialLoadError} <Button size="small" onClick={() => on('retryMaterialLines')}>重新读取物料</Button></p>}
    {lines.map((row, index) => {
      const s = row.catalog_snapshot || {}, rule = s.recognition_rule, visible = row.inputVisibility || {};
      return <article className="ds-oc-material-row" key={row.client_key} aria-label={`物料 ${index + 1}：${s.name || '未命名'}`}>
        <div className="ds-oc-material-head"><div><b>{index + 1}. {s.name || '物料名称未提供'}</b><p className="ds-muted ds-oc-note">{s.product_line_name || '未标注产品线'} · {ruleLabels[rule] || '规则未配置'} · 目录价 {s.catalog_price ?? '未填'} 元 / {s.unit || '单位未标注'}</p></div><Button size="small" danger disabled={disabled} onClick={() => on('removeMaterial', {dataset: {key: row.client_key}})}>移除</Button></div>
        <div className="ds-oc-material-grid">
          {numeric(row, 'quantity', `数量${s.unit ? `（${s.unit}）` : ''}`, true)}{numeric(row, 'unit_price', '成交单价（元，含税）', true)}
          <SbField label="用途" required><SbLabeledSelect value={row.usage || undefined} placeholder="请选择用途" allowClear={false} disabled={disabled} width="100%" options={[{value: 'self_use', label: '自用'}, {value: 'resale', label: '转售'}]} onChange={value => input(row, 'usage', value, 'materialUsage')} /></SbField>
          {visible.delivery && date(row, 'expected_delivery_on', '预计交付日期')}
          {visible.monthly && <>{date(row, 'expected_start_on', '预计服务开始日期')}{numeric(row, 'service_months', '服务月数', false, true)}</>}
          {visible.accounts && numeric(row, 'expected_account_count', '预计账号数', false, true)}
          {visible.oa && date(row, 'expected_oa_on', '预计 OA 日期')}
          {visible.consumption && <>{numeric(row, 'expected_consumption_amount', '预计消耗金额（元，含税）')}{numeric(row, 'expected_consumption_year', '预计消耗年份', false, true)}<SbField label="预计消耗季度"><SbLabeledSelect value={row.expected_consumption_quarter ? Number(row.expected_consumption_quarter) : undefined} disabled={disabled} width="100%" options={[1, 2, 3, 4].map(value => ({value, label: `Q${value}`}))} onChange={value => input(row, 'expected_consumption_quarter', value == null ? '' : String(value))} /></SbField></>}
        </div>
        <p className="ds-muted ds-oc-note">交易行金额（元，含税）：{row.line_amount_text ?? '待填写数量与单价'}{row.discount_text ? ` · 折扣：${row.discount_text}` : ''}</p>
        <SbField label="备注"><SbTextarea value={row.note || ''} maxLength={1000} disabled={disabled} onChange={value => input(row, 'note', value)} /></SbField>
      </article>;
    })}
    {!lines.length && !d.materialLoadError && !d.materialLinesLoading && <p className="ds-muted ds-oc-note">当前没有交易物料，销售仍可独立填写季度预测。</p>}
    <MaterialForecastReference form={form} invokeOn={invokeOn} disabled={disabled} />
    {!!(d.retiredMaterialLines || []).length && <details className="ds-oc-retired"><summary>已移除物料（{d.retiredMaterialLines.length}）</summary><p className="ds-muted">历史物料及其实际记录保留。</p><ul>{d.retiredMaterialLines.map(row => <li key={row.id}>{row.catalog_snapshot?.name || '物料名称未提供'} · {row.quantity} × {row.unit_price} 元</li>)}</ul></details>}
  </section>;
}
