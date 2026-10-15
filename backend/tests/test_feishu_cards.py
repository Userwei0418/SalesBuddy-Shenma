from copy import deepcopy
import json
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from sales_backend.domain.feishu_sync.cards import notification_card
from sales_backend.domain.feishu_sync.config import SyncConfig
from sales_backend.domain.feishu_sync.projection import project
from sales_backend.domain.opportunities import differences
from tests.test_feishu_sync_policy import opportunity_payload, payload
from tests.test_feishu_sync_service import Connection, setup


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


@pytest.mark.parametrize(('amount', 'currency', 'expected'), [
    (1_000_000_000, 'CNY', '1,000,000,000元（10亿元）'),
    (Decimal('123456789.12'), 'CNY', '123,456,789.12元（1.2345678912亿元）'),
    ('1234500.00', 'CNY', '1,234,500元（123.45万元）'),
    ('1e4', 'cny', '10,000元（1万元）'),
    (Decimal('9999.99'), 'CNY', '9,999.99元'),
    (0, 'CNY', '0元'),
    (Decimal('-0.00'), 'CNY', '0元'),
    ('-12345.60', 'CNY', '-12,345.6元（-1.23456万元）'),
    (1_000_000_000, 'USD', '1,000,000,000 USD'),
    (0, 'EUR', '0 EUR'),
    (10000, 'XYZ', '10,000 XYZ'),
    (10000, None, '10,000（币种未填写）'),
    (10000, '', '10,000（币种未填写）'),
    ('not-a-number', 'CNY', '金额格式异常'),
    (True, 'CNY', '金额格式异常'),
    (float('inf'), 'CNY', '金额格式异常'),
    (float('-inf'), 'CNY', '金额格式异常'),
    (float('nan'), 'CNY', '金额格式异常'),
    (Decimal('sNaN'), 'CNY', '金额格式异常'),
    ('1e999999', 'CNY', '金额超出显示范围'),
    ('1e-999999', 'CNY', '金额超出显示范围'),
])
def test_opportunity_card_formats_amount_without_changing_units_or_source(amount, currency, expected):
    values = {'amount': amount, 'currency': currency}
    card = notification_card('opportunity', values, SyncConfig.model_validate(payload()), None)
    rows = [e['text']['content'] for e in card['elements']]
    assert f'金额：{expected}' in rows
    assert values['amount'] is amount
    assert values['currency'] == currency
    assert all(e['text']['tag'] == 'plain_text' for e in card['elements'])


@pytest.mark.parametrize('amount', [None, ''])
def test_opportunity_card_missing_amount_is_not_shown_as_zero(amount):
    card = notification_card('opportunity', {'amount': amount, 'currency': 'CNY'},
                             SyncConfig.model_validate(payload()), None)
    assert not any(e['text']['content'].startswith('金额：') for e in card['elements'])


def test_opportunity_card_does_not_truncate_significant_digits_of_large_finite_amount():
    card = notification_card('opportunity', {'amount': Decimal('1e59'), 'currency': 'CNY'},
                             SyncConfig.model_validate(payload()), None)
    row = next(e['text']['content'] for e in card['elements'] if e['text']['content'].startswith('金额：'))
    assert row == f"金额：100{',000' * 19}元（1{',000' * 17}亿元）"


@pytest.mark.parametrize(('values', 'expected'), [
    ({'stage': 'identified', 'probability': 10}, '意向沟通－10%'),
    ({'stage': 'qualified', 'probability': 30}, '商机确认－30%'),
    ({'stage': 'solution', 'probability': 50}, '方案沟通－50%'),
    ({'stage': 'proposal', 'probability': 70}, '商务谈判－70%'),
    ({'stage': 'negotiation', 'probability': 90}, '客户签约－90%'),
    ({'stage': 'won', 'status': 'won', 'probability': 100}, '赢单 Won－100%'),
    ({'stage': 'lost', 'status': 'lost', 'probability': 0}, '丢单 Lost'),
    ({'stage': 'qualified'}, '商机确认－30%'),
    ({'stage': 'lost'}, '丢单 Lost'),
    ({'stage': None, 'probability': 70}, '商务谈判－70%'),
    ({'stage': 'future_stage'}, 'future_stage'),
    ({'stage': 'future_stage', 'probability': 0}, 'future_stage'),
    ({'stage': 'future_stage', 'probability': Decimal('sNaN')}, 'future_stage'),
    ({'stage': None}, None),
    ({'stage': ''}, None),
])
def test_opportunity_card_uses_existing_stage_labels_and_preserves_unknown_codes(values, expected):
    original = deepcopy(values)
    card = notification_card('opportunity', values, SyncConfig.model_validate(payload()), None)
    rows = [e['text']['content'] for e in card['elements']]
    if expected is None:
        assert not any(row.startswith('阶段：') for row in rows)
    else:
        assert f'阶段：{expected}' in rows
    assert values.keys() == original.keys()
    assert str(values) == str(original)


def test_opportunity_card_presentation_keeps_base_projection_in_original_yuan_and_stage_code():
    raw = {'id': 'opportunity-1', 'amount': Decimal('1000000000.00'), 'currency': 'CNY',
           'stage_code': 'qualified', 'probability': 30, 'status': 'open'}
    original = deepcopy(raw)
    projected = project('opportunity', raw, {'amount', 'currency', 'stage', 'probability', 'status'})
    frozen_projection = deepcopy(projected)
    card = notification_card('opportunity', projected, SyncConfig.model_validate(payload()), None)
    assert '金额：1,000,000,000元（10亿元）' in [e['text']['content'] for e in card['elements']]
    assert projected == frozen_projection
    assert projected['amount'] == Decimal('1000000000.00')
    assert projected['stage'] == 'qualified'
    assert raw == original


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['pending', 'sent'])
async def test_frozen_opportunity_notification_keeps_original_payload_and_does_not_regenerate(status):
    service, event, client = setup()
    config, _ = service.client({})
    event.update(object_kind='opportunity', notification_planned=True)
    frozen_card = {'elements': [{'tag': 'div', 'text': {'tag': 'plain_text',
                    'content': '金额：1000000000\n阶段：qualified'}}]}
    service.repository.deliveries.return_value = [
        {'status': status, 'chat_id': 'oc_fixture', 'payload': frozen_card, 'dedupe_key': 'frozen-fixture'},
    ]
    with patch('sales_backend.services.feishu_sync.notification_card',
               side_effect=AssertionError('Frozen notifications must not be regenerated')):
        await service.notify(Connection(), event, config,
            {'record_status': '有效', 'amount': 1_000_000_000, 'currency': 'CNY', 'stage': 'qualified'},
            {}, client)
    service.repository.freeze_delivery.assert_not_awaited()
    service.repository.mark_planned.assert_not_awaited()
    if status == 'pending':
        client.send_card.assert_awaited_once_with('oc_fixture', frozen_card, 'frozen-fixture')
    else:
        client.send_card.assert_not_awaited()


def update_snapshot(**changes):
    return {"schema_version": 1, "change_id": "00000000-0000-0000-0000-000000000006",
            "opportunity_name": "本地部署项目", "customer_name": "隔离客户", "owner_name": "当前负责人",
            "department_id": None, "actor_name": "修改人甲", "changed_at": "2026-09-27T01:30:00+00:00",
            "currency": "CNY", "changes": [{"label": "跟进计划", "before": "周一沟通", "after": "周二拜访"}], **changes}


def update_card(snapshot, record_id="recUpdate"):
    from sales_backend.domain.feishu_sync.cards import opportunity_update_card
    return opportunity_update_card(snapshot, SyncConfig.model_validate(opportunity_payload()), record_id)


def card_text(card):
    return "\n".join(e["text"]["content"] for e in card["elements"] if e["tag"] == "div")


def test_update_card_displays_frozen_actor_china_time_and_before_after_plain_text():
    snapshot = update_snapshot(actor_name="<at id=all>修改人</at>")
    original = deepcopy(snapshot)
    card = update_card(snapshot)
    assert card["header"]["title"]["content"] == "商机更新"
    text = card_text(card)
    for expected in ["本地部署项目", "隔离客户", "当前负责人", "修改人：<at id=all>修改人</at>",
                     "2026-09-27 09:30", "北京时间", "跟进计划", "修改前：周一沟通", "修改后：周二拜访"]:
        assert expected in text
    assert all(e["text"]["tag"] == "plain_text" for e in card["elements"] if e["tag"] == "div")
    query = parse_qs(urlsplit(card["elements"][-1]["actions"][0]["url"]).query)
    assert query == {"table": ["tblOpportunities"], "record": ["recUpdate"]}
    assert snapshot == original


def test_update_card_uses_exact_audit_wan_values_when_amount_is_corrected_downward():
    before = {"amount": Decimal("1000000000.00"), "probability": 10, "status": "open"}
    after = {**before, "amount": Decimal("12345.67")}
    old_forecasts = [{"year": 2026, "quarter": 4, "recognized_amount": Decimal("1000000000.00"), "collection_amount": None}]
    new_forecasts = [{"year": 2026, "quarter": 4, "recognized_amount": Decimal("12345.67"), "collection_amount": Decimal("0")}]
    changes = differences(before, after, old_forecasts, new_forecasts)
    snapshot = update_snapshot(changes=changes)
    original = deepcopy(snapshot)
    text = card_text(update_card(snapshot))
    assert text.count("修改前：1,000,000,000元（10亿元）") == 2
    assert text.count("修改后：12,345.67元（1.234567万元）") == 2
    assert "2026 Q4 预测含税确收" in text and "2026 Q4 预测回款" in text
    assert "修改前：未填写" in text and "修改后：0元" in text
    assert "100,000元" not in text and "123,456,700" not in text
    assert snapshot == original


def test_update_card_translates_channel_preserves_stage_and_foreign_currency():
    changes = [
        {"label": "销售渠道", "before": "direct", "after": "partner"},
        {"label": "商机阶段", "before": "意向沟通－10%", "after": "商机确认－30%"},
        {"label": "ACV（万元）", "before": "1", "after": "1.234567"},
    ]
    text = card_text(update_card(update_snapshot(changes=changes, currency="USD")))
    assert "修改前：直销" in text and "修改后：合作伙伴" in text
    assert "意向沟通－10%" in text and "商机确认－30%" in text
    assert "修改前：10,000 USD" in text and "修改后：12,345.67 USD" in text
    assert "元" not in text


@pytest.mark.parametrize("value,expected", [(None, "未填写"), ("", "未填写"), ("未填写", "未填写"),
                                           ("NaN", "金额格式异常"), ("1e999999", "金额超出显示范围")])
def test_update_card_handles_missing_or_invalid_audit_amount_without_inventing_zero(value, expected):
    card = update_card(update_snapshot(changes=[{"label": "ACV（万元）", "before": value, "after": "1"}]))
    assert f"修改前：{expected}" in card_text(card)


def test_update_card_bounds_total_utf8_size_without_shortening_amount_digits():
    changes = [{"label": "ACV（万元）", "before": "10000000000000000.000001", "after": "1.234567"}]
    changes += [{"label": "计划" + "甲" * 300, "before": "前" * 10000, "after": "后" * 10000} for _ in range(100)]
    card = update_card(update_snapshot(changes=changes, opportunity_name="商" * 10000, customer_name="客" * 10000))
    text = card_text(card)
    assert "修改前：100,000,000,000,000,000,000.01元（1,000,000,000,000.0000000001亿元）" in text
    assert "修改后：12,345.67元（1.234567万元）" in text
    assert "完整变更见系统商机变更记录" in text
    assert card["elements"][-1]["actions"][0]["text"]["content"] == "查看当前商机"
    assert len(json.dumps(card, ensure_ascii=False).encode("utf-8")) <= 24000
