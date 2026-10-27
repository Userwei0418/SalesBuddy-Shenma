import React from 'react';
import {Button, Checkbox} from 'antd';
import {SbField, SbLabeledSelect} from '@shandiant/ui-react';
import {Input} from './ImeInput.jsx';

export default function OpportunityRecognizedActual({form, invokeOn, disabled}) {
  const f = form.data || {};
  if (!form.properties?.existing || form.properties?.visitContext || !f.recognizedActualVisible) return null;
  const loading = Boolean(f.recognizedActualLoading), loaded = f.recognizedActualLoaded === true;
  const locked = Boolean(disabled || loading || !loaded || f.recognizedActualEditable !== true);
  const call = (name, payload) => { if (!disabled && !loading) invokeOn(form, name, payload); };
  const input = (id, key, value) => { if (!locked) call('recognizedActualInput', {dataset: {id, key}, detail: {value}}); };
  const rows = f.recognizedActualRows || [];
  return <section className="ds-oc-recognized ds-oc-collection" aria-label="季度实际不含税确收" aria-busy={loading}>
    <div className="ds-oc-material-head"><h3>季度实际不含税确收 <small className="ds-muted">选填</small></h3>
      <div><span className="ds-muted">随商机一起保存</span><Button type="link" size="small" disabled={disabled || loading} onClick={() => call('retryRecognizedActual')}>刷新状态</Button></div>
    </div>
    <div className="ds-oc-forecast-head">
      <SbLabeledSelect label="季度" allowClear={false} value={f.recognizedActualQuarterIndex} disabled={disabled || loading} width={180}
        options={(f.recognizedActualQuarterOptions || []).map((row, index) => ({value: index, label: row.label}))}
        onChange={value => call('recognizedActualQuarter', {detail: {value}})} />
      <span className="ds-muted">{loading ? '正在读取实际确收…' : '按物料填写本季度累计不含税金额，保存即生效'}</span>
    </div>
    {f.recognizedActualError && <p className="ds-oc-error" role="alert">{f.recognizedActualError}</p>}
    {loaded && <>
      <div className="ds-oc-collection-summary">
        <div><span>当前已保存金额</span><strong>{f.recognizedActualCurrentText}</strong></div>
        {!!rows.length && <div><span>物料填写合计</span><strong>{f.recognizedActualTotalText}</strong></div>}
      </div>
      {f.recognizedActualReason && <p className="ds-muted ds-oc-note">{f.recognizedActualReason}</p>}
      {f.recognizedActualAttributionText && <p className="ds-oc-recognized-notice">{f.recognizedActualAttributionText}</p>}
      {!rows.length && <p className="ds-muted ds-oc-note">尚无已保存的交易物料。新增物料后先保存商机，再按物料登记；已有历史总额保持原记录，不自动拆分。</p>}
      {rows.map(item => <div key={item.material_line_id} className="ds-oc-recognized-row">
        <div className="ds-oc-material-head"><h4>{item.material_name}</h4><span className="ds-muted">{item.is_active ? '当前物料' : '历史物料'}</span></div>
        <p className="ds-muted ds-oc-note">{item.material_code} · 当前保存：{item.currentText}</p>
        {f.recognizedActualNeedsReview && item.dirty && <p className="ds-oc-recognized-notice">最新登记依据：{item.currentEvidence || '未填写'}{item.currentNote ? `；备注：${item.currentNote}` : ''}</p>}
        {f.recognizedActualEditable && item.eligible ? <>
          <div className="ds-oc-grid">
            <SbField label="本季度累计不含税确收（万元）" help={item.amountPreview || '未登记请留空；明确为零请填 0。'}>
              <Input aria-label={`${item.material_name} 本季度累计不含税确收（万元）`} inputMode="decimal" value={item.amount ?? ''} disabled={locked} placeholder="未登记"
                onChange={event => input(item.material_line_id, 'amount', event.target.value)} />
            </SbField>
            <SbField label="确收依据" required={Boolean(item.dirty)} help="登记和调整金额、依据或备注时必填。">
              <Input value={item.evidence || ''} disabled={locked} maxLength={160} placeholder="验收单、财务凭证等依据"
                onChange={event => input(item.material_line_id, 'evidence', event.target.value)} />
            </SbField>
            <div className="ds-oc-extra-wide"><SbField label="备注" help="选填">
              <Input.TextArea value={item.note || ''} disabled={locked} maxLength={1000} autoSize={{minRows: 1, maxRows: 3}} placeholder="补充登记或调整说明"
                onChange={event => input(item.material_line_id, 'note', event.target.value)} />
            </SbField></div>
          </div>
        </> : <><p className="ds-muted ds-oc-note">{item.eligible ? '当前仅可查看' : '此物料不在本商机登记确收'}</p>
          {item.source_ref && <p className="ds-muted ds-oc-note">确收依据：{item.source_ref}</p>}
          {item.note && <p className="ds-muted ds-oc-note">备注：{item.note}</p>}</>}
      </div>)}
      {f.recognizedActualNeedsReview && <div className="ds-oc-recognized-notice"><p>实际确收已有更新，已保留你修改的输入。请核对最新已登记金额和依据。</p>
        <Checkbox disabled={locked} onChange={event => input(null, 'reviewed', event.target.checked)}>已核对最新登记，保存本次修改</Checkbox>
      </div>}
      {f.recognizedActualReplaceRequired && <div className="ds-oc-recognized-notice"><p>本季度存在其他来源的确收总额，按物料登记后将采用物料实绩，不与旧来源累加。请核对本季度完整物料登记。</p>
        <Checkbox checked={Boolean(f.recognizedActualReplaceSource)} disabled={locked} onChange={event => input(null, 'replaceSource', event.target.checked)}>以本次物料登记替换原有确收来源</Checkbox>
      </div>}
      {f.recognizedActualDirty && <Button type="link" className="ds-oc-recognized-reset" disabled={disabled || loading} onClick={() => call('resetRecognizedActualDraft')}>撤销本季度输入</Button>}
    </>}
    <p className="ds-muted ds-oc-note">每项按新的季度累计金额替换，未修改的物料保持原值。总额自动汇总；预测、ACV 和回款不自动转成确收。物料调整后需先保存商机，再登记实际确收。</p>
  </section>;
}
