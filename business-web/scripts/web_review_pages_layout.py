"""Presentation-only follow-up to the September Web review.

Keep the shared Page's handlers, permissions and data intact. Runtime decoration
only formats rendered text; it never writes a Page field or creates a request.
"""
from web_detail_layout import nodes, one, take
from web_extended_details_layout import bindings


def _avatar(node, name):
    node['a']['data-web-avatar-name'] = name
    node['a']['aria-hidden'] = 'true'


def adapt(tree, route, fragment):
    before = bindings(tree)
    if route == 'pages/index/index.wxml':
        metric = one(tree, 'web-home-metric')
        metric['a']['title'] = '{{item.hint}} · 点击查看任务'
        metric['a']['aria-label'] = '{{item.label}}：{{item.value}}。{{item.hint}}，点击查看任务'
        # No fabricated recent objects or recommendations are added to fill space.
        one(tree, 'web-home-summary')['c'].append(fragment(
            '<view class="web-review-metric-note">点击指标查看任务；按当前账号授权范围统计</view>'
        )[0])
    elif route in ('pages/customers/index.wxml', 'pages/customer-detail/index.wxml'):
        for node in nodes(tree):
            classes = set(node['a'].get('class', '').split())
            if 'logo' in classes or 'detail-contact-logo' in classes or 'contact-avatar' in classes:
                _avatar(node, '{{item.name}}')
            elif 'detail-logo' in classes:
                _avatar(node, '{{selectedCustomer.name}}')
            elif 'customer-mark' in classes:
                _avatar(node, '{{customer.name}}')
            if 'operating-picker-value' in classes:
                node['a']['data-web-filter-value'] = 'true'
            if 'opportunity-list-card' in classes:
                node['a']['data-web-review-stage'] = 'true'
    elif route == 'pages/bi/index.wxml':
        heading = one(tree, 'page-heading')
        for node in nodes([heading]):
            node['c'] = ['经营分析' if c == '看板' else c for c in node['c']]
        one(tree, 'metrics-title')['c'] = ['统计范围']
        # The native countdown and exact money formatting remain authoritative.
        one(tree, 'countdown-card')['a']['aria-label'] = '季度时间进度'
    elif route == 'pages/visit-entry/index.wxml':
        page = one(tree, 'web-visit-workspace')
        hero = one([page], 'entry-hero')
        steps = take([hero], one([hero], 'process-card'))
        steps['a']['class'] += ' web-review-visit-steps'
        steps['a']['role'] = 'list'
        steps['a']['aria-label'] = '拜访录入步骤'
        for child in steps['c']:
            if isinstance(child, dict) and child['t'] == 'view':
                child['a']['role'] = 'listitem'
        hero['c'].append(steps)
        picker = one([page], 'customer-picker-card')
        picker['c'].extend(fragment('''
          <view wx:if="{{!customerConfirmed}}" class="web-review-selection-help" role="note">请先从匹配结果选择客户，再提交拜访内容。</view>
          <view wx:elif="{{isFde && !fdeOpportunityVerified}}" class="web-review-selection-help" role="note">还需选择本人参与的商机，才能提交拜访。</view>
        '''))
        selected = one([picker], 'selected-customer')
        first = next(n for n in selected['c'] if isinstance(n, dict) and n['t'] == 'view')
        _avatar(first, '{{customerName}}')
        result = one([picker], 'customer-result')
        first = next(n for n in result['c'] if isinstance(n, dict) and n['t'] == 'view')
        _avatar(first, '{{item.name}}')
        # Put recording/file tools beside the input header instead of after a
        # screen-height blank textarea. All original controls move together.
        editor = one([page], 'web-visit-editor')
        note = one([editor], 'note-card')
        capture = take([editor], one([editor], 'capture-card'))
        note['c'].insert(note['c'].index(one([note], 'note-input')), capture)
    elif route == 'pages/task-detail/index.wxml':
        response = one(tree, 'response-card')
        footer = next(n for n in nodes(tree) if 'bottom-bar' in n['a'].get('class', '').split()
                      and n['a'].get('wx:if') == '{{task.canRespond}}')
        notice = next(n for n in footer['c'] if isinstance(n, dict)
                      and 'response-button-row' not in n['a'].get('class', '').split())
        take([footer], notice)
        notice['a']['class'] = 'web-review-response-notice'
        notice['a']['id'] = 'web-task-response-note'
        textarea = next(n for n in nodes([response]) if n['t'] == 'textarea')
        textarea['a']['aria-describedby'] = 'web-task-response-note'
        response['c'].insert(response['c'].index(textarea), notice)
    if before != bindings(tree):
        raise ValueError('Review presentation changed native handlers: ' + route)
    return tree
