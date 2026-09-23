"""Plain-text cards matching the useful fields of CRM-V3 follow-up notifications."""
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo


def notification_card(kind, values, config, record_id):
    title = {'customer': '新增客户', 'opportunity': '新增商机', 'visit': '新增跟进记录'}.get(kind, '业务更新')
    definitions = {
        'customer': [('name', '客户'), ('owner_name', '负责人'), ('department_name', '部门'),
                     ('industry', '行业'), ('needs', '客户需求'), ('next_action', '下一步计划')],
        'opportunity': [('name', '商机'), ('customer_name', '客户'), ('owner_name', '负责人'),
                        ('amount', '金额'), ('currency', '币种'), ('stage', '阶段'),
                        ('expected_close_date', '预计关单')],
        'visit': [('customer_name', '客户'), ('opportunity_name', '商机'), ('partner_name', '伙伴'),
                  ('owner_name', '填写人'), ('interaction_at', '跟进日期'), ('contact_name', '对接人'),
                  ('content', '沟通内容'), ('next_action', '下一步计划')],
    }
    elements = []
    for field, label in definitions.get(kind, []):
        value = values.get(field)
        if value is None or value == '':
            continue
        if field == 'interaction_at':
            try:
                parsed = datetime.fromisoformat(str(value))
                if parsed.tzinfo:
                    parsed = parsed.astimezone(ZoneInfo('Asia/Shanghai'))
                value = parsed.strftime('%Y-%m-%d %H:%M')
            except ValueError:
                pass
        limit = 1200 if field in {'content', 'needs'} else 800 if field == 'next_action' else 120
        value = str(value)
        value = value if len(value) <= limit else value[:limit] + '…（完整内容请查看详情）'
        # User text cannot become mentions, Markdown links or executable card actions.
        elements.append({'tag': 'div', 'text': {'tag': 'plain_text', 'content': f'{label}：{value}'}})
    if record_id and config.base_url and kind in config.mappings:
        url = config.base_url + '?' + urlencode({'table': config.mappings[kind].table_id, 'record': record_id})
        elements.append({'tag': 'action', 'actions': [{'tag': 'button', 'type': 'primary', 'url': url,
                        'text': {'tag': 'plain_text', 'content': '查看详情'}}]})
    if not elements:
        elements = [{'tag': 'div', 'text': {'tag': 'plain_text', 'content': title}}]
    return {'config': {'wide_screen_mode': True},
            'header': {'template': 'blue', 'title': {'tag': 'plain_text', 'content': title}},
            'elements': elements}
