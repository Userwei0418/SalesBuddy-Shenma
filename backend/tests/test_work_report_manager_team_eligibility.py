"""Execute the real selector SQL in temporary PostgreSQL with synthetic grants.

This checks role qualification and subject-grant intersections, not production RLS.
"""
import getpass
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import asyncpg
import pytest
import pytest_asyncio

from sales_backend.contracts.work_report import ShareTargets
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.work_report import WorkReportError
from sales_backend.services import review_scope, work_report_overview
from sales_backend.services import work_report_access as access

W, U, A, B = [str(UUID(int=n)) for n in (1, 2, 3, 4)]


@pytest.fixture(scope="module")
def postgres():
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip("temporary PostgreSQL binaries unavailable")
    with tempfile.TemporaryDirectory(prefix="sb-manager-", dir="/tmp") as name:
        root = Path(name)
        subprocess.run([initdb, "-D", str(root / "data"), "-A", "trust", "--no-locale"],  # noqa: S603 - trusted local PG fixture
                       check=True, capture_output=True)
        subprocess.run([pg_ctl, "-D", str(root / "data"), "-l", str(root / "server.log"),  # noqa: S603 - trusted local PG fixture
                        "-o", f"-k {root} -h '' -p 55439", "-w", "start"], check=True, capture_output=True)
        try:
            yield root
        finally:
            subprocess.run([pg_ctl, "-D", str(root / "data"), "-m", "immediate", "-w", "stop"],  # noqa: S603 - trusted local PG fixture
                           check=True, capture_output=True)


@pytest_asyncio.fixture
async def connection(postgres):
    conn = await asyncpg.connect(host=str(postgres), port=55439, user=getpass.getuser(), database="postgres")
    transaction = conn.transaction()
    await transaction.start()
    await conn.execute("""
      CREATE SCHEMA platform; CREATE SCHEMA common; CREATE SCHEMA security;
      CREATE TABLE platform.team(id uuid,workspace_id uuid,name text,status text DEFAULT 'active',
        deleted_at timestamptz,parent_team_id uuid,valid_from timestamptz DEFAULT '-infinity',
        valid_to timestamptz DEFAULT 'infinity');
      CREATE TABLE platform.team_membership(workspace_id uuid,team_id uuid,user_ref_id uuid,
        membership_role text,valid_from timestamptz DEFAULT '-infinity',valid_to timestamptz DEFAULT 'infinity');
      CREATE FUNCTION common.current_role_code() RETURNS text LANGUAGE sql AS
        $$ SELECT current_setting('test.role') $$;
      CREATE FUNCTION common.current_user_ref_id() RETURNS uuid LANGUAGE sql AS
        $$ SELECT current_setting('test.user_id')::uuid $$;
      CREATE FUNCTION common.current_workspace_id() RETURNS uuid LANGUAGE sql AS
        $$ SELECT current_setting('test.workspace_id')::uuid $$;
      CREATE FUNCTION security.has_active_role(role text) RETURNS boolean LANGUAGE sql AS
        $$ SELECT role=current_setting('test.active_role') $$;
      CREATE FUNCTION security.account_team_scope(uuid,uuid,text) RETURNS TABLE(team_id text) LANGUAGE sql AS
        $$ SELECT unnest(string_to_array(current_setting('test.bound_teams'),',')) $$;
      CREATE FUNCTION security.authorization_subject(permission text,kind text,person uuid,team uuid)
        RETURNS boolean LANGUAGE sql AS $$ SELECT team::text=ANY(string_to_array(
          current_setting(CASE WHEN permission='work_report.write' THEN 'test.write_teams'
                         ELSE 'test.read_teams' END),',')) $$;
    """)
    await conn.executemany("INSERT INTO platform.team(id,workspace_id,name) VALUES($1,$2,$3)",
                          [(A, W, "A"), (B, W, "B")])
    for key, value in dict(role="manager", active_role="manager", user_id=U, workspace_id=W,
                           read_teams=f"{A},{B}", write_teams=f"{A},{B}", bound_teams="").items():
        await conn.execute("SELECT set_config($1,$2,true)", "test." + key, value)
    try:
        yield conn
    finally:
        await transaction.rollback()
        await conn.close()


def actor(role="manager"):
    return ActorContext(workspace_id=W, user_id=U, role=role, data_scope="workspace")


async def teams(conn, role="manager"):
    return await access._teams(conn, actor(role), "work_report.read", write=True, managed_only=True)


@pytest.mark.asyncio
async def test_manager_needs_both_subject_grants_and_active_role_without_appointment(connection):
    c = connection
    assert [r["id"] for r in await teams(c)] == [A, B]
    assert await c.fetchval("SELECT count(*) FROM platform.team_membership") == 0
    await c.execute("SELECT set_config('test.write_teams',$1,true)", A)
    assert [r["id"] for r in await teams(c)] == [A]
    await c.execute("SELECT set_config('test.read_teams',$1,true)", B)
    assert await teams(c) == []
    await c.execute("SELECT set_config('test.read_teams',$1,true)", A)
    await c.execute("SELECT set_config('test.active_role','sales',true)")
    assert await teams(c) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["supervisor", "fde_lead", "sales", "fde"])
async def test_nonmanager_still_requires_matching_appointment_and_role_binding(connection, role):
    c = connection
    for key in ("role", "active_role"):
        await c.execute("SELECT set_config($1,$2,true)", "test." + key, role)
    assert await teams(c, role) == []
    await c.execute("INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role)"
                    " VALUES($1,$2,$3,$4)", W, A, U, role)
    assert await teams(c, role) == []  # Appointment alone cannot extend role scope.
    await c.execute("SELECT set_config('test.bound_teams',$1,true)", A)
    assert [r["id"] for r in await teams(c, role)] == ([A] if role in {"supervisor", "fde_lead"} else [])


@pytest.mark.asyncio
async def test_options_and_overview_use_identical_manager_eligibility(connection, monkeypatch):
    grants = {"work_report.read", "work_report.write", "agent.work_report"}
    effective = SimpleNamespace(allows=lambda p: p in grants, require=lambda p: None)
    monkeypatch.setattr(access.AuthorizationRepository, "effective", AsyncMock(return_value=effective))
    monkeypatch.setattr(access, "_members", AsyncMock(return_value=[dict(id=U, name="self")]))
    empty_share = ShareTargets(allowed_kinds=[], members=[], teams=[])
    monkeypatch.setattr(access, "share_targets", AsyncMock(return_value=empty_share))
    await connection.execute("SELECT set_config('test.write_teams',$1,true)", A)
    options = await access.report_options(connection, actor())
    overview = await work_report_overview.options(connection, actor())
    assert [str(t.id) for t in options.teams] == [A]
    assert [str(t.id) for t in overview.generation.managed_teams] == [A]
    grants.remove("work_report.write")
    assert (await access.report_options(connection, actor())).available_scope_kinds == []
    assert (await work_report_overview.options(connection, actor())).generation.available_scope_kinds == []


@pytest.mark.asyncio
async def test_multiteam_generation_rejects_entire_selection_if_one_subject_unwritable(connection, monkeypatch):
    resolved = SimpleNamespace(scope=SimpleNamespace(subjects=[
        SimpleNamespace(scope_type="team", id=UUID(A)), SimpleNamespace(scope_type="team", id=UUID(B))]))
    monkeypatch.setattr(review_scope, "resolve_review_scope", AsyncMock(return_value=resolved))
    assert await access.resolve_review_generation_scope(connection, actor(), object()) is resolved
    await connection.execute("SELECT set_config('test.write_teams',$1,true)", A)
    with pytest.raises(WorkReportError) as exc:
        await access.resolve_review_generation_scope(connection, actor(), object())
    assert exc.value.status == 403
