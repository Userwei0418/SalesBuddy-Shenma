"""Web composition of four existing forms; source handlers and access checks stay intact."""
from web_detail_layout import nodes, one, take, wrap


def editor_layout(page):
    sheet = one([page], 'editor-sheet')
    head = take([sheet], one([sheet], 'editor-head'))
    actions = take([sheet], one([sheet], 'editor-actions'))
    sheet['c'] = [head, wrap('web-extended-editor-scroll', sheet['c']), actions]
    for node in nodes([sheet]):
        if node['t'] == 'textarea':
            node['a'].pop('auto-height', None)
    for row in nodes([page]):
        if row['a'].get('bindtap') == 'openEditor':
            row['a']['title'] = '{{item.value || item.label}}'


def adapt(tree, route, fragment):
    if route in ('pages/customer-create/index.wxml', 'pages/customer-assign-confirm/index.wxml'):
        creating = route == 'pages/customer-create/index.wxml'
        page = one(tree, 'create-page' if creating else 'confirm-page')
        page['a']['class'] += ' web-extended-form ' + ('web-customer-create' if creating else 'web-customer-assign')
        body = one([page], 'create-body' if creating else 'confirm-body')
        body['a']['class'] += ' web-extended-scroll'
        body['a']['data-web-page-scroll'] = 'true'
        one([page], 'bottom-bar')['a']['class'] += ' web-extended-footer'
        editor_layout(page)
        if not creating:
            one([page], 'hero-title')['a']['title'] = '{{customerName}}'
    elif route == 'pages/customer-edit/index.wxml':
        page = one(tree, 'edit-page')
        page['a']['class'] += ' web-extended-form web-customer-edit'
        form = one([page], 'form-card')
        agent = one([page], 'agent-card')
        footer = one([page], 'submit-bar')
        parent = next(n for n in nodes([page]) if form in n['c'])
        # Keep the original wx:else paired with the loading branch.
        parent['t'] = 'view'
        parent['a']['class'] = 'web-extended-loaded'
        for node in (form, agent, footer):
            take([parent], node)
        parent['c'] = [wrap('web-extended-scroll web-extended-edit-columns', [form, agent], **{'data-web-page-scroll': 'true'}), footer]
        footer['a']['class'] += ' web-extended-footer'
    elif route == 'pages/demo-create/index.wxml':
        pages = [n for n in nodes(tree) if 'demo-page' in n['a'].get('class', '').split()]
        if len(pages) != 2:
            raise ValueError('Review Demo form/detail branches before Web adaptation')
        for page in pages:
            if 'demo-detail-page' in page['a'].get('class', '').split():
                one([page], 'demo-title')['a']['title'] = '{{sceneDetail.name}}'
            page['a']['class'] += ' web-extended-form web-demo-form'
        for node in nodes(tree):
            classes = node['a'].get('class', '').split()
            if any(cls in classes for cls in ('demo-scroll', 'demo-detail-scroll')):
                node['a']['class'] += ' web-extended-scroll'
                node['a']['data-web-page-scroll'] = 'true'
            if 'demo-footer' in classes:
                node['a']['class'] += ' web-extended-footer'
    return tree
