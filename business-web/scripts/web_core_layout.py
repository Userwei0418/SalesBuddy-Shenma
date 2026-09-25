"""Primary Web scroll surfaces for existing core pages; native callbacks remain intact."""
from web_detail_layout import nodes, one, take, wrap


def adapt(tree, route, fragment):
    if route == 'pages/tasks/index.wxml':
        page = one(tree, 'web-task-workspace')
        listing = one([page], 'web-task-list')
        content = one([page], 'web-task-content')
        start = content['c'].index(listing)
        content['c'][start:] = [wrap('web-task-results-scroll', content['c'][start:], tag='scroll-view', **{'scroll-y': 'true', 'data-web-page-scroll': 'true', 'aria-label': '任务列表'})]
    elif route == 'pages/workbench/index.wxml':
        results = one(tree, 'web-opportunity-results')
        tools = one([results], 'workbench-opportunity-tools')
        content = [n for n in results['c'] if n is not tools]
        results['c'] = [tools, wrap('web-opportunity-results-scroll', content, tag='scroll-view', **{'scroll-y': 'true', 'data-web-page-scroll': 'true', 'aria-label': '商机列表'})]
        # Desktop pairs the existing overview with one complete list panel.
        # The inner scroll owner remains available for the narrow layout.
        head, summary = one(tree, 'page-head'), one(tree, 'opportunity-summary-card')
        parent = next(n for n in nodes(tree) if any(c is results for c in n['c']))
        start = parent['c'].index(head)
        for node in (head, summary, results):
            take(tree, node)
        results['a']['data-web-page-scroll'] = 'true'
        parent['c'].insert(start, wrap('web-workbench-columns', [
            wrap('web-workbench-overview', [head, summary], tag='aside', **{'aria-label': '商机概况'}),
            results,
        ]))
    elif route == 'pages/visit-entry/index.wxml':
        page = one(tree, 'web-visit-workspace')
        customer = take([page], one([page], 'customer-picker-card'))
        editor = take([page], one([page], 'web-visit-editor'))
        page['c'].insert(1, wrap('web-visit-content', [customer, editor], **{'data-web-page-scroll': 'true'}))
    elif route in ('pages/opportunity-create/index.wxml', 'pages/management-task-create/index.wxml'):
        one(tree, 'web-workflow-scroll')['a']['data-web-page-scroll'] = 'true'
    return tree
