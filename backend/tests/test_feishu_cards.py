from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from sales_backend.domain.feishu_sync.cards import notification_card
from sales_backend.domain.feishu_sync.config import SyncConfig
from tests.test_feishu_sync_policy import payload


def test_followup_card_keeps_v3_business_context_and_safe_detail_link():
    data = payload()
    data['mappings']['visit'] = {**data['mappings'].pop('customer'), 'table_id': 'tblVisits'}
    config = SyncConfig.model_validate(data)
    values = {'customer_name': '客户A', 'opportunity_name': '项目B', 'owner_name': '销售C',
              'interaction_at': '2026-09-19T01:30:00+00:00', 'contact_name': '联系人D',
              'content': '<at id=all>hello</at>' + '内容' * 3000, 'next_action': '下周评审'}
    card = notification_card('visit', values, config, 'recRecord')
    rows = [e['text']['content'] for e in card['elements'] if e['tag'] == 'div']
    text = '\n'.join(rows)
    for expected in ['客户A', '项目B', '销售C', '2026-09-19 09:30', '联系人D', '沟通内容', '下一步计划：下周评审']:
        assert expected in text
    assert len(text) < 2500 and '完整内容请查看详情' in text
    assert all(e['text']['tag'] == 'plain_text' for e in card['elements'] if e['tag'] == 'div')
    action = card['elements'][-1]['actions'][0]
    link = urlsplit(action['url'])
    assert link.hostname == 'example.feishu.cn' and link.path == '/base/testBase'
    assert parse_qs(link.query) == {'table': ['tblVisits'], 'record': ['recRecord']}


@pytest.mark.parametrize('url', ['http://example.feishu.cn/base/testBase',
    'https://example.feishu.cn.evil.com/base/testBase', 'https://user:pass@example.feishu.cn/base/testBase',
    'https://example.feishu.cn/base/other', 'https://example.feishu.cn/base/testBase?secret=bad'])
def test_notification_detail_url_cannot_redirect_to_unrelated_or_credential_url(url):
    data = payload()
    data['base_url'] = url
    with pytest.raises(ValidationError):
        SyncConfig.model_validate(data)


def test_notifications_require_detail_url_but_paused_data_only_configuration_remains_compatible():
    data = payload()
    data.pop('base_url')
    with pytest.raises(ValidationError):
        SyncConfig.model_validate(data)
    data['notification']['enabled'] = False
    SyncConfig.model_validate(data)
