"""Web-only layout of shared detail and visit templates; business handlers stay native."""


def nodes(items):
    for node in items:
        if isinstance(node, dict):
            yield node
            yield from nodes(node['c'])


def one(tree, cls):
    found = [n for n in nodes(tree) if cls in n['a'].get('class', '').split()]
    if len(found) != 1:
        raise ValueError(f'Review Web detail layout: expected one {cls}, got {len(found)}')
    return found[0]


def take(tree, node):
    for parent in nodes(tree):
        if any(child is node for child in parent['c']):
            parent['c'].remove(node)
            return node
    raise ValueError('Web detail node no longer has the expected parent')


def wrap(cls, children, tag='view', **attrs):
    return {'t': tag, 'a': {'class': cls, **attrs}, 'c': children}


def adapt(tree, route, fragment):
    if route == 'pages/customers/index.wxml':
        overlay = one(tree, 'detail-overlay')
        overlay['a']['class'] += ' web-customer-workspace'
        body = one([overlay], 'detail-body')
        hero = take(tree, one([overlay], 'detail-hero'))
        score = take(tree, one([overlay], 'detail-score'))
        tabs = take(tree, one([overlay], 'detail-tabs'))
        edit = one([hero], 'manual-edit-entry')
        take([hero], edit)
        edit['a']['class'] += ' web-detail-edit'
        edit['a']['title'] = '维护客户事实；Agent 计算结果只读'
        edit['c'] = ['编辑客户']
        actions = fragment('''<view class="web-detail-actions"><button wx:if="{{canCreateTask}}" bindtap="createTask">创建任务</button><button wx:if="{{canEditOpportunity}}" bindtap="createOpportunity">新增商机</button></view>''')[0]
        actions['c'].insert(0, edit)
        header = wrap('web-detail-header', [hero, actions])
        scroll = one([overlay], 'detail-scroll')
        overlay['c'].insert(overlay['c'].index(scroll), header)
        overlay['c'].insert(overlay['c'].index(scroll), tabs)
        # Both existing advice placements have identical inputs; show one side panel.
        advice = [n for n in nodes(body['c']) if n['t'] == 'template' and n['a'].get('is') == 'customer-advice-panel']
        if len(advice) != 2:
            raise ValueError('Review customer advice placements')
        for node in advice:
            take([body], node)
        advice[0]['a']['wx:if'] = '{{!isFde}}'
        body['c'] = [wrap('web-detail-main', body['c']), wrap('web-detail-aside', [score, advice[0]], tag='aside')]
        body['a']['class'] += ' web-detail-columns'
        nav = one([overlay], 'detail-nav')
        back = one([nav], 'detail-back')
        back['t'] = 'button'
        back['c'] = ['‹ 返回客户列表']
        back['a']['aria-label'] = '返回客户列表'

    elif route == 'pages/customer-detail/index.wxml':
        page = one(tree, 'detail-page')
        page['a']['class'] += ' web-standalone-customer'
        hero = take(tree, one([page], 'customer-hero'))
        score = take(tree, one([page], 'score-card'))
        links = take(tree, one([page], 'detail-context-links'))
        tabs = take(tree, one([page], 'detail-tabs'))
        bottom = take(tree, one([page], 'bottom-actions'))
        bottom['a']['class'] = 'web-detail-record'
        # Reuse the original recordVisit action, whose entry supports text, voice and files.
        button = next(n for n in bottom['c'] if isinstance(n, dict))
        button['c'] = ['记录拜访']
        content = page['c']
        page['c'] = [wrap('web-detail-header', [hero, wrap('web-detail-actions', [links, bottom])]), tabs,
                     wrap('web-detail-columns', [wrap('web-detail-main', content), wrap('web-detail-aside', [score], tag='aside')])]

    elif route == 'pages/customer-assets/index.wxml':
        page = one(tree, 'actual-page')
        page['a']['class'] += " {{opportunityId ? 'web-opportunity-workspace' : ''}}"
        heading = one([page], 'actual-heading')
        help_button = take([heading], one([heading], 'actual-info'))
        edit = fragment('''<button wx:if="{{webCanEditOpportunity}}" disabled="{{fdeRelationSaving}}" bindtap="webEditOpportunity">编辑商机</button>''')[0]
        heading['c'].append(wrap('web-detail-actions web-opportunity-actions', [edit, help_button]))
        note = one([page], 'opportunity-readonly-note')
        note['c'] = ["{{webCanEditOpportunity ? '可通过右上方「编辑商机」修改商业信息。' : '当前账号可查看商业信息。'}}{{canManageFdeRelation && canManageFde ? '协助名单可在此调整，调整后请保存。' : '协助名单由有权限的人员维护。'}}"]
        advice = take(tree, one([page], 'op-advice'))
        tabs = one([page], 'op-detail-tabs')
        # Summary is a read-only projection of the normalized opportunity, on every tab.
        summary = fragment('''<view wx:if="{{opportunityId && opportunity}}" class="web-op-summary">
          <view><label>客户</label><text>{{opportunity.customerName}}</text></view>
          <view><label>ACV（商机金额）</label><strong>{{opportunity.amount}}</strong></view>
          <view><label>当前阶段</label><text>{{opportunity.stageText}}</text></view>
          <view><label>预计签约</label><text>{{opportunity.expectedDate}}</text></view>
          <view><label>经营状态</label><text class="traffic-badge traffic-{{opportunity.signal.tone}}">{{opportunity.signal.label}} · {{opportunity.statusLabel}}</text></view>
        </view>''')[0]
        page['c'].insert(page['c'].index(tabs), summary)
        tabs['a']['class'] += ' web-detail-sticky-tabs'
        # Leave the asset-only page and all modal form conditions intact.
        first = page['c'].index(tabs) + 1
        rest = page['c'][first:]
        page['c'][first:] = [wrap('web-op-columns', [wrap('web-op-main', rest), wrap('web-op-aside', [advice], tag='aside')])]
        overview = next(n for n in nodes(rest) if n['a'].get('wx:if') == "{{!opportunityId || opportunityTab==='overview'}}")
        overview['t'] = 'view'
        overview['a']['class'] = 'web-op-overview'
        info = take([overview], one([overview], 'opportunity-view'))
        overview['c'] = [info, wrap('web-op-actuals', overview['c'])]

    elif route == 'pages/visit-entry/index.wxml':
        # v1.0.6 adds a native fixed navigation bar and matching spacer. The Web
        # desktop already has its own app header; retain both only below 901 px.
        one(tree, 'entry-navigation')
        spacers = [n for n in nodes(tree) if n['a'].get('style') == 'height:{{navTop + navHeight}}px;']
        if len(spacers) != 1:
            raise ValueError('Review Web visit navigation spacer')
        spacers[0]['a']['class'] = 'web-entry-navigation-space'
        page = one(tree, 'visit-entry-page')
        page['a']['class'] += ' web-visit-workspace'
        hero = one([page], 'entry-hero')
        customer = one([page], 'customer-picker-card')
        note = one([page], 'note-card')
        capture = one([page], 'capture-card')
        process = one([page], 'process-card')
        tip = one([page], 'safe-tip')
        submit = one([page], 'submit-button')
        first_visit = take([note], one([note], 'first-visit-field'))
        customer['c'].append(first_visit)
        textarea = one([note], 'note-input')
        textarea['a'].pop('auto-height', None)
        textarea['a']['aria-label'] = '拜访原始记录'
        hero['c'] = [wrap('web-visit-heading', hero['c']), process]
        page['c'] = [hero, customer, wrap('web-visit-editor', [note, capture]), tip, submit]
    return tree
