"""Desktop detail frames: move native nodes without changing business conditions."""
from web_detail_layout import nodes, one, take, wrap

ROUTES = {'customer-assets', 'visit-confirm', 'visit-detail', 'task-detail', 'risk-detail', 'report-detail'}


def bindings(tree):
    return sorted((key, value) for node in nodes(tree) for key, value in node['a'].items()
                  if key.startswith(('bind', 'catch')))


def scroll(children, cls=''):
    return wrap('web-extended-scroll ' + cls, children, **{'data-web-page-scroll': 'true'})


def adapt(tree, route, fragment):
    name = route.split('/')[1] if route.startswith('pages/') else ''
    if name not in ROUTES:
        return tree
    before = bindings(tree)
    if name in ('task-detail', 'risk-detail'):
        page = one(tree, 'detail-page')
        page['a']['class'] += ' web-extended-detail'
        body = one([page], 'detail-body')
        body['a']['class'] += ' web-extended-grid'
        body['a']['data-web-page-scroll'] = 'true'
        if name == 'task-detail':
            info = take([body], one([body], 'info-card'))
            body['c'] = [scroll(body['c']), wrap('web-extended-aside', [info], tag='aside')]
        else:
            # Move the complete if/else pair together; never detach permission checks.
            aside_classes = {'action-card', 'resolved-card', 'resolution-card'}
            aside = [node for node in body['c'] if isinstance(node, dict)
                     and aside_classes.intersection(node['a'].get('class', '').split())]
            for node in aside:
                body['c'].remove(node)
            body['c'] = [scroll(body['c']), wrap('web-extended-aside', aside, tag='aside')]
    elif name == 'visit-detail':
        page = one(tree, 'visit-page')
        page['a']['class'] += ' web-extended-detail'
        content = next(n for n in page['c'] if isinstance(n, dict) and n['a'].get('wx:elif') == '{{visit}}')
        content['t'] = 'view'
        content['a']['class'] = 'web-visit-record'
        hero = take([content], one([content], 'visit-hero'))
        info = next(n for n in content['c'] if isinstance(n, dict) and any(
            isinstance(c, dict) and 'field-grid' in c['a'].get('class', '').split() for c in n['c']))
        content['c'].remove(info)
        note = next(n for n in content['c'] if isinstance(n, dict) and n['a'].get('class') == 'readonly-note')
        content['c'].remove(note)
        fde = take(tree, one(tree, 'fde-note'))
        content['c'] = [hero, fde, wrap('web-extended-grid', [scroll(content['c']), wrap('web-extended-aside', [info], tag='aside')], **{'data-web-page-scroll': 'true'}), note]
    elif name == 'report-detail':
        page = one(tree, 'report-page')
        page['a']['class'] += ' web-extended-detail'
        hero = take([page], one([page], 'report-hero'))
        page['c'] = [hero, scroll(page['c'])]
    elif name == 'visit-confirm':
        page = one(tree, 'page')
        page['a']['class'] += ' web-extended-detail'
        content = next(n for n in page['c'] if isinstance(n, dict) and 'wx:else' in n['a'])
        content['t'] = 'view'
        content['a']['class'] = 'web-review-record'
        hero = take([content], one([content], 'hero'))
        steps = take([content], one([content], 'flow-steps'))
        footer = take([content], one([content], 'footer'))
        content['c'] = [hero, steps, scroll(content['c']), footer]
    elif name == 'customer-assets':
        page = one(tree, 'actual-page')
        page['a']['class'] += ' web-extended-detail'
        one([page], 'web-op-main')['a']['data-web-page-scroll'] = 'true'
        one([page], 'web-op-columns')['a']['data-web-page-scroll'] = 'true'
    if before != bindings(tree):
        raise ValueError('Extended detail layout changed native handlers: ' + route)
    return tree
