"""Compact Web claim list; preserve the imported page's approval and selection logic."""

from web_detail_layout import nodes, one, take, wrap


def adapt(tree, route, fragment):
    if route != 'pages/customer-claim/index.wxml':
        return tree

    page = one(tree, 'claim-page')
    page['a']['class'] += ' web-claim-workspace'
    hero = one([page], 'claim-hero')
    hero['a']['class'] += ' web-claim-heading'
    take([hero], one([hero], 'claim-eyebrow'))
    title = one([hero], 'claim-title')
    title['t'] = 'h1'
    title['c'] = ['客户认领']

    main = one([page], 'claim-main')
    search = take([main], one([main], 'claim-search'))
    count = take([main], one([main], 'claim-section-head'))
    # Search, its result count and the loaded/total count keep their native bindings.
    toolbar = wrap('web-claim-toolbar', [search, count])
    result = take([main], one([main], 'claim-result'))
    result['a'].update({'role': 'status', 'aria-live': 'polite'})

    rows = one([main], 'claim-list')
    rows['a']['aria-label'] = '公司客户名单，选择一家客户提交认领申请'
    card = one([rows], 'claim-card')
    card['a'].update({
        'aria-pressed': '{{selectedCustomerId === item.id}}',
        'aria-label': '{{item.name}}，{{item.claimLabel}}',
        'data-eligible': '{{item.claimEligible}}',
        'data-claim-status': '{{item.claim_status}}',
    })
    # Do not disable ineligible rows: the original handler explains the exact
    # ownership / pending-review reason and clears an earlier selection.
    radio = one([card], 'claim-radio')
    radio['a']['aria-hidden'] = 'true'
    name = one([card], 'claim-name')
    name['a'].update({'title': '{{item.name}}', 'data-column': '客户名称'})
    level = next(n for n in name['c'] if isinstance(n, dict) and n['t'] == 'label')
    take([name], level)
    level_column = wrap('web-claim-level', [level] + fragment('<text wx:else>—</text>'), **{'data-column': '等级'})
    meta = one([card], 'claim-meta')
    meta['a']['data-column'] = '行业 / 团队'
    meta['c'] = fragment('''<text title="{{item.industry || '行业待补充'}}">{{item.industry || '行业待补充'}}</text><label title="{{item.team || '当前部门'}}">{{item.team || '当前部门'}}</label>''')
    owner = one([card], 'claim-owner')
    owner['a'].update({'data-column': '负责人', 'title': "{{item.owner || '未分配'}}"})
    owner['c'] = ["{{item.owner || '未分配'}}"]
    state = fragment('<view class="web-claim-state" data-column="认领状态"><text>{{item.claimLabel}}</text></view>')[0]
    card_main = one([card], 'claim-card-main')
    card_main['c'] = [name, level_column, meta, owner, state]

    table_head = fragment('''<view class="web-claim-columns" aria-hidden="true"><text></text><text>客户名称</text><text>等级</text><text>行业 / 团队</text><text>负责人</text><text>认领状态</text></view>''')[0]
    # The native if/elif/else list/error/empty chain and pagination chain stay
    # adjacent and unchanged. The Web runtime forwards scrolltolower to the
    # existing onReachBottom; retryMore remains available as an explicit action.
    scroll = wrap('web-claim-scroll', [table_head] + main['c'], tag='scroll-view', **{
        'scroll-y': 'true', 'bindscrolltolower': 'onReachBottom', 'lower-threshold': '80',
        'aria-label': '客户名单',
    })
    main['c'] = [toolbar, result, wrap('web-claim-table', [scroll])]

    footer = one([page], 'claim-footer')
    buttons = list(footer['c'])
    selection = fragment('''<view class="web-claim-selection" role="status" aria-live="polite">
      <block wx:if="{{selectedCustomerId}}"><text class="web-claim-selection-count">已选择 1 家客户</text><text wx:for="{{customers}}" wx:key="id" wx:if="{{selectedCustomerId === item.id}}" class="web-claim-selection-name" title="{{item.name}}">{{item.name}}</text></block>
      <text wx:else>请选择一家可申请认领的客户</text>
    </view>''')[0]
    footer['c'] = [selection, wrap('web-claim-footer-actions', buttons)]

    # Fail the build if a future source update silently changes this page's
    # actions. No endpoint, Page method, eligibility or approval state is replaced.
    expected = {'inputQuery', 'selectCustomer', 'refreshCustomers', 'retryMore', 'confirmClaim', 'onReachBottom', 'clearFilters', 'changeIndustry', 'retryFilterOptions', 'changeClaimStatus'}
    actual = {v for node in nodes(tree) for k, v in node['a'].items() if k.startswith('bind')}
    if actual != expected:
        raise ValueError('Review Web claim layout after source handler changes: ' + repr(actual))
    return tree
