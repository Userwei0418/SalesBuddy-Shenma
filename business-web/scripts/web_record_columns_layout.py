"""Align record details with the desktop overview/content column pattern.

Native conditions, values and handlers are kept intact.  The CSS only changes
column placement above the desktop breakpoint; no business fields are derived.
"""
from web_detail_layout import one, take
from web_extended_details_layout import bindings


def adapt(tree, route, fragment):
    name = route.split('/')[1] if route.startswith('pages/') else ''
    if name not in {'task-detail', 'risk-detail', 'visit-detail', 'report-detail'}:
        return tree
    before = bindings(tree)
    page = one(tree, 'web-extended-detail')
    page['a']['class'] += ' web-record-detail web-record-' + name
    if name == 'risk-detail':
        body = one([page], 'web-extended-grid')
        main = one([body], 'web-extended-scroll')
        aside = one([body], 'web-extended-aside')
        summary = take([main], one([main], 'summary-card'))
        # Keep the resolved / unresolved condition pair in one parent.
        main['c'].extend(aside['c'])
        aside['c'] = [summary]
        body['c'] = [aside, main]
    elif name == 'visit-detail':
        record = one([page], 'web-visit-record')
        hero = one([record], 'visit-hero')
        hero['c'].append(take([record], one([record], 'fde-note')))
    elif name == 'report-detail':
        main = one([page], 'web-extended-scroll')
        card = next(n for n in main['c'] if isinstance(n, dict)
                    and 'section-card' in n['a'].get('class', '').split())
        card['a']['class'] += ' web-record-report-body'
        card['a']['data-web-page-scroll'] = 'true'
    if before != bindings(tree):
        raise ValueError('Record detail columns changed native handlers: ' + route)
    return tree
