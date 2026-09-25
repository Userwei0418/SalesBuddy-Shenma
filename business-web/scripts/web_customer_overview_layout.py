"""Web-only customer overview grouping; no new data or business handlers."""
from web_detail_layout import nodes, one, take, wrap


def _overview(tree, root, condition, metric_class, heading_class, action_class):
    overview = next(n for n in nodes([root]) if n['a'].get('wx:if') == condition)
    overview['a']['class'] = (overview['a'].get('class', '') + ' web-customer-overview-tab').strip()
    metrics = take([root], one([overview], metric_class))
    metrics['a']['wx:if'] = condition
    metrics['a']['class'] += ' web-customer-kpis'
    tabs = one([root], 'detail-tabs')
    root['c'].insert(root['c'].index(tabs), metrics)
    action = take([overview], one([overview], action_class))
    action['a']['class'] += ' web-customer-next-action'
    action['a']['wx:if'] = condition
    one([root], 'web-detail-aside')['c'].insert(0, action)
    children = [n for n in overview['c'] if isinstance(n, dict) or n.strip()]
    headings = [i for i, n in enumerate(children) if isinstance(n, dict) and heading_class in n['a'].get('class', '').split()]
    if len(headings) != 2 or headings[0] != 0:
        raise ValueError('Review customer overview sections after native template changes')
    split = headings[1]
    opportunities = wrap('web-customer-overview-section web-customer-overview-opportunities', children[:split], tag='section')
    contacts = wrap('web-customer-overview-section web-customer-overview-contacts', children[split:], tag='section')
    overview['c'] = [wrap('web-customer-overview-panels', [opportunities, contacts])]
    return overview, contacts


def adapt(tree, route, fragment):
    if route not in ('pages/customers/index.wxml', 'pages/customer-detail/index.wxml'):
        return tree
    bindings = sorted((k, v) for node in nodes(tree) for k, v in node['a'].items() if k.startswith(('bind', 'catch')))
    if route == 'pages/customers/index.wxml':
        page = one(tree, 'customer-page')
        page['a']['class'] += ' web-customer-overview-page'
        search = take([page], one([page], 'map-toolbar'))
        filters = take([page], one([page], 'operating-filters'))
        asset = one([page], 'asset-overview')
        page['c'].insert(page['c'].index(asset) + 1, wrap('web-customer-list-controls', [search, filters]))
        heading = take([page], one([page], 'asset-page-heading'))
        asset = take([page], asset)
        page['c'].insert(0, wrap('web-customer-summarybar', [heading, asset]))
        battle = take([page], one([page], 'battle-section'))
        heading = take([page], next(n for n in page['c'] if isinstance(n, dict) and 'asset-list-heading' in n['a'].get('class', '').split()))
        listing = take([page], next(n for n in page['c'] if isinstance(n, dict) and 'customer-list' in n['a'].get('class', '').split()))
        pane = wrap('web-customer-list-pane', [heading, wrap('web-customer-list-scroll', [listing], tag='scroll-view', **{'scroll-y': 'true', 'data-web-page-scroll': 'customers', 'aria-label': '客户列表'})])
        insert = next(i for i, n in enumerate(page['c']) if isinstance(n, dict) and ('customer-sheet-layer' in n['a'].get('class', '').split() or 'detail-overlay' in n['a'].get('class', '').split()))
        page['c'].insert(insert, wrap('web-customer-results', [battle, pane], **{'data-web-page-scroll': 'customers'}))
        # Shared filters govern both visible columns. Scroll ownership belongs
        # to the desktop list, or the combined results on narrower windows.
        controls = take([page], one([page], 'web-customer-list-controls'))
        results = take([page], one([page], 'web-customer-results'))
        errors = [n for n in page['c'] if isinstance(n, dict) and n['a'].get('wx:if') == '{{directoryError}}']
        content = [controls]
        content.extend(take([page], error) for error in errors)
        content.append(results)
        summary = one([page], 'web-customer-summarybar')
        page['c'].insert(page['c'].index(summary) + 1, wrap('web-customer-content', content, **{'aria-label': '客户经营内容'}))
        root = one([page], 'web-customer-workspace')
        root['a']['class'] += ' web-customer-overview-detail'
        _overview(tree, root, "{{detailTab === 'overview'}}", 'detail-metrics', 'detail-title', 'detail-ai')
        for link in nodes([root]):
            if 'opportunity-actual-link' in link['a'].get('class', '').split():
                link['c'] = ['商机详情']
    else:
        root = one(tree, 'web-standalone-customer')
        root['a']['class'] += ' web-customer-overview-detail'
        _, contacts = _overview(tree, root, "{{activeTab === 'overview'}}", 'metric-grid', 'section-heading', 'ai-card')
        # The original contacts pagination sits outside its tab; move that wrapper
        # intact into its visual section so loading/retry remains beside the list.
        native = [n for n in nodes([root]) if n['t'] == 'view' and n['a'].get('wx:if') == "{{activeTab === 'overview'}}" and any(isinstance(c, dict) and c['t'] == 'detail-pagination' and c['a'].get('data-section') == 'contacts' for c in n['c'])]
        if len(native) != 1:
            raise ValueError('Review standalone customer contact pagination')
        contacts['c'].append(take([root], native[0]))
    if bindings != sorted((k, v) for node in nodes(tree) for k, v in node['a'].items() if k.startswith(('bind', 'catch'))):
        raise ValueError('Customer overview layout must preserve every native action')
    return tree
