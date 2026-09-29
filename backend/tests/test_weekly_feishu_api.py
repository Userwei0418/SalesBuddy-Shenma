"""HTTP contract checks for explicit weekly-report Feishu publication.

The route tests keep the persistence/service boundary mocked: PostgreSQL-backed
publication and snapshot freezing are covered by the disposable integration
suite.  These tests verify the user-visible confirmation endpoint, route-level
permissions, validation, and status polling contract.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from sales_backend.api import weekly_reports
from sales_backend.api.dependencies import get_database
from sales_backend.api.management_dependencies import get_password_identity
from sales_backend.api.permission_gate import enforce_route_permission
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.services.weekly_reports import WeeklyReportService
from tests.authorization_fixtures import install_http_authorization


REPORT_ID = "10000000-0000-4000-8000-000000000001"


def _detail(*, feishu=None):
    now = datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc).isoformat()
    return {
        "id": REPORT_ID,
        "request_id": "20000000-0000-4000-8000-000000000001",
        "report_week": "2026-09-28",
        "report_week_end": "2026-10-04",
        "source_cutoff_at": now,
        "snapshot_at": now,
        "status": "succeeded",
        "result_status": "ready",
        "period": {"start_date": "2026-09-28", "end_date": "2026-10-04",
                   "timezone": "Asia/Shanghai", "date_basis": "created_at"},
        "statistics": {"record_count": 2, "customer_count": 1, "opportunity_count": 1},
        "input_sha256": "a" * 64,
        "draft_version": 1,
        "error_code": None,
        "created_at": now,
        "updated_at": now,
        "finished_at": now,
        "runtime_snapshot_verified": False,
        "actual_snapshot_id": None,
        "title": "本周销售周报",
        "body_markdown": "人工核对后的正文",
        "original_result": {"status": "ready"},
        "draft_source": "agent",
        "draft_references_validated": True,
        "runtime_metadata": {},
        "feishu": feishu or {"status": "pending", "event_id": "30000000-0000-4000-8000-000000000001"},
    }


@pytest.fixture
def weekly_http(monkeypatch):
    """App with the same route permission gate and a fake service boundary."""
    app = FastAPI(dependencies=[Depends(enforce_route_permission)])
    app.include_router(weekly_reports.router)
    actor = ActorContext(
        workspace_id=str(uuid4()), user_id=str(uuid4()), role=RoleCode.MANAGER,
        data_scope=DataScope.WORKSPACE,
    )
    grants = {"access.console": "workspace", "weekly_report.edit": "self",
              "weekly_report.read": "self"}
    database, identity = install_http_authorization(monkeypatch, app, actor, grants)
    database.settings = SimpleNamespace()
    @app.exception_handler(PermissionError)
    async def denied(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)
    app.dependency_overrides[get_password_identity] = lambda: identity
    app.dependency_overrides[get_database] = lambda: database
    publish = AsyncMock(return_value=_detail())
    status = AsyncMock(return_value={"status": "sent", "event_id": "30000000-0000-4000-8000-000000000001",
                                     "message_id": "om_weekly_test"})
    monkeypatch.setattr(WeeklyReportService, "publish_feishu", publish)
    monkeypatch.setattr(WeeklyReportService, "feishu_status", status)
    return TestClient(app), identity, grants, publish, status


def test_publish_requires_saved_version_and_returns_pending_event(weekly_http):
    client, _identity, _grants, publish, _status = weekly_http
    response = client.post(f"/api/v1/web/weekly-reports/{REPORT_ID}/feishu/publish",
                           json={"expected_version": 1})
    assert response.status_code == 200, response.text
    assert response.json()["feishu"]["status"] == "pending"
    publish.assert_awaited_once()
    actor, report_id, expected_version = publish.await_args.args
    assert report_id == REPORT_ID and expected_version == 1
    assert actor.workspace_id


def test_publish_payload_rejects_stale_or_extra_fields_before_service(weekly_http):
    client, _identity, _grants, publish, _status = weekly_http
    for payload in ({"expected_version": 0}, {"expected_version": 1, "confirmed": True}):
        response = client.post(f"/api/v1/web/weekly-reports/{REPORT_ID}/feishu/publish", json=payload)
        assert response.status_code == 422, response.text
    publish.assert_not_awaited()


def test_status_endpoint_is_read_only_and_exposes_provider_receipt(weekly_http):
    client, _identity, _grants, _publish, status = weekly_http
    response = client.get(f"/api/v1/web/weekly-reports/{REPORT_ID}/feishu")
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "sent", "event_id": "30000000-0000-4000-8000-000000000001",
                               "message_id": "om_weekly_test"}
    status.assert_awaited_once()
    assert status.await_args.args[1] == REPORT_ID


def test_route_permission_blocks_publish_without_weekly_edit_grant(weekly_http):
    client, _identity, grants, publish, _status = weekly_http
    grants.pop("weekly_report.edit")
    response = client.post(f"/api/v1/web/weekly-reports/{REPORT_ID}/feishu/publish",
                           json={"expected_version": 1})
    assert response.status_code == 403, response.text
    publish.assert_not_awaited()


def test_route_permission_allows_status_with_read_only_grant(weekly_http):
    client, _identity, grants, _publish, status = weekly_http
    grants.pop("weekly_report.edit")
    response = client.get(f"/api/v1/web/weekly-reports/{REPORT_ID}/feishu")
    assert response.status_code == 200, response.text
    status.assert_awaited_once()
