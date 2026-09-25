from pathlib import Path
from html.parser import HTMLParser
import base64, copy, hashlib, json, mimetypes, re, shutil, tempfile
from web_detail_layout import adapt as web_detail_layout
from web_customer_overview_layout import adapt as web_customer_overview_layout
from web_workbench_layout import adapt as web_workbench_layout
from web_workflow_layout import adapt as web_workflow_layout
from web_claim_layout import adapt as web_claim_layout
from web_core_layout import adapt as web_core_layout
from web_extended_forms_layout import adapt as web_extended_forms_layout
from web_extended_details_layout import adapt as web_extended_details_layout
from web_insights_layout import adapt as web_insights_layout
from web_collection_columns_layout import adapt as web_collection_columns_layout
from web_customer_detail_columns_layout import adapt as web_customer_detail_columns_layout
from web_opportunity_columns_layout import adapt as web_opportunity_columns_layout
from web_record_columns_layout import adapt as web_record_columns_layout
from web_visit_advice_layout import adapt as web_visit_advice_layout
from web_review_lists_layout import adapt as web_review_lists_layout
from web_review_forms_layout import adapt as web_review_forms_layout
from web_review_pages_layout import adapt as web_review_pages_layout

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'source' / 'miniprogram'
DIST = ROOT / 'dist'
WEB_BRAND = '商汤销售小浣熊'

def brand_template(text):
    # Only static first-party UI labels; customer and API content is never rewritten.
    return (text.replace('销售智助 · MVP Preview 0.1', WEB_BRAND + ' · Web 工作空间')
                .replace('>销售智助<', '>' + WEB_BRAND + '<')
                .replace('>SALES INTELLIGENCE SYSTEM<', '>Raccoon SalesBuddy<'))


def web_api_module(text):
    # Browser-only lifecycle adaptation. Keep the imported Mini Program untouched;
    # reject the old Agent poll before it can pick up a newly signed-in account.
    anchor = 'function waitForRun(runId, options = {}) {\n'
    poll = '    const poll = () => {\n      getRun(runId)'
    if text.count(anchor) != 1 or text.count(poll) != 1:
        raise ValueError('Agent polling source changed; review the Web session adaptation.')
    text = text.replace(anchor, anchor + '  const session = currentSession();\n', 1)
    text = text.replace(poll, '    const poll = () => {\n'
                        '      try { assertCurrentSession(session); } catch (error) { reject(error); return; }\n'
                        '      getRun(runId)', 1)
    advice = "module.exports.queryBusinessAdvice = async (subjectKind,subjectId,section='overview',retry=false) => {\n"
    identity_check = "    if(identity()!==owner)throw Error('登录身份已变化');"
    if text.count(advice) != 1 or text.count(identity_check) != 1:
        raise ValueError('Advice polling source changed; review the Web session adaptation.')
    text = text.replace(advice, advice + '  const session = currentSession();\n', 1)
    text = text.replace(identity_check, '    assertCurrentSession(session);\n' + identity_check, 1)
    return text + '''\n// Customer business Web only; uses the existing shared login and refresh path.
module.exports.weeklyRequest = (path='', options={}) => request({...options, path:'/web/weekly-reports'+path});
'''



class WXML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {'t': 'root', 'a': {}, 'c': []}
        self.stack = [self.root]
    def handle_starttag(self, tag, attrs):
        node = {'t': tag, 'a': {k: v if v is not None else '' for k, v in attrs}, 'c': []}
        self.stack[-1]['c'].append(node)
        if tag not in ('input', 'image', 'img', 'br', 'hr'):
            self.stack.append(node)
    def handle_startendtag(self, tag, attrs):
        self.stack[-1]['c'].append({'t': tag, 'a': {k: v if v is not None else '' for k, v in attrs}, 'c': []})
    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]['t'] == tag:
                self.stack = self.stack[:i]
                break
    def handle_data(self, data):
        self.stack[-1]['c'].append(data)

def template_record(path):
    """Mini Program imports expose named templates, not rendered page content."""
    parser = WXML(); parser.feed(brand_template(path.read_text()))
    templates = {}
    def declarations(nodes):
        for node in nodes:
            if isinstance(node, dict):
                if node['t'] == 'template' and 'name' in node['a']:
                    templates[node['a']['name']] = node['c']
                elif node['t'] != 'import':
                    declarations(node['c'])
    for node in parser.root['c']:
        if isinstance(node, dict) and node['t'] == 'import':
            imported = (path.parent / node['a']['src']).resolve()
            if SOURCE.resolve() not in imported.parents or not imported.is_file():
                raise ValueError('Missing or unsafe WXML import: ' + str(imported))
            imported_parser = WXML(); imported_parser.feed(brand_template(imported.read_text()))
            declarations(imported_parser.root['c'])
    declarations(parser.root['c'])
    return parser.root['c'], templates

def web_visit_review_tree(tree):
    # Native inputField writes values[key] without rebuilding core/firstVisitFields
    # while typing. Bind Web controlled inputs to that authoritative draft, so a
    # render for the review gate cannot write a stale item.value over user input.
    bindings = 0
    def fragment(text):
        parser = WXML(); parser.feed(text)
        return [n for n in parser.root['c'] if not isinstance(n, str) or n.strip()]
    def adapt(items):
        nonlocal bindings
        for node in items:
            if not isinstance(node, dict):
                continue
            attrs = node['a']
            if node['t'] in ('input', 'textarea') and attrs.get('bindinput') == 'inputField' and attrs.get('value') == '{{item.value}}':
                attrs['value'] = '{{values[item.key]}}'
                bindings += 1
            if 'analyzing-card' in attrs.get('class', '').split():
                attrs['role'] = 'status'
                attrs['aria-live'] = 'polite'
                node['c'] = fragment('''
                  <view class="analysis-orbit"><view class="analysis-core">AI</view></view>
                  <view class="web-analysis-copy"><view class="section-title">正在检查拜访内容</view><view class="analysis-caption">{{analysisPhrase}}</view><view class="muted">质检期间暂不可编辑；等待超时会恢复编辑，保留已有草稿。</view></view>''')
            if 'structured-entry' in attrs.get('class', '').split():
                fields = [n for n in node['c'] if isinstance(n, dict)]
                core = next(i for i, n in enumerate(fields) if 'core-field' in n['a'].get('class', '').split())
                first = next(i for i, n in enumerate(fields) if 'first-visit-section' in n['a'].get('class', '').split())
                def section(cls, title, children):
                    return {'t': 'section', 'a': {'class': cls}, 'c': fragment('<h2 class="web-form-heading">' + title + '</h2>') + children}
                node['c'] = [section('web-visit-basics', '基本信息', fields[1:core]),
                             section('web-visit-narrative', '拜访内容', fields[core:first]),
                             fields[first], section('web-visit-metadata', '记录信息', fields[first+1:])]
            adapt(node['c'])
    adapt(tree)
    if bindings != 3:
        raise ValueError('Visit input structure changed; review authoritative draft bindings.')
    def add_readonly(items):
        for i, node in enumerate(items):
            if not isinstance(node, dict):
                continue
            if 'analyzing-card' in node['a'].get('class', '').split():
                items[i+1:i+1] = fragment('''
                  <section wx:if="{{flowStep === 'analyzing'}}" class="card web-analysis-preview" aria-label="本次送检内容，只读">
                    <view class="web-analysis-preview-head"><h2>本次送检内容</h2><text>只读</text></view>
                    <view class="web-analysis-context"><strong>{{customerName}}</strong><text>{{visitDateLabel}}</text><text>{{values.contact_name}}</text></view>
                    <view wx:for="{{core}}" wx:key="key" class="web-analysis-field"><h3>{{item.label}}</h3><p>{{values[item.key] || '未填写'}}</p></view>
                    <details wx:if="{{isFirstVisit}}" class="web-analysis-extra"><summary>首次拜访补充信息</summary><view wx:for="{{firstVisitFields}}" wx:key="key" class="web-analysis-field"><h3>{{item.label}}</h3><p>{{values[item.key] || '未填写'}}</p></view></details>
                  </section>''')
                return True
            if add_readonly(node['c']):
                return True
        return False
    if not add_readonly(tree):
        raise ValueError('Visit analysis structure changed; review read-only context.')
    return tree

def web_home_tree(tree):
    # Present the shared Page data and handlers in a desktop work sequence.
    # Do not infer task queues from notification/card caches or sort the event stream.
    def nodes(items):
        for item in items:
            if isinstance(item, dict):
                yield item
                yield from nodes(item['c'])
    def one_class(name):
        matches = [n for n in nodes(tree) if name in n['a'].get('class', '').split()]
        if len(matches) != 1:
            raise ValueError('Home structure changed; review Web layout: ' + name)
        return matches[0]
    def fragment(text):
        parser = WXML(); parser.feed(text)
        return [n for n in parser.root['c'] if not isinstance(n, str) or n.strip()]
    assistant = one_class('assistant-page')
    assistant['a']['class'] += ' web-home-workspace'
    overview = one_class('overview-fixed')
    brief = one_class('brief-card')
    overview['c'][overview['c'].index(brief)] = fragment('''
      <view class="web-home-summary" aria-label="任务概况">
        <view class="web-home-section-heading"><text>先处理待办</text><label>按当前账号授权范围统计</label></view>
        <view class="web-home-metrics">
          <button wx:for="{{webOverviewMetrics}}" wx:key="key" class="brief-metric web-home-metric" data-key="{{item.key}}" bindtap="openOverviewTasks">
            <view class="web-home-metric-top"><text class="metric-label">{{item.label}}</text><text class="web-home-enter">↗</text></view>
            <text class="metric-num">{{item.value}}</text><text class="web-home-metric-hint">{{item.hint}}</text>
          </button>
        </view>
      </view>''')[0]
    composer = one_class('composer-wrap')
    quick = one_class('quick-action-bar')
    composer['c'].remove(quick)
    composer['a']['wx:if'] = '{{homeChatBIEnabled || managementTaskMode || visitRecordingMode}}'
    quick['a']['class'] = 'web-home-actions'
    for n in nodes(quick['c']):
        if 'quick-action-button' in n['a'].get('class', '').split():
            n['t'] = 'button'
            n['c'] = fragment('''<text class="web-home-action-icon">{{item.kind === 'customer' ? '＋' : item.kind === 'visit' ? '✎' : '✓'}}</text><text class="quick-action-label">{{item.label}}</text>''')
    shortcuts = {'t':'view','a':{'class':'web-home-shortcuts','wx:if':'{{quickActions.length}}'},'c':fragment('<view class="web-home-section-heading"><text>常用操作</text><label>选择要开始的工作</label></view>') + [quick]}
    assistant['c'].insert(assistant['c'].index(overview) + 1, shortcuts)
    chat = one_class('chat-scroll')
    assistant['c'].insert(assistant['c'].index(chat), fragment('''<view class="web-home-feed-heading"><view><text>业务动态</text><label>客户进展、任务通知与拜访回执</label></view><text class="web-activity-count">{{webActivityFiltered ? '匹配 ' + webActivityCount + ' 条 / 已加载 ' + webActivityTotal + ' 条' : '当前已加载 ' + webActivityTotal + ' 条'}} · {{webHomeOrderLabel}}</text></view>''')[0])
    assistant['c'].insert(assistant['c'].index(chat), fragment('''<view class="web-activity-tools">
      <view class="web-activity-filters" role="group" aria-label="动态类型"><button wx:for="{{webActivityGroups}}" wx:key="key" class="web-activity-filter {{item.selected ? 'active' : ''}}" data-key="{{item.key}}" title="{{item.hint}}" aria-pressed="{{item.selected}}" bindtap="webFilterActivity">{{item.label}}<span>{{item.count}}</span></button></view>
      <view class="web-activity-search"><input value="{{webActivityQuery}}" bindinput="webSearchActivity" placeholder="搜索客户、任务或动态内容" aria-label="搜索当前已加载动态"/><button wx:if="{{webActivityQuery}}" bindtap="webClearActivity" aria-label="清除搜索和分类">×</button></view>
    </view>''')[0])
    stream = one_class('chat-stream')
    stream['c'].insert(0, fragment('''<view wx:if="{{!webHomeMessages.length && !isThinking && !isProcessing}}" class="web-home-feed-empty"><text>{{webActivityFiltered ? '没有匹配的动态' : '业务动态会显示在这里'}}</text><label>{{webActivityFiltered ? '试试其他关键词，或查看全部动态。' : '任务变化、客户进展和拜访回执可在此查看。'}}</label><button wx:if="{{webActivityFiltered}}" bindtap="webClearActivity">查看全部动态</button></view>''')[0])
    message_loops = [n for n in nodes(tree) if n['a'].get('wx:for') == '{{messages}}']
    if len(message_loops) != 1:
        raise ValueError('Home message loop changed; review Web presentation.')
    message_loops[0]['a']['wx:for'] = '{{webHomeMessages}}'
    message_loops[0]['c'].insert(0, fragment('''<view wx:if="{{webActivityCompact && item.webActivityDay}}" class="web-activity-day">{{item.webActivityDay}}</view>''')[0])
    # This static badge represents an event receipt, not a fresh backend sync guarantee.
    badge = one_class('database-badge')
    badge['c'] = [v.replace('数据已同步','动态记录') if isinstance(v,str) else v for v in badge['c']]
    # Web activity rows disclose the complete original receipt. Keep its existing
    # handlers/datasets and every source field; the summary is read-only UI.
    original_card = one_class('business-card')
    compact = copy.deepcopy(original_card)
    compact['a']['class'] += " web-activity-row category-{{item.webActivity.kind}} {{item.webActivity.health ? 'health-' + item.webActivity.tone : ''}} {{item.webActivity.completed ? 'is-complete' : ''}}"
    compact['a']['wx:if'] = "{{item.kind === 'data-card' && webActivityCompact}}"
    original_card['a']['wx:if'] = "{{item.kind === 'data-card' && !webActivityCompact}}"
    actions = [n for n in compact['c'] if isinstance(n, dict) and 'business-action' in n['a'].get('class', '').split()]
    if len(actions) != 1:
        raise ValueError('Home receipt action changed; review the Web activity row.')
    compact['c'].remove(actions[0])
    actions[0]['a']['aria-label'] = '{{item.card.action.label}}：{{item.webActivity.title}}'
    details = fragment('''<details class="web-activity-detail">
      <summary class="web-activity-summary" title="展开或收起完整动态">
        <span class="web-activity-icon" aria-hidden="true">{{item.webActivity.icon}}</span>
        <span class="web-activity-main">
          <span class="web-activity-title-line"><span class="web-activity-title" title="{{item.webActivity.title}}">{{item.webActivity.title}}</span></span>
          <span class="web-activity-secondary"><span class="web-activity-category">{{item.webActivity.label}}</span><span wx:if="{{item.webActivity.preview}}" class="web-activity-preview" title="{{item.webActivity.preview}}">{{item.webActivity.preview}}</span><span wx:for="{{item.webActivity.metrics}}" wx:for-item="metric" wx:key="label" class="web-activity-metric"><span>{{metric.label}}</span><b>{{metric.value}}</b></span></span>
          <span wx:if="{{item.webActivity.change}}" class="web-activity-change" title="{{item.webActivity.change}}">{{item.webActivity.change}}</span>
        </span>
        <span class="web-activity-status-slot"><span wx:if="{{item.webActivity.status}}" class="web-activity-status {{item.webActivity.health ? 'web-activity-light' : 'web-activity-result'}} signal-{{item.webActivity.tone}}" title="{{item.webActivity.statusHint}}" aria-label="{{item.webActivity.statusHint}}"><span wx:if="{{item.webActivity.health}}" class="web-activity-dot" aria-hidden="true"></span>{{item.webActivity.status}}</span></span>
        <span class="web-activity-time" title="{{item.time}}">{{item.webActivity.time.clock}}</span>
        <span class="web-activity-toggle" aria-hidden="true"><span class="web-activity-chevron">⌄</span></span>
      </summary>
    </details>''')[0]
    details['c'].append({'t': 'view', 'a': {'class': 'web-activity-body'}, 'c': compact['c']})
    compact['c'] = [details] + actions
    parent = next(n for n in nodes(tree) if any(child is original_card for child in n['c']))
    parent['c'].insert(parent['c'].index(original_card), compact)
    # Desktop zones: task overview + shortcuts, followed by a separate event inbox.
    summary = one_class('web-home-summary')
    overview['c'].remove(summary)
    assistant['c'].remove(shortcuts)
    overview['c'].append({'t': 'view', 'a': {'class': 'web-home-focus'}, 'c': [summary, shortcuts]})
    # Keep the role-gated team entry in the overview rail at every breakpoint.
    fde_summary = one_class('fde-team-summary')
    assistant['c'].remove(fde_summary)
    overview['c'].append(fde_summary)
    heading, tools = one_class('web-home-feed-heading'), one_class('web-activity-tools')
    first = assistant['c'].index(heading)
    for n in [heading, tools, chat]:
        assistant['c'].remove(n)
    assistant['c'].insert(first, {'t': 'section', 'a': {'class': 'web-home-feed-panel', 'aria-label': '业务动态'}, 'c': [heading, tools, chat]})
    return tree

def css(path, seen=None):
    if not path.exists():
        return ''
    path = path.resolve()
    seen = set() if seen is None else set(seen)
    if path in seen:
        raise ValueError(f'CSS import cycle: {path}')
    seen.add(path)
    result = re.sub(r'@import\s+["\']([^"\']+)["\'];?', lambda m: css(path.parent / m[1], seen), path.read_text())
    result = re.sub(r'(-?\d*\.?\d+)rpx', r'calc(var(--rpx) * \1)', result)
    result = design_skin(result)
    return re.sub(r'(?<![\w.-])page(?=[\s,{.:#])', '#page-root', result)


# 设计系统皮肤：小程序页面样式进 Web 时统一到部门规范（desgin 仓库 14 章）。
# 字号不低于 12px；不用渐变；常见写死的颜色换成 --ui-* 变量。业务代码不动，只改编译出来的 Web 样式。
SKIN_COLORS = {
    'var(--ui-primary)': ['#1677ff', '#1683ee', '#176fc9', '#176fc5', '#225e91', '#2875b7', '#3577bc', '#2b6088', '#1356ac'],
    'var(--ui-ink)': ['#182233', '#24394f', '#28435b', '#30475f', '#173551', '#2e2928', '#14243d'],
    'var(--ui-muted)': ['#8a98aa', '#8795a7', '#8290a2', '#718197', '#8293a7', '#7b91a5', '#718097', '#708096', '#8298aa', '#698399', '#65758b', '#63758e'],
    'var(--ui-line)': ['#e5edf5', '#dfe9f2', '#dce8f4', '#e1ebf4', '#dce9f4', '#e3ebff', '#dfe7f3', '#cfe3f4'],
    'var(--ui-selected)': ['#edf6ff', '#eaf5ff', '#eef6fd', '#e7f2fd', '#e6f3ff', '#edf7ff', '#e6f1fd', '#f2f6ff', '#f4faff'],
    'var(--ui-surface-subtle)': ['#edf2f7', '#edf3f8', '#f6f9fc', '#f8fbfe', '#f7fbff', '#f6f9fd', '#f9fcff', '#f8fbff', '#f6faff', '#eef5fc', '#eef4fb', '#eef3f8', '#edf5fc', '#e6edf4', '#fbfdff', '#f7fafc', '#eaf1f7', '#f2f5f9', '#f0f5fb', '#f8f2ee'],
}

def gradient_to_flat(match):
    # 渐变改平色：取第一个色标。很浅的（近白、透明）换成表面色，其余保留原色，随后再映射到变量。
    body = match[0][match[0].index('(') + 1:-1]
    stops = re.findall(r'(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))', body)
    for stop in stops:
        if stop.startswith('#'):
            h = stop[1:]
            if len(h) in (3, 4): h = ''.join(c * 2 for c in h[:3])
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            if (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.88: return 'var(--ui-surface)'
            return stop
        nums = re.findall(r'[\d.]+', stop)
        if len(nums) >= 3:
            r, g, b = (float(n) for n in nums[:3]); a = float(nums[3]) if len(nums) > 3 else 1
            if a < 0.35 or (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.88: return 'var(--ui-surface)'
            return stop
    return 'var(--ui-surface)'

def design_skin(text):
    text = re.sub(r'(?:linear|radial)-gradient\((?:[^()]|\([^()]*\))*\)', gradient_to_flat, text)
    text = re.sub(r'font-size\s*:\s*calc\(var\(--rpx\) \* (\d+(?:\.\d+)?)\)',
                  lambda m: 'font-size:var(--ui-text-small)' if float(m[1]) <= 24 else m[0], text)
    for token, colors in SKIN_COLORS.items():
        for color in colors:
            text = re.sub(re.escape(color) + r'(?![0-9a-fA-F])', token, text, flags=re.IGNORECASE)
    return text

def component_css(text, module):
    # Native components isolate their classes. A combined Web stylesheet must not
    # let two independent pickers' .sheet-* classes overwrite each other.
    qualifier = ':where([data-wx-style="' + module + '"])'
    return re.sub(r'([^{}]+)\{', lambda m: re.sub(r'\.([A-Za-z_][\w-]*)',
        lambda part: part[0] + qualifier, m[1]) + '{', text)

def build():
    manifest = json.loads((ROOT / 'source' / 'manifest.json').read_text())
    allowed = set(manifest['files']) | {'config.js'}
    actual = {p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob('*') if p.is_file()}
    if actual != allowed:
        raise ValueError(f'Source inventory mismatch: {sorted(actual ^ allowed)}')
    expected_config = 'module.exports = ' + json.dumps({'API_BASE_URL': '/api/v1', **manifest['feature_flags']}) + ';\n'
    if (SOURCE / 'config.js').read_text() != expected_config:
        raise ValueError('Browser config must use the fixed same-origin /api/v1 path.')
    for name, checksum in manifest['files'].items():
        if hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() != checksum:
            raise ValueError(f'Shared business source changed: {name}; update through the import script.')
    data = {'modules': {}, 'pages': {}, 'components': {}, 'images': {}, 'config': json.loads((SOURCE / 'app.json').read_text()), 'css': css(SOURCE / 'app.wxss'), 'revision': manifest['frontend_revision']}
    if data['config'].get('window', {}).get('navigationBarTitleText') == '销售智助':
        data['config']['window']['navigationBarTitleText'] = WEB_BRAND
    for path in sorted(SOURCE.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(SOURCE).as_posix()
        if path.suffix == '.js':
            module = path.read_text()
            if rel == 'utils/apiClient.js':
                module = web_api_module(module)
            if rel == 'pages/index/index.js':
                module = module.replace('我是销售智助，今天可以帮你查看客户、商机和待办。', '我是商汤销售小浣熊，今天可以帮你查看客户、商机和待办。')
            data['modules'][rel[:-3]] = module
        elif path.suffix == '.wxml' and not rel.startswith('templates/'):
            tree, templates = template_record(path)
            def fragment(text):
                parser = WXML(); parser.feed(text)
                return [n for n in parser.root['c'] if not isinstance(n, str) or n.strip()]
            if rel == 'pages/index/index.wxml':
                tree = web_home_tree(tree)
            if rel == 'pages/tasks/index.wxml':
                parser = WXML(); parser.feed((ROOT / 'task-workspace.wxml').read_text())
                tree = parser.root['c']
            if rel == 'pages/visit-confirm/index.wxml':
                tree = web_visit_review_tree(tree)
            if rel in ('pages/customers/index.wxml', 'pages/customer-detail/index.wxml', 'pages/customer-assets/index.wxml', 'pages/visit-entry/index.wxml', 'pages/workbench/index.wxml', 'pages/opportunity-create/index.wxml', 'pages/management-task-create/index.wxml', 'pages/customer-claim/index.wxml'):
                if rel == 'pages/workbench/index.wxml':
                    tree = web_workbench_layout(tree, fragment)
                elif rel == 'pages/customer-claim/index.wxml':
                    tree = web_claim_layout(tree, rel, fragment)
                elif rel in ('pages/opportunity-create/index.wxml', 'pages/management-task-create/index.wxml'):
                    tree = web_workflow_layout(tree, rel, fragment)
                else:
                    tree = web_detail_layout(tree, rel, fragment)
                    tree = web_customer_overview_layout(tree, rel, fragment)
            tree = web_core_layout(tree, rel, fragment)
            tree = web_extended_forms_layout(tree, rel, fragment)
            tree = web_extended_details_layout(tree, rel, fragment)
            tree = web_insights_layout(tree, rel, fragment)
            tree = web_collection_columns_layout(tree, rel, fragment)
            tree = web_customer_detail_columns_layout(tree, rel, fragment)
            tree = web_opportunity_columns_layout(tree, rel, fragment)
            tree = web_record_columns_layout(tree, rel, fragment)
            tree = web_visit_advice_layout(tree, rel, fragment)
            tree = web_review_lists_layout(tree, rel, fragment)
            tree = web_review_forms_layout(tree, rel, fragment)
            tree = web_review_pages_layout(tree, rel, fragment)
            config = json.loads(path.with_suffix('.json').read_text()) if path.with_suffix('.json').exists() else {}
            if config.get('navigationBarTitleText') == '销售智助':
                config['navigationBarTitleText'] = WEB_BRAND
            group = 'components' if rel.startswith('components/') else 'pages'
            stylesheet = css(path.with_suffix('.wxss'))
            if group == 'components':
                stylesheet = component_css(stylesheet, rel[:-5])
            data[group][rel[:-5]] = {'tree': tree, 'templates': templates, 'config': config, 'css': stylesheet}
        elif rel.startswith('images/'):
            data['images'][rel] = 'data:' + (mimetypes.guess_type(path.name)[0] or 'application/octet-stream') + ';base64,' + base64.b64encode(path.read_bytes()).decode()
    assert len(data['config']['pages']) == manifest['registered_pages']
    assert all(page in data['pages'] and page in data['modules'] for page in data['config']['pages'])
    # Additional Web page; keep the imported Mini Program inventory and manifest frozen.
    weekly_route = 'pages/weekly-report/index'
    data['config']['pages'].append(weekly_route)
    data['modules'][weekly_route] = (ROOT / 'web-pages' / 'weekly-report.js').read_text()
    data['pages'][weekly_route] = {'tree': [], 'templates': {}, 'config': {'navigationBarTitleText': '周报'}, 'css': ''}
    with tempfile.TemporaryDirectory(prefix='.build-', dir=ROOT) as temp:
        stage = Path(temp)
        (stage / 'bundle.js').write_text('window.SALES_BUNDLE=' + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';\n')
        for filename in ('index.html', 'entry.js', 'shell.js', 'desktop.css', 'date-picker.js', 'date-picker.css', 'runtime.js', 'runtime.css', 'browser-platform.js', 'preview-api.js', 'preview-workflow.js', 'crm-preview-ui.js', 'select-components.js', 'select-components.css', 'dashboard-charts.js', 'dashboard-charts.css', 'visual-theme.css', 'design-tokens.css', 'theme.js', 'theme.css', 'home-activity.js', 'home-activity.css', 'detail-workspace.js', 'detail-workspace.css', 'workbench-filters.css'):
            shutil.copyfile(ROOT / filename, stage / filename)
        for filename in ('task-workspace.js', 'task-workspace.css', 'workflow-forms.css', 'claim-workspace.css', 'workspace-frame.css', 'insights-workspace.css', 'workbench-columns.css', 'customer-columns.css', 'task-columns.css', 'collection-columns.css', 'customer-detail-columns.css', 'opportunity-detail-columns.css', 'record-detail-columns.css', 'review-lists.css', 'review-forms.css', 'review-pages.css', 'review-interactions.css', 'legacy.css', 'review-forms.js', 'review-pages.js', 'review-interactions.js', 'review-lists.js'):
            shutil.copyfile(ROOT / filename, stage / filename)
        shutil.copytree(ROOT / 'assets', stage / 'assets')
        shutil.copytree(ROOT / 'design-system', stage / 'design-system')
        (stage / 'version.json').write_text(json.dumps({'frontend_revision': data['revision'], 'pages': len(data['pages']), 'components': len(data['components']), 'source_files_verified': len(manifest['files'])}, indent=2))
        if DIST.exists():
            shutil.rmtree(DIST)
        shutil.copytree(stage, DIST)
    print(f"BUILD_OK: {len(data['pages'])} pages, {len(data['components'])} components; {len(manifest['files'])} shared files unchanged")

if __name__ == '__main__':
    build()
