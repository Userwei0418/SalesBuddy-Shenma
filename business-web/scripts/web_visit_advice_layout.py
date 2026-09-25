"""Keep archived-visit advice discoverable, using the native request and decisions.

Only the Web composition changes.  The source Page still decides when to fetch
automatically; an idle panel can invoke its existing loadAdvice handler manually.
"""
import copy
from web_detail_layout import nodes, one, take, wrap


def adapt(tree, route, fragment):
    if route != 'pages/visit-confirm/index.wxml':
        return tree
    page = one(tree, 'page')
    success = one([page], 'success-card')
    mask = one(tree, 'advice-mask')
    scroll = one([mask], 'advice-scroll')
    decision = one([mask], 'advice-item')
    original_decision = copy.deepcopy(decision)
    if success['a'].get('wx:if') != '{{archived}}' or mask['a'].get('wx:if') != '{{!isFde && archived && showAdvice}}':
        raise ValueError('Archived visit advice conditions changed; review the Web layout.')
    opens = [n for n in nodes([success]) if n['a'].get('bindtap') == 'openAdvice']
    if len(opens) != 1:
        raise ValueError('Archived visit advice entry changed; review the Web layout.')
    take([success], opens[0])
    # Drop only sheet presentation controls. Keep the native loading/error/empty
    # branches and the exact component properties, event and suggestion payload.
    if mask in tree:
        tree.remove(mask)
    else:
        take(tree, mask)
    scroll['t'] = 'view'
    scroll['a'] = {'class': 'web-visit-advice-content', 'aria-live': 'polite'}
    scroll['c'] += fragment('''<view wx:else class="sheet-status web-visit-advice-idle">
      <text>尚未获取本次待办建议</text>
      <view class="muted">下一步行动已保存在拜访记录中。获取建议后，可逐条确认是否创建待办。</view>
      <button class="v-button secondary" disabled="{{!visitId}}" bindtap="loadAdvice">获取待办建议</button>
    </view>''')
    panel = wrap('web-visit-advice-panel', fragment('''
      <view class="web-visit-advice-heading"><h2>待办事项推荐</h2>
        <view class="muted">拜访已保存；采纳后还需确认负责人、商机和截止时间。</view>
      </view>''') + [scroll], tag='section', **{'wx:if': '{{!isFde}}', 'aria-label': '待办事项推荐'})
    index = page['c'].index(success)
    success['a'].pop('wx:if')
    page['c'][index] = wrap('web-visit-archive-workspace', [success, panel], **{'wx:if': '{{archived}}'})
    if decision != original_decision:
        raise ValueError('Web archive layout changed a native advice decision.')
    return tree
