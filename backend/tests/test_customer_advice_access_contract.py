"""Object-level advice affordances default closed independently of customer read."""

from uuid import uuid4

from sales_backend.contracts.detail_reads import CustomerHeader


def header(**access):
    return CustomerHeader(
        id=uuid4(), name='可查看档案的客户', primary_contact=None,
        primary_opportunity=None, read_model='detail_header_v1', **access,
    ).model_dump(mode='json')


def test_legacy_customer_header_does_not_imply_advice_access():
    result = header()
    assert result['can_read_advice'] is False
    assert result['can_request_advice'] is False
    assert result['name'] == '可查看档案的客户'


def test_read_only_advice_access_remains_distinct_from_generation():
    result = header(can_read_advice=True, can_request_advice=False)
    assert result['can_read_advice'] is True
    assert result['can_request_advice'] is False
