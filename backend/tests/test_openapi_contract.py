"""守住 API 契约：spec 不再漂移，前端依赖的端点不被误删或改名。

openapi/openapi.yaml 曾手工维护到严重失真——声明了 14 条线上不存在的路径，
又漏了 15 条真实存在的。现在它由 scripts/export_openapi.py 从应用生成，
本文件保证它不再腐化。
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from export_openapi import SPEC_PATH, render  # noqa: E402

# 小程序实际请求的端点，取自 frontend/miniprogram/utils/apiClient.js
# 与 backend/clients/miniprogram/apiClient.js。少一条前端就会 404。
FRONTEND_ENDPOINTS = (
    "/api/v1/agent/runs/{run_id}",
    "/api/v1/assistant/home",
    "/api/v1/audio/transcriptions",
    "/api/v1/auth/logout",
    "/api/v1/auth/refresh",
    "/api/v1/auth/session",
    "/api/v1/conversations",
    "/api/v1/conversations/{conversation_id}/messages",
    "/api/v1/customers",
    "/api/v1/customers/claim-pool",
    "/api/v1/customers/{customer_id}",
    "/api/v1/customers/{customer_id}/assignments",
    "/api/v1/customers/{customer_id}/opportunities",
    "/api/v1/directory/members",
    "/api/v1/directory/task-assignees",
    "/api/v1/notifications",
    "/api/v1/notifications/{notification_id}/read",
    "/api/v1/opportunities",
    "/api/v1/profile/evaluation",
    "/api/v1/profile/sales-growth",
    "/api/v1/profile/sales-growth/review",
    "/api/v1/profile/team-members/{account_code}/sales-growth",
    "/api/v1/risks",
    "/api/v1/risks/{risk_id}",
    "/api/v1/risks/{risk_id}/resolve",
    "/api/v1/tasks",
    "/api/v1/tasks/customers",
    "/api/v1/tasks/opportunities",
    "/api/v1/tasks/recipients",
    "/api/v1/tasks/{task_id}",
    "/api/v1/tasks/{task_id}/events",
    "/api/v1/visits",
    "/api/v1/visits/form-schema",
    "/api/v1/workbench",
)


def test_committed_spec_matches_the_application() -> None:
    assert SPEC_PATH.exists(), "openapi/openapi.yaml 缺失，请运行 scripts/export_openapi.py"
    assert SPEC_PATH.read_text() == render(), (
        "openapi/openapi.yaml 与应用不一致，"
        "请运行 uv run --extra dev python scripts/export_openapi.py"
    )


def test_every_endpoint_the_miniprogram_calls_still_exists() -> None:
    from sales_backend.main import app

    live = set(app.openapi()["paths"])
    missing = [path for path in FRONTEND_ENDPOINTS if path not in live]
    assert not missing, f"小程序依赖的端点已不存在，前端会 404: {missing}"
