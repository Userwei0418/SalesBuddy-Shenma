"""Web presentation for insight/list pages. Native state, actions and permissions remain intact."""
from web_detail_layout import nodes, one, take, wrap


def _clean(items):
    return [n for n in items if isinstance(n, dict) or n.strip()]


def _scroll(children, extra=''):
    return wrap('web-insights-scroll ' + extra, children, **{'data-web-page-scroll': 'true', 'tabindex': '0', 'aria-label': '完整内容'})


def _frame(root, header, content, kind):
    root['a']['class'] = root['a'].get('class', '') + ' web-insights-workspace web-insights-' + kind
    root['c'] = [wrap('web-insights-header', header), _scroll(content)]


def adapt(tree, route, fragment):
    routes = ('pages/bi/index.wxml', 'pages/profile/index.wxml', 'pages/member-growth/index.wxml',
              'pages/fde-records/index.wxml', 'pages/risks/index.wxml', 'pages/opportunities/index.wxml',
              'components/fde-dashboard/index.wxml', 'components/fde-projects/index.wxml')
    if route not in routes:
        return tree
    actions = sorted((k, v) for n in nodes(tree) for k, v in n['a'].items() if k.startswith(('bind', 'catch')))
    if route == 'pages/bi/index.wxml':
        root = one(tree, 'bi-page')
        head = [take([root], one([root], c)) for c in ('page-head', 'dashboard-quarter-filter')]
        metrics = one([root], 'metrics-section')
        head += [take([metrics], one([metrics], c)) for c in ('metrics-heading', 'metrics-meta')]
        _frame(root, head, _clean(root['c']), 'bi')
    elif route == 'pages/profile/index.wxml':
        root = one(tree, 'profile-page')
        card = take([root], one([root], 'profile-card'))
        logout = take([root], one([root], 'logout-button'))
        prefix = [wrap('web-insights-identity', [card, logout])]
        content = take([root], one([root], 'profile-content'))
        children = _clean(content['c'])
        cut = next(i for i, n in enumerate(children) if isinstance(n, dict) and 'profile-directory-state' in n['a'].get('class', '').split())
        prefix += children[:cut]
        _frame(root, prefix, children[cut:] + _clean(root['c']), 'profile')
    elif route == 'pages/member-growth/index.wxml':
        root = one(tree, 'member-growth-page')
        hero = take([root], one([root], 'member-hero'))
        _frame(root, [hero], _clean(root['c']), 'growth')
    elif route == 'pages/risks/index.wxml':
        root = one(tree, 'risk-page')
        head = [take([root], one([root], c)) for c in ('risk-hero', 'risk-tabs')]
        content = _clean(root['c'])
        # Loading was already native state; expose it instead of showing an empty result during a request.
        content = fragment('<view wx:if="{{loading}}" class="web-insights-state" role="status">正在加载风险…</view>') + [wrap('', content, tag='block', **{'wx:else': ''})]
        _frame(root, head, content, 'risks')
    elif route == 'pages/opportunities/index.wxml':
        root = one(tree, 'page')
        hero = take([root], one([root], 'hero'))
        create = take([root], one([root], 'create-row'))
        filters = take([root], one([root], 'filter-card'))
        _frame(root, [wrap('web-insights-titlebar', [hero, create]), filters], _clean(root['c']), 'opportunities')
    elif route == 'components/fde-dashboard/index.wxml':
        root = one(tree, 'fde-board')
        root['a']['class'] += ' web-insights-workspace web-insights-fde'
        native = next(n for n in _clean(root['c']) if n['a'].get('wx:if') == '{{!recordsOnly}}')
        head = [take([native], one([native], c)) for c in ('page-head', 'period-filter', 'metrics-section')]
        native['c'] = [wrap('web-insights-header', head), _scroll(_clean(native['c']), 'web-fde-dashboard-content')]
        layer = one([root], 'activity-layer')
        layer['a']['class'] += ' web-fde-records'
        scroll = one([layer], 'activity-scroll')
        scroll['a']['data-web-page-scroll'] = 'true'
        scroll['a']['aria-label'] = '完整跟进记录'
    elif route == 'components/fde-projects/index.wxml':
        root = one(tree, 'projects')
        children = _clean(root['c'])
        cut = next(i for i, n in enumerate(children) if isinstance(n, dict) and 'fde-result-note' in n['a'].get('class', '').split()) + 1
        _frame(root, children[:cut], children[cut:], 'projects')
    # fde-records itself deliberately retains the native guarded component/context.
    if actions != sorted((k, v) for n in nodes(tree) for k, v in n['a'].items() if k.startswith(('bind', 'catch'))):
        raise ValueError('Insights Web layout must preserve every native action')
    return tree
