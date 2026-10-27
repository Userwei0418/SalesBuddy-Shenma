"""Run the real cohort SQL in disposable PG; no application DB or RLS changes.

The existing Unix-only PG fixture and synthetic subject grants exercise query
qualification, not the production permission engine (which is unchanged).
"""
import getpass
from types import SimpleNamespace
from uuid import UUID

import asyncpg
import pytest
import pytest_asyncio

from sales_backend.repositories.dashboard_performance import cohort
from tests.test_work_report_manager_team_eligibility import postgres  # noqa: F401 - shared fixture

W, FOREIGN, TEAM = [str(UUID(int=n)) for n in (1, 2, 3)]


@pytest_asyncio.fixture
async def connection(postgres):
    conn = await asyncpg.connect(host=str(postgres), port=55439, user=getpass.getuser(), database="postgres")
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("""
          CREATE SCHEMA platform; CREATE SCHEMA security;
          CREATE TABLE platform.user_ref(id uuid,workspace_id uuid,display_name text,
            status text DEFAULT 'active',deleted_at timestamptz);
          CREATE TABLE platform.role_binding(user_ref_id uuid,workspace_id uuid,role_code text,
            valid_from timestamptz DEFAULT '-infinity',valid_to timestamptz DEFAULT 'infinity');
          CREATE TABLE platform.team(id uuid,workspace_id uuid,name text,parent_team_id uuid,
            status text DEFAULT 'active',deleted_at timestamptz,
            valid_from timestamptz DEFAULT '-infinity',valid_to timestamptz DEFAULT 'infinity');
          CREATE TABLE platform.team_membership(id uuid,workspace_id uuid,user_ref_id uuid,team_id uuid,
            is_primary boolean DEFAULT true,valid_from timestamptz DEFAULT '-infinity',
            valid_to timestamptz DEFAULT 'infinity');
          CREATE TABLE security.denied(permission text,subject_id uuid);
          CREATE FUNCTION security.authorization_subject(permission text,kind text,person uuid,team uuid)
            RETURNS boolean LANGUAGE sql AS $$ SELECT NOT EXISTS(SELECT 1 FROM security.denied d
              WHERE d.permission=$1 AND d.subject_id=COALESCE($3,$4)) $$;
          CREATE FUNCTION security.target_scope_read(text,uuid,uuid,text)
            RETURNS boolean LANGUAGE sql AS $$ SELECT true $$;
        """)
        await conn.execute("INSERT INTO platform.team(id,workspace_id,name) VALUES($1,$2,'Sales')", TEAM, W)
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


async def person(conn, number, roles, *, workspace=W, status="active", deleted=False):
    ident = str(UUID(int=number))
    await conn.execute("INSERT INTO platform.user_ref VALUES($1,$2,$3,$4,"
                       "CASE WHEN $5 THEN transaction_timestamp() END)", ident, workspace, str(number), status, deleted)
    await conn.executemany("INSERT INTO platform.role_binding(user_ref_id,workspace_id,role_code) VALUES($1,$2,$3)",
                           [(ident, workspace, role) for role in roles])
    return ident


@pytest.mark.asyncio
async def test_effective_roles_exclude_mixed_roles_without_cross_workspace_or_historical_shadowing(connection):
    c = connection
    eligible = {await person(c, 10, ["sales"]), await person(c, 11, ["supervisor"]),
                await person(c, 12, ["sales", "supervisor"])}
    excluded = set()
    for n, role in enumerate(("manager", "fde", "fde_lead", "operations"), 20):
        excluded.add(await person(c, n, [role]))
        excluded.add(await person(c, n + 10, ["sales", role]))
        excluded.add(await person(c, n + 20, ["supervisor", role]))
    expired = await person(c, 50, ["sales", "manager"])
    future = await person(c, 51, ["supervisor", "operations"])
    await c.execute("UPDATE platform.role_binding SET valid_to=transaction_timestamp() WHERE user_ref_id=$1 AND role_code='manager'", expired)
    await c.execute("UPDATE platform.role_binding SET valid_from=transaction_timestamp()+interval '1 day' WHERE user_ref_id=$1 AND role_code='operations'", future)
    eligible.update((expired, future))
    foreign_binding = await person(c, 52, ["sales"])
    await c.execute("INSERT INTO platform.role_binding(user_ref_id,workspace_id,role_code) VALUES($1,$2,'fde')", foreign_binding, FOREIGN)
    eligible.add(foreign_binding)
    absent = {await person(c, 60, ["sales"], status="inactive"),
              await person(c, 61, ["sales"], deleted=True),
              await person(c, 62, ["sales"], workspace=FOREIGN),
              await person(c, 63, ["administrator"]), await person(c, 64, ["sales"])}
    await c.execute("UPDATE platform.role_binding SET valid_to=transaction_timestamp() WHERE user_ref_id=$1", str(UUID(int=64)))
    rows = await cohort(c, SimpleNamespace(workspace_id=W), actual=False)
    people = {r["id"]: r for r in rows if r["kind"] == "person"}
    assert {ident for ident, row in people.items() if row["participates"]} == eligible
    assert {ident for ident, row in people.items() if not row["participates"]} == excluded
    assert not (people.keys() & absent)
    assert [(r["id"], r["participates"]) for r in rows if r["kind"] == "team"] == [(TEAM, True)]


@pytest.mark.asyncio
async def test_role_filter_does_not_bypass_existing_ranking_read_or_source_permissions(connection):
    c = connection
    allowed = await person(c, 10, ["sales"])
    hidden = [await person(c, n, ["supervisor"]) for n in (11, 12)]
    await c.executemany("INSERT INTO security.denied VALUES($1,$2)",
                       [("dashboard.ranking", hidden[0]), ("dashboard.read", hidden[1]),
                        ("actual.read", allowed), ("target.read", allowed)])
    for actual in (False, True):
        rows = await cohort(c, SimpleNamespace(workspace_id=W), actual=actual)
        people = [r for r in rows if r["kind"] == "person"]
        assert len(people) == 1 and people[0]["id"] == allowed
        assert people[0]["source_complete"] is (not actual)
        assert people[0]["target_allowed"] is False
        assert [r["id"] for r in rows if r["kind"] == "team"] == [TEAM]
