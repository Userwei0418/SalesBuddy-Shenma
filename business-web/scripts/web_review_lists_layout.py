"""One Web opportunity row projection; original reads, filters and actions remain native."""
from web_detail_layout import nodes, one, take, wrap


def _actions(tree):
    return sorted((key, value) for node in nodes(tree) for key, value in node['a'].items()
                  if key.startswith(('bind', 'catch')))


def _table(tree, row_class, list_class, fragment, editable):
    listing = one(tree, list_class)
    listing['a'].update({'role': 'table', 'aria-label': '商机列表', 'data-web-review-list': 'opportunity'})
    listing['a']['class'] += ' web-review-opportunity-table'
    group = one([listing], 'opportunity-quarter-group')
    group['a']['role'] = 'rowgroup'
    heading = one([group], 'opportunity-quarter-heading')
    heading['a']['class'] += ' web-review-quarter-heading'
    heading['a']['role'] = 'row'
    heading['c'] = [wrap('web-review-quarter-label', heading['c'], role='cell', **{'aria-colspan': '7'})]
    row = one([group], row_class)
    row['a'].update({'role': 'row', 'aria-label': '{{item.name}}，{{item.customer_name}}，打开商机详情'})
    row['a']['class'] += ' web-review-opportunity-row'
    # Workbench's original edit action lives in an imported template. Reuse the
    # exact capability guard, datasets and catch handler; do not add edit to the
    # separate opportunities page, which has no such handler or permission data.
    edit = '''<view wx:if="{{item.canEdit}}" class="opportunity-card-edit web-review-row-edit" role="button" aria-label="编辑商机：{{item.name}}" data-customer-id="{{item.customer_id}}" data-opportunity-id="{{item.id}}" catchtap="editOpportunity">编辑</view>''' if editable else ''
    grade = '{{item.gradeLabel}}' if editable else '{{item.gradeText}}'
    row['c'] = fragment('''
      <view class="web-review-op-identity" role="cell" data-column="商机 / 客户">
        <view class="web-review-op-name"><text>{{item.name}}</text><label class="opportunity-grade grade-{{item.gradeCode}}">''' + grade + '''</label></view>
        <view class="web-review-op-customer">{{item.customer_name}}</view>
        <view class="web-review-op-owner">{{item.team}} · {{item.owner}}</view>
      </view>
      <view class="web-review-op-stage" role="cell" data-column="阶段 / 状态">
        <view class="web-review-stage-title"><text>{{item.stageName}}</text><label>{{item.probabilityText}}</label></view>
        <view class="web-review-stage-track" aria-hidden="true"><text style="width:{{item.progressPercent}}%"></text></view>
        <text class="web-review-op-signal traffic-text-{{item.signal.tone}}">{{item.signal.label}} · {{item.signal.detail}}</text>
      </view>
      <view class="web-review-op-amount" role="cell" data-column="累计确收">{{item.recognizedLabel}}</view>
      <view class="web-review-op-amount" role="cell" data-column="累计回款">{{item.collectionLabel}}</view>
      <view class="web-review-op-date" role="cell" data-column="预计关单">{{item.closeLabel}}</view>
      <view class="web-review-op-product" role="cell" data-column="产品线">{{item.productLineLabel}}</view>
      <view class="web-review-op-actions" role="cell" data-column="操作">''' + edit + '''<text class="web-review-row-open" aria-hidden="true">查看 ›</text></view>
    ''')
    head = fragment('''<view class="web-review-opportunity-columns" role="row"><text role="columnheader">商机 / 客户</text><text role="columnheader">阶段 / 状态</text><text role="columnheader" class="web-review-money-heading">累计确收</text><text role="columnheader" class="web-review-money-heading">累计回款</text><text role="columnheader">预计关单</text><text role="columnheader">产品线</text><text role="columnheader">操作</text></view>''')[0]
    note = take([listing], one([listing], 'opportunity-order-note'))
    note['a']['role'] = 'row'
    note['c'] = [wrap('web-review-sort-note', note['c'], role='cell', **{'aria-colspan': '7'})]
    # Keep the native if/elif loading/error/empty chain adjacent.
    listing['c'][0:0] = [note, head]
    return row


def adapt(tree, route, fragment):
    if route not in ('pages/workbench/index.wxml', 'pages/opportunities/index.wxml', 'pages/customer-claim/index.wxml'):
        return tree
    original = _actions(tree)
    if route == 'pages/customer-claim/index.wxml':
        row = one(tree, 'claim-card')
        row['a']['aria-label'] = "{{item.name}}，{{item.claimLabel}}，{{item.claimEligible ? '可选择' : '不可选择，点击查看原因'}}"
        row['a']['title'] = "{{item.claimEligible ? '选择此客户提交认领申请' : item.claimLabel + '，点击查看原因'}}"
        one([row], 'claim-radio')['a']['wx:if'] = '{{item.claimEligible}}'
        # An unavailable row still invokes selectCustomer so the existing
        # explanation and clearing of a previous selection are preserved.
        assert _actions(tree) == original, 'Claim presentation must preserve all native actions'
        return tree
    if route == 'pages/workbench/index.wxml':
        _table(tree, 'workbench-opportunity-card', 'workbench-opportunity-list', fragment, True)
        scope = one(tree, 'web-list-scope')
        scope['c'] = ['筛选商机列表']
        scope['a']['class'] += ' web-review-list-scope'
        assert _actions(tree) == sorted(original + [('catchtap', 'editOpportunity')]), 'Workbench table must preserve original actions'
    else:
        _table(tree, 'card', 'list', fragment, False)
        page = one(tree, 'web-collection-opportunities')
        page['a']['class'] += ' web-review-opportunities-page'
        hero = one([page], 'hero')
        one([hero], 'eyebrow')['c'] = ['{{scope}}']
        one([hero], 'subtitle')['c'] = ['按阶段、预计关单和等级筛选']
        # This route has no independent overview statistics; its result count
        # belongs above the list, so avoid a nearly empty desktop context rail.
        assert _actions(tree) == original, 'Opportunity table must preserve all native actions'
    return tree
