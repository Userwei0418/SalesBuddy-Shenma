"""Boundary checks for the one-time notification-test archive wrapper."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.maintenance.feishu_notification_test_archive import (
    NotificationTestDescriptor,
    archive,
)
from sales_backend.maintenance.feishu_test_archive import ArchiveRejected


def descriptor():
    return NotificationTestDescriptor(
        company_reference='fixture-company-reference',
        contact_name='fixture-contact',
        customer_name='fixture-customer',
        created_day=date(2026, 9, 19),
        industry='fixture-industry',
        opportunity_name='fixture-opportunity',
        visit_marker='fixture-visit-marker',
    )


@pytest.mark.asyncio
async def test_wrapper_requires_administrator_and_does_not_accept_extra_fields():
    with pytest.raises(ValueError):
        NotificationTestDescriptor.model_validate({**descriptor().model_dump(), 'id': 'unexpected'})
    connection = SimpleNamespace(fetchval=AsyncMock())
    actor = SimpleNamespace(role=SimpleNamespace(value='sales'))
    with pytest.raises(ArchiveRejected, match='WORKSPACE_OR_ROLE_MISMATCH'):
        await archive(connection, actor, descriptor())
    connection.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrapper_surfaces_database_rejection_without_claiming_success():
    connection = SimpleNamespace(fetchval=AsyncMock(return_value={'code': 'NOTIFICATION_TEST_DESCRIPTOR_NOT_AUTHORIZED'}))
    actor = SimpleNamespace(role=SimpleNamespace(value='administrator'))
    with pytest.raises(ArchiveRejected, match='NOTIFICATION_TEST_DESCRIPTOR_NOT_AUTHORIZED'):
        await archive(connection, actor, descriptor())
    assert connection.fetchval.await_count == 1
