"""Keep each Web filter beside its results without changing shared query handlers."""
from web_detail_layout import nodes, one, take, wrap


def adapt(tree, fragment):
    summary = one(tree, 'opportunity-summary-card')
    summary['a'].update({'role': 'region', 'aria-label': '商机总览'})
    period = take(tree, one([summary], 'quarter-filter-summary'))
    metrics = one([summary], 'opportunity-summary-metrics')
    summary['c'].insert(summary['c'].index(metrics), period)
    one([period], 'quarter-filter-title')['c'] = ['统计时间']
    one([period], 'quarter-selection-label')['a']['wx:if'] = '{{summaryQuarter.quarters.length}}'
    all_periods = next(n for n in nodes([period]) if n['a'].get('data-value') == 'all')
    all_periods['c'] = ['全部时间']
    year = next(n for n in nodes([period]) if n['t'] == 'picker')
    year['a']['aria-label'] = '总览统计季度的年份'
    foot = one([period], 'quarter-filter-foot')
    help_link = take([period], one([period], 'metric-help'))
    foot['c'] = ['季度可多选 · 仅影响本区统计数字']
    head = one([summary], 'opportunity-summary-head')
    head['c'] = [wrap('web-summary-title-group', head['c']), help_link]

    tools = one(tree, 'workbench-opportunity-tools')
    filters = one([tools], 'workbench-opportunity-filter')
    title = one([filters], 'workbench-filter-title')
    title['c'] = ['筛选条件']
    one([filters], 'workbench-filter-reset')['c'] = ['清空列表筛选']
    close_label = next(n for n in nodes([one([filters], 'filter-close')]) if n['t'] == 'label')
    close_label['c'] = ['预计关单']
    conditions = fragment('''<view wx:if="{{opportunityFilterActive}}" class="web-list-conditions" role="status" aria-live="polite">
      <text class="web-list-conditions-label">已筛选</text>
      <text wx:if="{{role === 'manager' &amp;&amp; executionTeamIndex}}" class="web-list-condition">团队：{{executionTeamLabel}}</text>
      <text wx:if="{{role !== 'sales' &amp;&amp; opportunityOwnerIndex}}" class="web-list-condition">负责人：{{opportunityOwnerOptions[opportunityOwnerIndex].label}}</text>
      <block wx:for="{{opportunityStageOptions}}" wx:key="value"><text wx:if="{{item.selected}}" class="web-list-condition">阶段：{{item.label}}</text></block>
      <text wx:if="{{listQuarter.quarters.length || opportunityCloseIndex}}" class="web-list-condition">预计关单：{{opportunityCloseIndex ? opportunityCloseOptions[opportunityCloseIndex].label : listQuarter.label}}</text>
      <text wx:if="{{opportunityGradeIndex}}" class="web-list-condition">等级：{{opportunityGradeOptions[opportunityGradeIndex].label}}</text>
    </view>''')
    filters['c'].extend(conditions)
    heading = fragment('''<view class="web-opportunity-list-heading">
      <view class="web-opportunity-list-title">商机列表<text wx:if="{{!opportunityListLoading &amp;&amp; !opportunityListError}}" class="web-list-count">{{opportunityTotal}}</text><text wx:if="{{opportunityListLoading}}" class="web-list-loading" role="status">读取中…</text></view>
      <text class="web-list-scope">以下条件仅筛选本列表</text>
    </view>''')[0]
    tools['c'].insert(0, heading)
    # The original list/empty/error/pagination siblings stay in the same order.
    parent = next(n for n in nodes(tree) if any(c is tools for c in n['c']))
    start = next(i for i, n in enumerate(parent['c']) if isinstance(n, dict) and n['a'].get('wx:if') == '{{opportunityListLoading}}')
    content = parent['c'][start:]
    content.remove(tools)
    parent['c'][start:] = [wrap('web-opportunity-results surface', [tools, *content], role='region', **{'aria-label': '商机列表'})]
    return tree
