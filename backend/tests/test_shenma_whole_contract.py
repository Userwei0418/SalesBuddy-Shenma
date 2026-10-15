"""Shenma-only release contracts and fail-closed sample runner admission."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.api.work_report_feishu import PublishRequest, publication_error
from sales_backend.domain.route_permissions import route_permission
from tests.system.shenma_whole_single_job import MARKER, assert_clone


@pytest.mark.parametrize("confirm", [False, 1, "true", None])
def test_report_feishu_publication_requires_literal_confirmation(confirm):
    with pytest.raises(ValidationError):
        PublishRequest(expected_version=1, revision_id=uuid4(), confirm=confirm)


def test_publication_uses_existing_scoped_actions_and_no_new_privilege():
    assert route_permission("work_report_feishu", "publish") == "work_report.share"
    assert route_permission("work_report_feishu", "status") == "work_report.read"
    assert route_permission("unified_model_key", "rotate_key") == "ai.config_publish"


@pytest.mark.parametrize("sqlstate,status", [("42501", 403), ("22023", 422), ("40001", 409), ("23505", 409)])
def test_publication_errors_do_not_expose_database_values(sqlstate, status):
    result = publication_error(SimpleNamespace(sqlstate=sqlstate, detail="private SQL and body"))
    assert result.status == status
    assert "private" not in result.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("name,marker", [
    ("shenma_sales", MARKER),
    ("shenma_preflight_20261015t123456z", "wrong"),
    ("shenma_preflight_20261015t123456z_extra", MARKER),
    ("other", MARKER),
])
async def test_exact_runner_rejects_wrong_database_or_marker_before_mutation(name, marker):
    connection = SimpleNamespace(fetchrow=AsyncMock(return_value={"datname": name, "marker": marker}),
                                 execute=AsyncMock())
    with pytest.raises(ValueError):
        await assert_clone(connection)
    connection.execute.assert_not_called()


@pytest.mark.asyncio
async def test_exact_runner_accepts_only_the_explicitly_marked_clone():
    name = "shenma_preflight_20261015t123456z"
    connection = SimpleNamespace(fetchrow=AsyncMock(return_value={"datname": name, "marker": MARKER}))
    assert await assert_clone(connection) == name


@pytest.mark.parametrize("raw", [
    {"schema_version": 1, "columns": ["name", "owner"], "filters": {"scope": None},
     "order": "close_date", "group_by": "quarter"},
    {"schema_version": 1, "columns": ["name"], "filters": {}, "order": "quarter_stage"},
    {"schema_version": 3, "columns": ["secret_unknown_column"]},
])
def test_old_or_unknown_saved_view_stays_explicitly_blocked_without_mutating_row(raw):
    from copy import deepcopy

    from sales_backend.repositories.opportunity_views import item
    original = deepcopy(raw)
    row = {"config": raw, "name": "本人旧方案", "version_no": 7}
    result = item(row)
    assert result["config"] is None and result["migration_blocked"] is True
    assert result["migration_notice"]["code"] == "migration_required"
    assert row["config"] == original and row["version_no"] == 7


def test_peer_mode_is_explicit_and_requires_real_and_effective_root(monkeypatch):
    from tests.system import shenma_whole_single_job as runner
    monkeypatch.delenv("SHENMA_PREFLIGHT_PEER_MAINTENANCE", raising=False)
    assert runner.peer_mode() is False
    monkeypatch.setenv("SHENMA_PREFLIGHT_PEER_MAINTENANCE", "1")
    monkeypatch.setattr(runner.os, "getuid", lambda: 501)
    monkeypatch.setattr(runner.os, "geteuid", lambda: 0)
    with pytest.raises(ValueError):
        runner.peer_mode()
    monkeypatch.setattr(runner.os, "getuid", lambda: 0)
    assert runner.peer_mode() is True
    monkeypatch.setenv("SHENMA_PREFLIGHT_PEER_MAINTENANCE", "yes")
    with pytest.raises(ValueError):
        runner.peer_mode()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_peer_connect_restores_root_even_when_handshake_fails(monkeypatch, failure):
    from tests.system import shenma_whole_single_job as runner
    calls = []
    identity = {"uid": 0}
    for key in ("HOME", "USER", "LOGNAME"):
        monkeypatch.setenv(key, "/root" if key == "HOME" else "root")
    monkeypatch.setattr(runner.os, "getuid", lambda: 0)
    monkeypatch.setattr(runner.os, "geteuid", lambda: identity["uid"])
    def seteuid(uid):
        calls.append(uid)
        identity["uid"] = uid
    monkeypatch.setattr(runner.os, "seteuid", seteuid)
    monkeypatch.setattr(runner.pwd, "getpwnam", lambda name: SimpleNamespace(
        pw_uid=123, pw_name="postgres", pw_dir="/var/lib/postgresql"))
    async def connect(dsn):
        assert identity["uid"] == 123
        assert runner.os.environ["HOME"] == "/var/lib/postgresql"
        assert runner.os.environ["USER"] == runner.os.environ["LOGNAME"] == "postgres"
        if failure:
            raise RuntimeError("synthetic handshake failed")
        return "authenticated-maintenance-connection"
    monkeypatch.setattr(runner.asyncpg, "connect", connect)
    if failure:
        with pytest.raises(RuntimeError):
            await runner.connect_maintenance("postgresql://private.invalid/db", use_peer=True)
    else:
        assert await runner.connect_maintenance("postgresql://private.invalid/db", use_peer=True)
    assert calls == [123, 0] and identity["uid"] == 0
    assert runner.os.environ["HOME"] == "/root"
    assert runner.os.environ["USER"] == runner.os.environ["LOGNAME"] == "root"


def test_peer_runtime_identity_drop_clears_groups_before_gid_uid(monkeypatch):
    from tests.system import shenma_whole_single_job as runner
    state = {"uid": 0, "gid": 0, "groups": [0, 1]}
    calls = []
    for key in ("HOME", "USER", "LOGNAME"):
        monkeypatch.setenv(key, "/root" if key == "HOME" else "root")
    cwd = []
    monkeypatch.setattr(runner.os, "chdir", cwd.append)
    for name, field in (("getuid", "uid"), ("geteuid", "uid"),
                        ("getgid", "gid"), ("getegid", "gid"), ("getgroups", "groups")):
        monkeypatch.setattr(runner.os, name, lambda field=field: state[field])
    for name, field in (("setgroups", "groups"), ("setgid", "gid"), ("setuid", "uid")):
        def update(value, name=name, field=field):
            calls.append((name, value))
            state[field] = value
        monkeypatch.setattr(runner.os, name, update)
    monkeypatch.setattr(runner.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=456, pw_gid=789,
        pw_name="shenma-sales", pw_dir="/var/lib/shenma-sales"))
    assert runner.drop_to_runtime() == 456
    assert calls == [("setgroups", []), ("setgid", 789), ("setuid", 456)]
    assert cwd == [runner.PUBLIC_BACKEND]
    assert runner.os.environ["HOME"] == "/var/lib/shenma-sales"
    assert runner.os.environ["USER"] == runner.os.environ["LOGNAME"] == "shenma-sales"
    with pytest.raises(ValueError):
        runner.drop_to_runtime()


@pytest.mark.parametrize("path,category", [
    ("/root/.postgresql/root.crt", "root_home"),
    ("/var/lib/postgresql/.postgresql/root.crt", "postgres_home_probe"),
    ("/etc/shenma-sales/private-keyring.json", "runtime_credential_file"),
    ("/var/lib/shenma-model-key/private.json", "runtime_credential_file"),
    ("/opt/shenma-sales/releases/opaque/backend/module.py", "candidate_release"),
    ("/unknown/private-business-name", "other"),
    (None, "unavailable"),
])
def test_exact_runner_permission_diagnostic_never_exposes_path_or_message(path, category):
    import json

    from tests.system import shenma_whole_single_job as runner
    runner.stage("runtime_connect")
    error = PermissionError(13, "synthetic-secret-message", path)
    result = runner.failure_diagnostic(error)
    assert result["stage"] == "runtime_connect" and result["errno"] == 13
    assert result["errno_name"] == "EACCES" and result["path_category"] == category
    serialized = json.dumps(result)
    assert "synthetic-secret-message" not in serialized
    if path is not None:
        assert path not in serialized


def test_identity_environment_rejects_root_home_before_identity_switch():
    from tests.system import shenma_whole_single_job as runner
    for path in ("/root", "/root/private", "relative-home"):
        with pytest.raises(ValueError):
            runner.identity_environment(SimpleNamespace(pw_name="shenma-sales", pw_dir=path))


def test_exact_claim_keeps_uuid_parameter_types_when_comparing_json_payload():
    import inspect

    from tests.system import shenma_whole_single_job as runner
    source = inspect.getsource(runner.execute_exact)
    # PostgreSQL otherwise infers $2/$5 as text, breaking the same parameters'
    # uuid projection predicates before any queue item is claimed.
    assert "j.payload->>'workspace_id'=($2::uuid)::text" in source
    assert "j.payload->>'user_id'=($5::uuid)::text" in source
    assert "$2::text" not in source and "$5::text" not in source
