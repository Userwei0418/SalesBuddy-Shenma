"""Desktop summary/list columns using the original guarded nodes and handlers."""
from web_detail_layout import nodes, one, take, wrap


def adapt(tree, route, fragment):
    kinds = {
        'pages/opportunities/index.wxml': 'opportunities',
        'pages/risks/index.wxml': 'risks',
        'components/fde-projects/index.wxml': 'projects',
    }
    if route not in kinds:
        return tree
    original = sorted((key, value) for node in nodes(tree) for key, value in node['a'].items()
                      if key.startswith(('bind', 'catch')))
    kind = kinds[route]
    page = one(tree, 'web-insights-' + kind)
    header = one([page], 'web-insights-header')
    content = one([page], 'web-insights-scroll')
    if kind == 'opportunities':
        controls = [take([header], one([header], 'filter-card'))]
    elif kind == 'risks':
        controls = [take([header], one([header], 'risk-tabs'))]
    else:
        controls = [take([header], one([header], name))
                    for name in ('workbench-opportunity-tools', 'fde-result-note')]
        # Summary quarters and list quarters remain independent native controls.
        foot = one([header], 'quarter-filter-foot')
        # Keep the 1.0.9 statistics-help action inside this footer.
    header['a']['class'] += ' web-collection-aside'
    header['a']['aria-label'] = '页面概况'
    page['a']['class'] += ' web-collection-columns web-collection-' + kind
    content['a']['class'] += ' web-collection-results'
    content['a']['aria-label'] = '列表内容'
    # In narrow windows this wrapper is display:contents. The existing inner
    # scroll host then remains active; on desktop only this outer host scrolls.
    main = wrap('web-collection-main', controls + [content],
                **{'data-web-page-scroll': 'true', 'aria-label': '筛选与列表', 'tabindex': '0'})
    page['c'] = [header, main]
    if original != sorted((key, value) for node in nodes(tree) for key, value in node['a'].items()
                          if key.startswith(('bind', 'catch'))):
        raise ValueError('Collection columns must preserve every original action')
    return tree
