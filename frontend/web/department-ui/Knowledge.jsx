import {Input} from './ImeInput.jsx';
import React, {useEffect, useMemo, useState} from 'react';
import {Alert, App, Avatar, Button, Drawer, Pagination, Segmented, Select, Table, Tooltip} from 'antd';
import {useLocalTablePagination} from './pc/list-pagination.js';
import {NavIcon} from './pc/icons.jsx';
import {Copyable, Empty2, LoadState, PageHead, Panel, Tag2, downloadText, fuzzyMatch, useAsync, useSessionState} from './pc/ui.jsx';
import {amountWan, can, dateLabel, identity, open, relativeDay, session, todayKey} from './pc/data.js';
import {composePlaybook, composeProposal, loadCatalog, loadPlaybook, playbookModel, searchCustomers} from './pc/knowledge-data.js';
import KnowledgeAdvice from './KnowledgeAdvice.jsx';
import './pc/knowledge.css';

// 知识库：方案与定价（当前版本可查）、客户档案 Playbook（按客户自动整理）、方案生成（按客户 + 方案 + 价格表出草稿）。
const TABS = [{value: 'catalog', label: '方案与定价'}, {value: 'playbook', label: '客户档案 Playbook'}, {value: 'proposal', label: '方案生成'}];
const CATEGORY_TONE = {solution: 'primary', pricing: 'warning', case: 'teal', template: 'accent'};

function CatalogNotice({catalog}) {
  if (catalog.sample) return <Alert type="info" showIcon title="演示工作区的示例内容" description="以下方案、价格与案例均为虚构示例，用于展示知识库的使用方式。正式工作区请以企业管理员维护并发布的资料为准。" />;
  if (catalog.empty) return <Alert type="warning" showIcon title="方案与定价目录尚未配置" description="当前工作区还没有可查询的方案和价格资料，请联系企业管理员维护后再试。" />;
  return null;
}

function CatalogDetail({item, catalog, onClose, onPropose}) {
  const history = item ? catalog.items.filter(other => other.id !== item.id && other.title === item.title) : [];
  return <Drawer open={Boolean(item)} onClose={onClose} size={620} rootClassName="department-ui pc-kb-drawer" title={item ? `${item.title} ${item.version || ''}` : ''}
    extra={item && item.category === 'solution' && item.status === 'current' && <Button type="primary" size="small" icon={<NavIcon name="spark" />} onClick={() => onPropose(item)}>用它生成方案</Button>}>
    {item && <div className="pc-kb-detail">
      <div className="pc-kb-detail-meta">
        <Tag2 tone={CATEGORY_TONE[item.category]}>{catalog.categories.find(c => c.key === item.category)?.label || item.category}</Tag2>
        {item.status === 'current' ? <Tag2 tone="success">当前版本</Tag2> : <Tag2>历史版本</Tag2>}
        {item.product_line && <Tag2>{item.product_line}</Tag2>}
        <span className="pc-muted">更新于 {item.updated_at || '—'} · {item.owner || '—'}</span>
      </div>
      <p className="pc-kb-summary">{item.summary}</p>
      {item.effective_from && <p className="pc-muted">有效期：{item.effective_from} 至 {item.effective_to || '另行通知'}</p>}
      {item.price_items?.length > 0 && <section><h3>价格</h3><Table size="small" rowKey="name" pagination={false} dataSource={item.price_items}
        columns={[{title: '项目', dataIndex: 'name'}, {title: '计费单位', dataIndex: 'unit'}, {title: '建议价', dataIndex: 'price', align: 'right'}, {title: '说明', dataIndex: 'note'}]} />
        {item.approval && <p className="pc-kb-approval"><NavIcon name="flag" />{item.approval}</p>}</section>}
      {item.highlights?.length > 0 && <section><h3>方案要点</h3><ul>{item.highlights.map(h => <li key={h}>{h}</li>)}</ul></section>}
      {item.scenarios?.length > 0 && <section><h3>适用场景</h3><div className="pc-kb-chips">{item.scenarios.map(h => <Tag2 key={h}>{h}</Tag2>)}</div></section>}
      {item.steps?.length > 0 && <section><h3>实施路径</h3><ol>{item.steps.map(h => <li key={h}>{h}</li>)}</ol></section>}
      {item.deliverables?.length > 0 && <section><h3>交付物</h3><p>{item.deliverables.join('、')}</p></section>}
      {item.files?.length > 0 && <section><h3>附件</h3><ul className="pc-kb-files">{item.files.map(file => <li key={file.name}><NavIcon name="file" />{file.url ? <a href={file.url} target="_blank" rel="noopener noreferrer">{file.name}</a> : <span>{file.name}</span>}</li>)}</ul></section>}
      {history.length > 0 && <section><h3>其它版本</h3><ul>{history.map(h => <li key={h.id}>{h.version} · {h.status === 'current' ? '当前版本' : '历史版本'} · 更新于 {h.updated_at}</li>)}</ul></section>}
    </div>}
  </Drawer>;
}

function CatalogTab({catalog, onPropose}) {
  const [category, setCategory] = useSessionState('kb-category', 'all');
  const [line, setLine] = useState('all');
  const [query, setQuery] = useState('');
  const [history, setHistory] = useState(false);
  const [selected, setSelected] = useState(null);
  const pagination = useLocalTablePagination('knowledge', JSON.stringify([category,line,query,history]));
  const lines = [...new Set(catalog.items.map(item => item.product_line).filter(Boolean))];
  const items = catalog.items.filter(item => (history || item.status === 'current') && (category === 'all' || item.category === category) && (line === 'all' || item.product_line === line) &&
    (!query.trim() || [item.title, item.summary, item.product_line, ...(item.tags || [])].some(value => fuzzyMatch(value, query))))
    .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
  const counts = Object.fromEntries(catalog.categories.map(c => [c.key, catalog.items.filter(item => item.category === c.key && item.status === 'current').length]));
  return <>
    <CatalogNotice catalog={catalog} />
    <div className="pc-kb-catalog">
      <aside className="pc-kb-filter" aria-label="分类">
        <button type="button" className={category === 'all' ? 'is-active' : ''} onClick={() => setCategory('all')}>全部<span>{catalog.items.filter(i => i.status === 'current').length}</span></button>
        {catalog.categories.map(c => <button type="button" key={c.key} className={category === c.key ? 'is-active' : ''} onClick={() => setCategory(c.key)}>{c.label}<span>{counts[c.key] || 0}</span></button>)}
        {lines.length > 0 && <><h4>产品线</h4>{['all', ...lines].map(value => <button type="button" key={value} className={line === value ? 'is-active' : ''} onClick={() => setLine(value)}>{value === 'all' ? '全部产品线' : value}</button>)}</>}
        <p className="pc-muted">目录版本：{catalog.version || '—'}<br />更新：{catalog.updated_at || '—'}</p>
      </aside>
      <div className="pc-kb-list">
        <div className="pc-kb-toolbar">
          <Input allowClear prefix={<NavIcon name="search" />} placeholder="搜索方案、价格、案例（支持拼音首字母）" value={query} onChange={event => setQuery(event.target.value)} aria-label="搜索知识库" />
          <Segmented size="small" value={history ? 'all' : 'current'} onChange={value => setHistory(value === 'all')} options={[{value: 'current', label: '只看当前版本'}, {value: 'all', label: '含历史版本'}]} />
        </div>
        {items.length ? <div className="pc-kb-cards">{items.slice((pagination.current - 1) * pagination.pageSize, pagination.current * pagination.pageSize).map(item => <button type="button" key={item.id} className={`pc-kb-card${item.status !== 'current' ? ' is-archived' : ''}`} onClick={() => setSelected(item)}>
          <div className="pc-kb-card-head"><Tag2 tone={CATEGORY_TONE[item.category]}>{catalog.categories.find(c => c.key === item.category)?.label || item.category}</Tag2>{item.status === 'current' ? <Tag2 tone="success">当前</Tag2> : <Tag2>历史</Tag2>}<span className="pc-muted">{item.version}</span></div>
          <b>{item.title}</b>
          <p className="pc-clamp-2">{item.summary}</p>
          {item.price_items?.length > 0 && <p className="pc-kb-price">{item.price_items.slice(0, 2).map(p => `${p.name} ${p.price}`).join(' · ')}</p>}
          <span className="pc-kb-card-foot">{item.product_line || '通用'} · 更新于 {item.updated_at}{item.owner ? ` · ${item.owner}` : ''}</span>
        </button>)}</div> : <Empty2 title={catalog.empty ? '目录还是空的' : '没有匹配的条目'} description={catalog.empty ? '请联系企业管理员维护方案与价格资料后再试' : '换个关键词，或切到「含历史版本」'} />}
        {items.length > 0 && <div className="pc-list-pagination"><Pagination {...pagination} total={items.length} /></div>}
      </div>
    </div>
    <CatalogDetail item={selected} catalog={catalog} onClose={() => setSelected(null)} onPropose={item => { setSelected(null); onPropose(item); }} />
  </>;
}

// Query the authorized readable directory for every keyword, including Latin
// names. Filtering the first 50 suggestions locally can hide valid customers.
export function CustomerPicker({value, onChange}) {
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const owner = identity();
  useEffect(() => { const id = setTimeout(() => setDebounced(query), 300); return () => clearTimeout(id); }, [query]);
  const result = useAsync(() => searchCustomers(debounced), [debounced, owner]);
  const pending = query.trim() !== debounced.trim() || result.loading;
  const options = pending || result.error ? [] : (result.value || []).map(row => ({value: row.id, label: row.name, row}));
  const empty = pending ? '正在查询…' : result.error
    ? <div className="pc-kb-customer-error" role="alert"><span>{result.error}</span><Button type="link" size="small" onClick={result.reload}>重新搜索</Button></div>
    : '没有匹配的可读客户，请换个关键词';
  return <Select className="pc-kb-customer" showSearch value={value?.id} placeholder="选择客户（客户全称或关键词）" filterOption={false} onSearch={setQuery} loading={pending}
    options={options} notFoundContent={empty} popupMatchSelectWidth={420}
    optionRender={option => <span className="pc-kb-option"><b>{option.data.label}</b><small>{[option.data.row.industry, option.data.row.owner].filter(Boolean).join(' · ')}</small></span>}
    onChange={id => {
      if (pending || result.error || owner !== identity()) return;
      const option = options.find(row => row.value === id);
      if (option) onChange(option.row);
    }} aria-label="选择客户" />;
}

function PlaybookTab({catalog, customer, setCustomer}) {
  const {message} = App.useApp();
  const data = useAsync(() => customer ? loadPlaybook(customer.id) : null, [customer?.id, identity()]);
  const model = useMemo(() => data.value ? playbookModel(data.value, catalog) : null, [data.value, catalog]);
  const markdown = useMemo(() => model ? composePlaybook(model, todayKey()) : '', [model]);
  return <>
    <div className="pc-kb-pick"><CustomerPicker value={customer} onChange={setCustomer} />
      {model && <><Button onClick={() => open.customer(model.id)} icon={<NavIcon name="clock" />}>客户档案与时间轴</Button>
        <Copyable text={markdown} label="复制 Playbook" onCopied={ok => ok ? message.success('已复制') : message.error('浏览器未允许复制，请改用下载')} />
        <Button icon={<NavIcon name="download" />} onClick={() => downloadText(`${model.name}_客户档案Playbook.md`, markdown, 'text/markdown;charset=utf-8')}>下载</Button></>}
    </div>
    {!customer ? <Empty2 title="选择一家客户，自动整理客户档案 Playbook" description="汇总客户概况、关键人、商机全景、沟通时间线、下一步、风险与可参考的方案" />
      : <LoadState loading={data.loading} error={data.error} onRetry={data.reload} rows={8}>{model && <div className="pc-kb-playbook">
        <section className="pc-kb-pb-main">
          <Panel title="客户概况" extra={<span className="pc-muted">负责人 {model.owner || '未分配'}{model.team ? ` · ${model.team}` : ''}</span>}>
            <dl className="pc-kb-facts">
              {[['行业', model.industry], ['等级', model.level], ['来源', model.source], ['合作伙伴', model.partner], ['客户潜力', model.potential ?? '未评估'], ['关系深度', model.relationship ?? '未评估'], ['所属象限', model.quadrant], ['在推商机', `${model.open.length} 个 · ${amountWan(model.openAmount)} 万元`]].filter(([, v]) => v !== '' && v !== null).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
            </dl>
          </Panel>
          <Panel title="沟通时间线要点" note={`共 ${model.visits.length} 次跟进${model.partial ? '（已读取最近 150 条）' : ''}`} extra={<Button type="link" size="small" onClick={() => open.customer(model.id)}>完整时间轴 →</Button>}>
            {model.visits.length ? <ol className="pc-kb-timeline">{model.visits.slice(0, 6).map(v => <li key={v.id}><span className="pc-kb-tl-date">{dateLabel(v.date)}</span><div><b>{v.recorderName}{v.mode ? ` · ${v.mode}` : ''}{v.opportunityName ? ` · ${v.opportunityName}` : ''}</b><p>{v.content || '沟通内容未填写'}</p>{v.nextAction && <p className="pc-fu-next"><span>下一步</span>{v.nextAction}</p>}</div></li>)}</ol> : <p className="pc-muted">还没有跟进记录</p>}
          </Panel>
          <Panel title="商机全景" bodyClassName="is-flush">
            <Table size="small" rowKey="id" pagination={false} dataSource={model.opportunities} locale={{emptyText: '暂无商机'}}
              columns={[{title: '商机', dataIndex: 'name', render: (value, row) => <button type="button" className="pc-link" onClick={() => open.opportunity(model.id, row.id)}>{value}</button>},
                {title: '阶段', dataIndex: 'stage', width: 120}, {title: '金额（万元）', dataIndex: 'amount', width: 110, align: 'right', render: v => amountWan(v)},
                {title: '预计关单', dataIndex: 'close', width: 110}, {title: '负责人', dataIndex: 'owner', width: 100}]} />
          </Panel>
        </section>
        <aside className="pc-kb-pb-side">
          <Panel title="组织与关键人">{model.contacts.length ? <ul className="pc-kb-people">{model.contacts.map(p => <li key={p.name + p.title}><Avatar size={28}>{p.name.slice(0, 1)}</Avatar><span><b>{p.name}{p.primary && <Tag2 tone="primary">首要</Tag2>}</b><small>{[p.title, p.role].filter(Boolean).join(' · ')}</small></span></li>)}</ul> : <p className="pc-muted">还没有登记联系人</p>}</Panel>
          <Panel title="下一步与待办">{model.nextAction || model.tasks.length ? <ul className="pc-wr-list">{model.nextAction && <li><b>最近约定</b><span>{model.nextAction}</span></li>}{model.tasks.slice(0, 6).map(t => <li key={t.id}><button type="button" className="pc-link" onClick={() => open.task(t.id)}>{t.title}</button><span>{[t.due && `截止 ${t.due}`, t.owner].filter(Boolean).join(' · ')}</span></li>)}</ul> : <p className="pc-muted">暂无待办</p>}</Panel>
          <Panel title="风险提示">{model.risks.length ? <ul className="pc-wr-list">{model.risks.map(r => <li key={r.id}><button type="button" className="pc-link" onClick={() => open.risk(r.id)}>{r.title}</button><span>{r.opportunityName}</span></li>)}</ul> : <p className="pc-muted">当前没有待处理的风险</p>}</Panel>
          <Panel title="可参考的方案与价格">{model.recommended.length ? <ul className="pc-wr-list">{model.recommended.map(item => <li key={item.id}><b>{item.title} {item.version}</b><span>{item.category === 'pricing' ? '价格表' : '方案'} · 更新于 {item.updated_at}</span></li>)}</ul> : <p className="pc-muted">方案库中暂无与该客户产品线匹配的条目</p>}</Panel>
          <KnowledgeAdvice customerId={customer.id} customerOverview={data.value?.overview} />
        </aside>
      </div>}</LoadState>}
  </>;
}

function ProposalTab({catalog, customer, setCustomer, solutionId, setSolutionId}) {
  const {message} = App.useApp();
  const data = useAsync(() => customer ? loadPlaybook(customer.id) : null, [customer?.id, identity()]);
  const model = useMemo(() => data.value ? playbookModel(data.value, catalog) : null, [data.value, catalog]);
  const [opportunityId, setOpportunityId] = useState(null);
  const [pricingId, setPricingId] = useState(null);
  const [draft, setDraft] = useState('');
  const solutions = catalog.items.filter(item => item.category === 'solution' && item.status === 'current');
  const pricings = catalog.items.filter(item => item.category === 'pricing' && item.status === 'current');
  const opportunity = model?.opportunities.find(o => o.id === opportunityId) || null;
  const solution = solutions.find(item => item.id === solutionId) || null;
  const pricing = pricings.find(item => item.id === pricingId) || null;
  useEffect(() => { setOpportunityId(null); setSolutionId(null); setPricingId(null); setDraft(''); }, [customer?.id]);
  useEffect(() => {
    // 选了商机后默认带出同产品线的方案与价格表，仍可手动改
    if (!opportunity?.productLine) return;
    const s = solutions.find(item => item.product_line === opportunity.productLine); if (s && !solutionId) setSolutionId(s.id);
    const p = pricings.find(item => item.product_line === opportunity.productLine); if (p && !pricingId) setPricingId(p.id);
  }, [opportunity?.id]);
  useEffect(() => { if (solution?.product_line && !pricingId) { const p = pricings.find(item => item.product_line === solution.product_line); if (p) setPricingId(p.id); } }, [solution?.id]);
  const generate = () => {
    if (!model) return;
    if (!solution) {
      message.warning('请先选择方案条目');
      return;
    }
    setDraft(composeProposal({model, opportunity, solution, pricing, generatedOn: todayKey()}));
    message.success('方案草稿已生成，请核对后再使用');
  };
  return <div className="pc-kb-proposal">
    <aside className="pc-kb-form">
      <Panel title="生成条件">
        <label>客户<CustomerPicker value={customer} onChange={setCustomer} /></label>
        <label>商机<Select value={opportunityId} onChange={setOpportunityId} allowClear placeholder={model ? '选择商机（可不选）' : '先选客户'} disabled={!model}
          options={(model?.opportunities || []).map(o => ({value: o.id, label: `${o.name} · ${o.stage || '阶段未填'}`}))} /></label>
        <label>方案条目<Select value={solutionId} onChange={setSolutionId} allowClear placeholder="选择方案" options={solutions.map(item => ({value: item.id, label: `${item.title} ${item.version || ''}`}))} /></label>
        <label>价格表<Select value={pricingId} onChange={setPricingId} allowClear placeholder="选择价格表（可不选）" options={pricings.map(item => ({value: item.id, label: `${item.title} ${item.version || ''}`}))} /></label>
        <Button type="primary" block icon={<NavIcon name="spark" />} disabled={!model || data.loading || !solution} onClick={generate}>生成方案草稿</Button>
        <p className="pc-muted">草稿按客户档案、跟进记录与方案库拼装，不调用模型改写；对客发送前请核对事实、口径与报价，报价以审批为准。</p>
      </Panel>
      {!solutions.length && <Alert type="warning" showIcon title="方案库为空" description="先在「方案与定价」维护方案条目，才能按方案生成草稿。" />}
    </aside>
    <Panel className="pc-kb-draft" title="方案草稿" note={draft ? '可直接编辑' : ''} extra={draft && <>
      <Copyable text={draft} label="复制" onCopied={ok => ok ? message.success('已复制') : message.error('浏览器未允许复制，请改用下载')} />
      <Button size="small" icon={<NavIcon name="download" />} onClick={() => downloadText(`${model?.name || '客户'}_方案草稿.md`, draft, 'text/markdown;charset=utf-8')}>下载 Markdown</Button></>}>
      {data.loading && customer ? <LoadState loading rows={8} /> : draft
        ? <Input.TextArea value={draft} onChange={event => setDraft(event.target.value)} autoSize={{minRows: 22, maxRows: 40}} aria-label="方案草稿" className="pc-kb-textarea" />
        : <Empty2 title="选择客户与方案后生成草稿" description="客户背景与需求来自跟进记录，方案要点来自方案库，报价来自价格表" />}
    </Panel>
  </div>;
}

export default function Knowledge({page}) {
  const options = page?.options || {};
  const tab = TABS.some(t => t.value === options.tab) ? options.tab : 'catalog';
  const catalog = useAsync(() => loadCatalog(), [identity()]);
  const [customer, setCustomer] = useSessionState('kb-customer', null);
  // 从方案详情「用它生成方案」跳过来时，方案条目经地址参数带过来（换页后组件会重建）
  const [solutionId, setSolutionId] = useState(options.solution || null);
  const go = (value, extra = '') => open.replace('pages/knowledge/index?tab=' + value + extra);
  return <div className="pc-page pc-knowledge">
    <PageHead title="知识库" subtitle={catalog.value ? `方案与定价目录 ${catalog.value.version || '未配置'} · 客户档案按系统记录自动整理` : '方案与定价、客户档案 Playbook、方案生成'}
      extra={<Segmented value={tab} onChange={go} options={TABS} />} />
    <LoadState loading={catalog.loading} error={catalog.error} onRetry={catalog.reload} rows={6}>
      {catalog.value && tab === 'catalog' && <CatalogTab catalog={catalog.value} onPropose={item => go('proposal', '&solution=' + encodeURIComponent(item.id))} />}
      {catalog.value && tab === 'playbook' && <PlaybookTab catalog={catalog.value} customer={customer} setCustomer={setCustomer} />}
      {catalog.value && tab === 'proposal' && <ProposalTab catalog={catalog.value} customer={customer} setCustomer={setCustomer} solutionId={solutionId} setSolutionId={setSolutionId} />}
    </LoadState>
  </div>;
}
