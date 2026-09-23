"""Reject malformed test cleanup receipts before reaching any business write."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.maintenance.feishu_test_archive import ArchiveManifest


def manifest_data():
    start = datetime.now(UTC)
    return dict(workspace_id=uuid4(), creator_id=uuid4(), run_id=uuid4(),
                started_at=start, ended_at=start + timedelta(minutes=10),
                reason='Authorized test data retirement',
                records=[dict(kind='customer', id=uuid4(), version=1, creation_request_id=uuid4(), creator_id=uuid4())])


@pytest.mark.parametrize('problem', ['duplicate', 'no_customer', 'two_customers', 'visit_without_opportunity',
                                    'unbounded_time', 'naive_time', 'reverse_time', 'unexpected_input',
                                    'no_receipt', 'no_version'])
def test_rejects_incomplete_or_expanded_archive_manifest(problem):
    body = manifest_data()
    if problem == 'duplicate':
        body['records'] *= 2
    elif problem == 'no_customer':
        body['records'][0]['kind'] = 'contact'
    elif problem == 'two_customers':
        body['records'].append(dict(body['records'][0], id=uuid4()))
    elif problem == 'visit_without_opportunity':
        body['records'].append(dict(body['records'][0], kind='visit', id=uuid4()))
    elif problem == 'unbounded_time':
        body['ended_at'] += timedelta(days=2)
    elif problem == 'naive_time':
        body['started_at'] = body['started_at'].replace(tzinfo=None)
    elif problem == 'reverse_time':
        body['ended_at'] = body['started_at'] - timedelta(seconds=1)
    elif problem == 'unexpected_input':
        body['delete_all_matching'] = True
    elif problem == 'no_receipt':
        del body['records'][0]['creation_request_id']
    else:
        body['records'][0]['version'] = 0
    with pytest.raises(ValidationError):
        ArchiveManifest.model_validate(body)


def test_partial_setup_and_full_exact_receipts_are_supported():
    body = manifest_data()
    partial = ArchiveManifest.model_validate(body)
    assert len(partial.records) == 1
    for kind in ('opportunity', 'visit', 'contact'):
        body['records'].append(dict(body['records'][0], kind=kind, id=uuid4()))
    full = ArchiveManifest.model_validate(body)
    assert len(full.records) == 4
    assert full.prefix == '飞书联调测试-' + body['run_id'].hex[:8]
