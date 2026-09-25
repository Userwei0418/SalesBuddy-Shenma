"""Desktop composition only. Reuse the Mini Program's conditions and handlers."""
from web_detail_layout import nodes, one, take, wrap


def adapt(tree, route, fragment):
    if route == 'pages/opportunity-create/index.wxml':
        page = one(tree, 'page-shell')
        page['a']['class'] += ' web-workflow web-opportunity-create'
        hero = one([page], 'opportunity-create-hero')
        context = take([hero], one([hero], 'customer-context'))
        # The original customer search and context share a narrow reference pane.
        picker = one([page], 'customer-picker-card')
        loading = one([page], 'loading-card')
        form = one([page], 'form-card')
        error = one([page], 'error-card')
        footer = one([page], 'footer')
        intro = fragment('''<view wx:if="{{!customerId}}" class="web-op-awaiting"><view class="web-op-awaiting-mark">1</view><text>先选择所属客户</text><label>选定客户后，在这里填写商机名称、阶段、金额和预计关单日期。</label></view>''')[0]
        page['c'] = [hero, error, wrap('web-workflow-scroll web-op-columns', [
            wrap('web-op-customer-pane', [picker, context], tag='aside'),
            wrap('web-op-form-pane', [intro, loading, form])]), footer]
        one([picker], 'search')['a']['aria-label'] = '搜索商机所属客户'
        for row in nodes([picker]):
            if row['a'].get('bindtap') == 'selectCustomer':
                row['t'] = 'button'
                row['a']['type'] = 'button'
        one([form], 'card-title')['c'] = ['商机信息']
        # Only decorative headings are removed. No permission/error hint is hidden.
        for cls in ('hero-glow', 'hero-eyebrow'):
            take([hero], one([hero], cls))
        for card in (picker, form):
            take([card], one([card], 'step-chip'))

    elif route == 'pages/management-task-create/index.wxml':
        page = one(tree, 'task-page')
        page['a']['class'] += ' web-workflow web-task-create'
        hero = one([page], 'task-hero')
        take([hero], one([hero], 'hero-eyebrow'))
        body = one([page], 'task-body')
        content = [n for n in body['c'] if isinstance(n, dict)]
        headings = [n for n in content if 'section-head' in n['a'].get('class', '').split()]
        if len(headings) != 4:
            raise ValueError('Review task creation sections before desktop adaptation')
        split = content.index(headings[2])
        textarea = next(n for n in nodes(content) if n['t'] == 'textarea')
        textarea['a'].pop('auto-height', None)
        textarea['a']['aria-label'] = '任务描述'
        body['a']['class'] += ' web-workflow-scroll web-task-columns'
        body['c'] = [wrap('web-task-content web-workflow-panel', content[:split]),
                     wrap('web-task-settings web-workflow-panel', content[split:], tag='aside')]
        due_head = one([body], 'custom-due-head')
        required = next(n for n in due_head['c'] if isinstance(n, dict) and n['t'] == 'text')
        take([due_head], required)
        required['a']['class'] = 'web-required'
        one([body], 'setting-label-row')['c'].append(required)
        # Preserve the native chooser layer and confirmation; only its presentation changes.
        one([page], 'bottom-bar')['a']['class'] += ' web-workflow-footer'
    return tree
