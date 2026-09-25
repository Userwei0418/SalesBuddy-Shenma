"""Reviewed Web forms. Field editing delegates to the original shared handlers."""
from web_detail_layout import nodes, one, take


def adapt(tree, route, fragment):
    if route == 'pages/management-task-create/index.wxml':
        page = one(tree, 'web-task-create')
        page['a']['class'] += ' review-task-create'
        content = one([page], 'web-task-content')
        settings = one([page], 'web-task-settings')
        content['a']['aria-label'] = '任务内容'
        settings['a']['aria-label'] = '任务执行设置'
        for pane in (content, settings):
            headings = [n for n in nodes([pane]) if 'section-head' in n['a'].get('class', '').split()]
            for head in headings:
                text = next((n for n in nodes(head['c']) if n['t'] == 'text' and n['c'] in (['任务类型'], ['任务描述'], ['任务负责人'])), None)
                if text:
                    text['c'].append(fragment('<label class="review-required">必填</label>')[0])
        for node in nodes([page]):
            if 'task-type-check' in node['a'].get('class', '').split():
                node['c'] = []
                node['a']['aria-hidden'] = 'true'
        # Keep the workflow explanation beside settings; remove its duplicate footer.
        footer = one([page], 'bottom-bar')
        footer['c'] = [n for n in footer['c'] if not isinstance(n, dict) or n['t'] == 'button']
        for node in nodes([settings]):
            if 'choice' in node['a'].get('class', '').split():
                node['t'] = 'button'
                node['a']['type'] = 'button'
                node['a']['aria-pressed'] = '{{selectedPriority === item}}'

    elif route == 'pages/customer-create/index.wxml':
        page = one(tree, 'web-customer-create')
        page['a']['class'] += ' review-customer-create'
        hero = one([page], 'create-hero')
        take([hero], one([hero], 'progress-orb'))
        meta = one([hero], 'hero-meta')
        meta['c'] = [n for n in meta['c'] if not isinstance(n, dict) or n['c'] != ['必填 {{completedRequiredCount}} / {{requiredCount}}']]
        meta['c'].append(fragment('<text class="review-create-completion" aria-live="polite">{{webRemainingCount ? "还需填写 " + webRemainingCount + " 项" : "必填信息已完成"}}</text>')[0])
        progress = one([hero], 'hero-progress')
        progress['a'].update({'role': 'progressbar', 'aria-label': '客户必填信息完成进度', 'aria-valuenow': '{{webProgressPercent}}', 'aria-valuemin': '0', 'aria-valuemax': '100'})
        for node in nodes([progress]):
            if 'style' in node['a']:
                node['a']['style'] = node['a']['style'].replace('progressPercent', 'webProgressPercent')
        for node in list(nodes([page])):
            if node['a'].get('class') == 'notice orange':
                take([page], node)
        heading = one([page], 'section-heading')
        heading['c'] = fragment('<view><text>客户基本信息</text><label>直接填写内容，选择项支持搜索</label></view>')
        row = one([page], 'field-row')
        row['a'].pop('bindtap', None)
        row['a']['class'] = row['a']['class'].replace('item.missing', 'item.webInlineMissing')
        row['a']['data-field-key'] = '{{item.key}}'
        label = one([row], 'field-label')
        label['a']['id'] = 'review-label-{{item.key}}'
        row['c'] = [label] + fragment('''
          <input wx:if="{{item.webInlineKind === 'text' &amp;&amp; !item.readonly}}" class="review-customer-input" value="{{item.webInlineValue}}" placeholder="{{item.webInlinePlaceholder}}" aria-labelledby="review-label-{{item.key}}" aria-required="{{item.required}}" aria-invalid="{{!!item.webInlineError}}" aria-describedby="review-error-{{item.key}}" data-index="{{index}}" data-field-key="{{item.key}}" bindinput="webInputCustomerField" bindblur="webCommitCustomerField" />
          <button wx:else class="review-customer-choice" type="button" disabled="{{item.readonly}}" aria-labelledby="review-label-{{item.key}}" aria-required="{{item.required}}" data-index="{{index}}" bindtap="webChooseCustomerField"><text>{{item.value || '请选择'}}</text><span aria-hidden="true">⌄</span></button>
          <text wx:if="{{item.webInlineError}}" id="review-error-{{item.key}}" class="review-field-error" role="alert">{{item.webInlineError}}</text>
        ''')

    elif route == 'pages/opportunity-create/index.wxml':
        page = one(tree, 'web-opportunity-create')
        page['a']['class'] += ' review-opportunity-create'
        awaiting = one([page], 'web-op-awaiting')
        awaiting['a']['class'] += ' review-op-preview'
        awaiting['c'] = fragment('''
          <view class="review-op-preview-heading"><text>商机信息</text><label>选择左侧客户后开始填写</label></view>
          <view class="review-op-preview-grid" aria-label="待填写的商机字段">
            <view><label>商机名称 <text class="review-required">必填</text></label><input disabled placeholder="填写项目名，可用部门、场景区分" /></view>
            <view><label>商机阶段 <text class="review-required">必填</text></label><button disabled>选择商机阶段 <span>⌄</span></button></view>
            <view><label>ACV（万元）<text class="review-required">必填</text></label><input disabled placeholder="请输入金额" /></view>
            <view><label>预计关单日期 <text class="review-required">必填</text></label><button disabled>选择日期 <span>⌄</span></button></view>
            <view><label>所属伙伴 <text class="review-required">必填</text></label><button disabled>请确认销售渠道 <span>⌄</span></button></view>
            <view><label>协助 FDE <text>选填</text></label><button disabled>选择协助人员 <span>⌄</span></button></view>
          </view><label class="review-op-preview-note">选择客户后即可填写，保存后归入该客户。</label>
        ''')
        picker = one([page], 'customer-picker-card')
        one([picker], 'empty-hint')['a']['wx:if'] = '{{query && !customers.length && !webCustomerLoading}}'
        picker['c'].extend(fragment('''
          <view wx:if="{{!query}}" class="review-customer-state" role="status">{{webCustomerLoading ? '正在加载可见客户…' : webCustomerError ? '客户列表未能加载，请重试或搜索' : customers.length ? '当前可见客户，可继续搜索' : '暂无可见客户，可输入名称搜索'}}</view>
          <button wx:if="{{!query &amp;&amp; webCustomerError}}" class="review-customer-retry" bindtap="webLoadInitialCustomers">重新加载客户</button>
        '''))
    return tree
