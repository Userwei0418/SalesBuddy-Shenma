import React from 'react';
import { Button, Input } from 'antd';
import { SbBottomBar, SbField, SbStatePanel, SbStatusTag, SbTextarea } from '@shandiant/ui-react';
import './demo-create.css';

// Demo 场景（原 pages/demo-create/index，FDE 用）：一次登记多个场景、编辑单个场景、查看详情并删除。语音录入沿用原页面的录音与转写。
export function supportsDemoCreate(page) {
  return Boolean(page && page.route === 'pages/demo-create/index');
}

export default function DemoCreate({ page, data: d, invoke }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const scenes = d.demoScenes || [];
  const opportunityName = d.opportunity?.name || '';
  if (!d.eligible) return <section className="ds-demo" aria-label="Demo 场景"><div className="ds-panel ds-demo-main"><SbStatePanel state={/正在/.test(d.accessMessage || '') ? 'loading' : 'forbidden'} title={d.accessMessage || '暂不可用'} description="请从关联商机进入。" /></div></section>;
  if (d.viewing) {
    const s = d.sceneDetail || {};
    return <section className="ds-demo" aria-label="Demo 场景详情">
      <div className="ds-panel ds-demo-main">
        <div className="ds-demo-head"><div><span className="ds-muted">{opportunityName}</span><h2>{s.name}</h2></div><SbStatusTag tone="good" label="已登记" /></div>
        <div className="ds-demo-body">
          <SbField label="场景描述"><p className="ds-demo-copy">{s.description}</p></SbField>
          <p className="ds-muted">{s.creatorName} · {s.createdLabel} · 修改操作会保留记录</p>
        </div>
        {s.can_edit && <div className="ds-demo-bar"><SbBottomBar reason="删除后无法恢复" secondary={{ label: '删除', disabled: Boolean(d.saving), onClick: () => call('deleteScene') }} primary={{ label: '编辑场景', disabled: Boolean(d.saving), onClick: () => call('startEditing') }} /></div>}
      </div>
    </section>;
  }
  const voiceLabel = id => d.voiceId === id ? (d.voiceState === 'recording' ? '录音中 · 点击结束' : d.voiceState === 'transcribing' ? '正在转写…' : '正在准备…') : '语音录入';
  return <section className="ds-demo" aria-label={d.editing ? '编辑 Demo 场景' : '创建 Demo 场景'}>
    <div className="ds-panel ds-demo-main">
      <div className="ds-demo-head"><div><span className="ds-muted">{opportunityName}</span><h2>{d.editing ? '编辑 Demo 场景' : '创建 Demo 场景'}</h2></div><span className="ds-muted">每个场景计 1 个 Demo，自动关联当前商机</span></div>
      <div className="ds-demo-body">
        <div className="ds-demo-list">
          {scenes.map((s, i) => <article key={s.id} className="ds-demo-scene">
            <header><b>场景 {i + 1}</b>{scenes.length > 1 && <Button type="link" size="small" onClick={() => call('removeDemoScene', { dataset: { id: s.id } })}>移除</Button>}</header>
            <SbField label="场景名称" required>
              <Input value={s.name || ''} maxLength={100} placeholder="例如：运输调度异常预警" onChange={e => call('editDemoScene', { dataset: { id: s.id, field: 'name' }, detail: { value: e.target.value } })} />
            </SbField>
            <SbField label="场景描述" required help="转写后追加到描述，可继续编辑">
              <SbTextarea value={s.description || ''} maxLength={2000} autoSize={{ minRows: 3, maxRows: 8 }} placeholder="描述业务问题、演示内容和预期效果" onChange={v => call('editDemoScene', { dataset: { id: s.id, field: 'description' }, detail: { value: v } })} />
              <Button size="small" className="ds-demo-voice" danger={d.voiceId === s.id && d.voiceState === 'recording'} disabled={Boolean(d.voiceState) && (d.voiceId !== s.id || d.voiceState !== 'recording')} onClick={() => call('toggleVoice', { dataset: { id: s.id } })}>{voiceLabel(s.id)}</Button>
            </SbField>
          </article>)}
        </div>
        {!d.editing && <Button disabled={scenes.length >= 20} onClick={() => call('addDemoScene')}>添加 Demo 场景</Button>}
        {d.demoError && <p className="ds-demo-error" role="alert">{d.demoError}</p>}
      </div>
      <div className="ds-demo-bar"><SbBottomBar reason="保存后同步到当前商机，并计入登记数量" primary={{ label: d.editing ? '保存修改' : `保存 ${scenes.length} 个场景`, loading: Boolean(d.saving), disabled: Boolean(d.voiceState), disabledReason: d.voiceState ? '先结束语音录入' : '', onClick: () => call('submitDemoScenes') }} /></div>
    </div>
  </section>;
}
