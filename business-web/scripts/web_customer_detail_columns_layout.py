"""Group existing customer identity and work content without changing native rules."""
from web_detail_layout import nodes, one, take, wrap


def adapt(tree, route, fragment):
    if route not in ('pages/customers/index.wxml', 'pages/customer-detail/index.wxml'):
        return tree
    bindings = sorted((k, v) for n in nodes(tree) for k, v in n['a'].items() if k.startswith(('bind', 'catch', 'wx:')))
    embedded = route == 'pages/customers/index.wxml'
    root = one(tree, 'web-customer-workspace' if embedded else 'web-standalone-customer')
    root['a']['class'] += ' web-customer-detail-layout'
    header = take([root], one([root], 'web-detail-header'))
    metrics = take([root], one([root], 'web-customer-kpis'))
    tabs = take([root], one([root], 'detail-tabs'))
    content = take([root], one([root], 'detail-scroll' if embedded else 'web-detail-columns'))
    root['c'].append(wrap('web-customer-detail-frame', [
        wrap('web-customer-identity', [header, metrics], tag='aside'),
        wrap('web-customer-primary', [tabs, content])
    ]))
    assert bindings == sorted((k, v) for n in nodes(tree) for k, v in n['a'].items() if k.startswith(('bind', 'catch', 'wx:'))), 'Customer column layout changed native bindings'
    return tree
