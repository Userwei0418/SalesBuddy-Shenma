"""Responsive opportunity detail columns; original data and action guards are retained."""
from web_detail_layout import nodes, one, take, wrap


def bindings(tree):
    return sorted((key, value) for node in nodes(tree) for key, value in node['a'].items()
                  if key.startswith(('bind', 'catch')))


def adapt(tree, route, fragment):
    if route != 'pages/customer-assets/index.wxml':
        return tree
    before = bindings(tree)
    page = one(tree, 'actual-page')
    heading = take([page], one([page], 'actual-heading'))
    quick = take([page], one([page], 'fde-quick-actions'))
    summary = take([page], one([page], 'web-op-summary'))
    tabs = take([page], one([page], 'op-detail-tabs'))
    columns = take([page], one([page], 'web-op-columns'))
    # CSS makes these wrappers transparent for asset-only and narrow views, so
    # there is one copy of each guarded native node at every window size.
    aside = wrap('web-op-identity', [heading, quick, summary], tag='aside',
                 **{'aria-label': '商机基本信息'})
    work = wrap('web-op-workarea', [tabs, columns],
                **{'data-web-page-scroll': 'true', 'aria-label': '商机业务详情'})
    page['c'].append(wrap('web-op-detail-frame', [aside, work]))
    if before != bindings(tree):
        raise ValueError('Opportunity columns must preserve every original action')
    return tree
