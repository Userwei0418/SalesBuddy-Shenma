"""Real service responses must use delivery state, including publish replays."""
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest
from fastapi import HTTPException

from sales_backend.contracts.weekly_reports import WeeklyList
from sales_backend.services import weekly_reports


def fixture(monkeypatch, status):
    now = datetime(2026, 9, 29, 3, tzinfo=timezone.utc)
    report, event = uuid4(), uuid4()
    statistics = dict(record_count=1, customer_count=1, opportunity_count=0)
    period = dict(start_date='2026-09-16', end_date='2026-09-29', timezone='Asia/Shanghai', date_basis='created_at')
    row = dict(id=report, request_id=uuid4(), status='succeeded', result_status='ready',
        input_snapshot=json.dumps(dict(context={'as_of': now.isoformat()}, statistics=statistics, period=period)),
        original_result={'title': '人工确认周报'}, error_code=None, report_week=date(2026,9,28),
        source_cutoff_at=now, draft_markdown='已核对正文', draft_version=2,
        input_sha256='a'*64, created_at=now, updated_at=now, finished_at=now,
        runtime_metadata={}, feishu_publish_event_id=event, feishu_publish_requested_at=now,
        feishu_publish_snapshot={'draft_version':1})
    receipt = dict(status=status, event_id=str(event), draft_version=1, message_id='om_received' if status=='sent' else None)
    async def fetchval(sql, *args):
        if 'security.weekly_feishu_status' in sql:
            return receipt
        if 'security.publish_weekly_report_feishu' in sql:
            return event
        if 'transaction_timestamp()' in sql:
            return now
        raise AssertionError(sql)
    connection = SimpleNamespace(fetchval=AsyncMock(side_effect=fetchval), fetchrow=AsyncMock(return_value=row),
        fetch=AsyncMock(return_value=[row]), execute=AsyncMock())
    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield connection
    service = weekly_reports.WeeklyReportService(SimpleNamespace(transaction=transaction,settings=None))
    monkeypatch.setattr(weekly_reports, 'require_permission', AsyncMock())
    monkeypatch.setattr(weekly_reports, 'assert_snapshot_access', AsyncMock())
    return service, connection, row, receipt


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['pending','sent','unknown','failed'])
@pytest.mark.parametrize('operation',['publish','read','save','list','replay'])
async def test_every_report_response_preserves_actual_receipt_and_published_version(monkeypatch,status,operation):
    service,connection,row,receipt=fixture(monkeypatch,status)
    actor=SimpleNamespace(workspace_id=str(uuid4()),user_id=str(uuid4()))
    if operation=='publish': result=await service.publish_feishu(actor,str(row['id']),row['draft_version'])
    elif operation=='read': result=await service.read(actor,str(row['id']))
    elif operation=='save': result=await service.save(actor,str(row['id']),row['draft_version'],'后续编辑')
    elif operation=='replay': result=await service.replay(connection,row,row['report_week'])
    else:
        listing=await service.list(actor,20,0)
        # List response validation must not silently discard the receipt.
        result=WeeklyList.model_validate(listing).model_dump()['items'][0]
    assert result['feishu']==receipt
    assert result['feishu']['draft_version']==1 and result['draft_version']==2
    assert any('security.weekly_feishu_status' in call.args[0] for call in connection.fetchval.await_args_list)


@pytest.mark.asyncio
async def test_unpublished_report_does_not_claim_delivery_or_query_event(monkeypatch):
    service,connection,row,_=fixture(monkeypatch,'sent')
    row['feishu_publish_event_id']=None
    result=await service._view(connection,row)
    assert result['feishu']=={'status':'not_published'}
    connection.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_publication_rejects_synthetic_source_with_actionable_conflict(monkeypatch):
    service, connection, row, _ = fixture(monkeypatch, 'pending')
    connection.fetchval.side_effect = asyncpg.InvalidParameterValueError('WEEKLY_SYNTHETIC_TRIAL_SOURCE')
    actor = SimpleNamespace(workspace_id=str(uuid4()), user_id=str(uuid4()))
    with pytest.raises(HTTPException) as error:
        await service.publish_feishu(actor, str(row['id']), row['draft_version'])
    assert error.value.status_code == 409
    assert error.value.detail == 'WEEKLY_SYNTHETIC_TRIAL_SOURCE'
