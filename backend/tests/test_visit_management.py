from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sales_backend.api import operations_visits
from sales_backend.contracts.visit_management import visit_management_filters
from sales_backend.repositories.visit_management import VisitManagementRepository


@pytest.mark.parametrize('changes', [
    {'date_from': date(2026, 10, 1), 'date_to': date(2026, 9, 1)},
    {'date_to': date.max}, {'score_min': 85, 'score_max': 60},
])
def test_filter_invalid_intervals(changes):
    args = dict(q='', score_min=None, score_max=None)
    args.update(changes)
    with pytest.raises(HTTPException) as raised:
        visit_management_filters(**args)
    assert raised.value.status_code == 422


@pytest.mark.asyncio
async def test_all_management_reads_request_readonly_consistent_snapshots(monkeypatch):
    observed = []
    identity = SimpleNamespace(actor=object())
    marker = object()

    class Database:
        @asynccontextmanager
        async def transaction(self, actor, **options):
            assert actor is identity.actor
            observed.append(options)
            yield marker

    async def read(self, conn, *args, **kwargs):
        assert conn is marker
        return {'result': True}

    for method in ('page', 'recorders', 'detail'):
        monkeypatch.setattr(VisitManagementRepository, method, read)
    database = Database()
    assert await operations_visits.visits({}, 50, 0, identity, database) == {'result': True}
    assert await operations_visits.recorders({}, '', 50, 0, identity, database) == {'result': True}
    assert await operations_visits.detail('6ea4c402-8789-45bd-8c3e-61a8cdc5f1a4', identity, database) == {'result': True}
    assert observed == [{'readonly': True, 'isolation': 'repeatable_read'}] * 3


@pytest.mark.asyncio
async def test_invisible_detail_is_not_found(monkeypatch):
    class Database:
        @asynccontextmanager
        async def transaction(self, *args, **kwargs):
            yield object()

    async def absent(*args):
        raise LookupError('记录不存在或不可见')

    monkeypatch.setattr(VisitManagementRepository, 'detail', absent)
    with pytest.raises(HTTPException) as raised:
        await operations_visits.detail('6ea4c402-8789-45bd-8c3e-61a8cdc5f1a4', SimpleNamespace(actor=None), Database())
    assert raised.value.status_code == 404
