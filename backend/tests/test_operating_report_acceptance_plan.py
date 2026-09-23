"""Business validation approval cannot silently expand report scope."""

import copy
import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts" / "acceptance"))
    live = importlib.import_module("operating_report_business_live")
    operator = importlib.import_module("operating_report_operator")
    plan = {
        "agent_id": live.AGENT,
        "workspace_id": live.WORKSPACE,
        "cases": list(live.CASES),
        "maximum_report_cards": 6,
        "approval_status": "approved",
        "publication_api_live_enable_authorized": True,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "actors": {
            account: {"role": role, "user_id": f"00000000-0000-0000-0000-{i:012d}"}
            for i, (account, role) in enumerate(live.ACCOUNTS.items(), start=1)
        },
    }
    return live, operator, plan


@pytest.mark.parametrize(
    "change",
    ["pending", "missing_publication", "expired", "extra_case", "extra_actor", "duplicate_actor", "wrong_role"],
)
def test_invalid_authorization_fails_before_any_session(setup, change):
    live, _, plan = setup
    if change == "pending":
        plan["approval_status"] = "pending"
    elif change == "missing_publication":
        plan["publication_api_live_enable_authorized"] = False
    elif change == "expired":
        plan["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    elif change == "extra_case":
        plan["cases"].append("extra")
    elif change == "extra_actor":
        plan["actors"]["XS002"] = plan["actors"]["XS001"]
    elif change == "duplicate_actor":
        plan["actors"]["ZJ001"]["user_id"] = plan["actors"]["XS001"]["user_id"]
    elif change == "wrong_role":
        plan["actors"]["XS001"]["role"] = "manager"
    with pytest.raises(AssertionError):
        live.validate_plan(plan, "normal-sales", live=True)


def test_modes_preserve_five_routes_and_keep_faults_out_of_production(setup):
    live, operator, plan = setup
    prior = {
        live.WORKSPACE: {"capabilities": {key: {"enabled": True, "rollout": "production"} for key in operator.PRIOR}}
    }
    original = copy.deepcopy(prior)
    for mode, account in live.CASES.items():
        value = operator.configuration(plan, mode, prior)
        caps = value[live.WORKSPACE]["capabilities"]
        report = caps["operating_report"]
        assert {key: caps[key] for key in operator.PRIOR} == original[live.WORKSPACE]["capabilities"]
        if mode == "production":
            assert report == {"enabled": True, "rollout": "production"}
        else:
            assert report["user_ids"] == [plan["actors"][account]["user_id"]]
            assert report["enabled"] == (mode != "off")
            assert report["block_platform_requests"] == (mode == "blocked")
        assert prior == original
    prior[live.WORKSPACE]["capabilities"]["today_tasks"]["enabled"] = False
    with pytest.raises(AssertionError):
        operator.configuration(plan, "normal-sales", prior)
