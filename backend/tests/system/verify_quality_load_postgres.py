"""Synthetic HTTP/pool/worker load observation on a self-owned PostgreSQL 5432 DB.

No latency SLA is invented here. The reported percentiles describe these local
samples (eight per case by default), not production capacity. Run without another
CI/load job for comparable timing; a concurrent CI run only checks correctness.
"""
# ruff: noqa: E402 -- Standalone script explicitly loads reviewed repo helpers.
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import secrets
import shutil
import subprocess
import sys
import traceback
from collections import Counter
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from urllib.parse import quote
from uuid import uuid4

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "tools/installation"), str(ROOT / "tools/local"),
               str(ROOT / "database/scripts"), str(ROOT / "backend/src")]
from bootstrap import identity, seed
from migrate import migrate
from runtime_roles import provision_new
from runtime_roles import verify as verify_roles

from sales_backend.auth.passwords import encode_password
from sales_backend.config import Settings
from sales_backend.db import Database, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.jobs import record_job_effect
from sales_backend.worker import Worker

CUSTOMERS = 1000
OPPORTUNITIES = 500
ACTIVE_CUSTOMERS = 100
JOBS = 20
JOB_TYPE = "quality.synthetic_probe"


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


def settings(dsn):
    # Construct explicitly: do not inherit a real DATABASE_URL, provider key,
    # credential file, platform URL or Agent pilot from the shell environment.
    return Settings(
        app_env="test", database_url=dsn, senseaudio_base_url="http://external.invalid",
        senseaudio_api_key="", asr_model="disabled", tts_model="disabled", llm_model="disabled",
        timeout_seconds=1, max_retries=0, access_token_secret=secrets.token_urlsafe(48),
        access_token_issuer="quality-fixture", access_token_audience="quality-fixture",
        access_token_minutes=30, refresh_token_days=1, auth_mode="password", demo_workspace="",
        database_min_pool_size=1, database_max_pool_size=4, worker_poll_seconds=.05,
        worker_lock_seconds=30, worker_id="quality-fixture", worker_database_max_pool_size=2,
    )


def company_prefixes(count):
    if not 2 <= count <= 30:
        raise ValueError("Synthetic tenant count must be between 2 and 30")
    return ("A", "B", *(f"C{number:02d}" for number in range(1, count - 1)))


async def seed_extra_company(owner, prefix, password_hash):
    """Additional isolated capacity tenants; no changes to daily local fixtures."""
    workspace = identity(prefix)
    team, fde_team = identity(prefix + "-sales"), identity(prefix + "-fde")
    await owner.execute("""INSERT INTO platform.workspace(id,external_workspace_id,name,attributes)
        VALUES($1,$2,$3,'{"local_synthetic":true}'::jsonb)""",
        workspace, "local-" + prefix.lower(), "合成容量公司-" + prefix)
    for tid, code in ((team, "sales"), (fde_team, "fde")):
        await owner.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$4)",
                            tid, workspace, code, "合成团队-" + code)
    for suffix, role, scope in (("ADMIN", "administrator", "workspace"), ("SALES", "sales", "self"),
                                ("MANAGER", "manager", "workspace"), ("FDE", "fde", "self"),
                                ("OPS", "operations", "workspace")):
        user = identity(prefix + suffix)
        team_id = fde_team if role == "fde" else team
        await owner.execute("""INSERT INTO platform.user_ref(id,workspace_id,external_user_id,
            display_name,account_code) VALUES($1,$2,$3,$3,$3)""", user, workspace, prefix + suffix)
        await owner.execute("""INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,
            data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)""", workspace, user, role, scope,
            None if role in {"administrator", "operations"} else team_id)
        if role not in {"administrator", "operations"}:
            await owner.execute("""INSERT INTO platform.team_membership(workspace_id,user_ref_id,
                team_id,membership_role) VALUES($1,$2,$3,$4)""", workspace, user, team_id, role)
        await owner.execute("""INSERT INTO platform.password_credential(user_ref_id,workspace_id,
            password_hash,must_change_password) VALUES($1,$2,$3,false)""", user, workspace, password_hash)
    await owner.execute("""INSERT INTO crm.customer(id,workspace_id,name,normalized_name,
        owner_user_ref_id,owner_team_id,created_by_user_ref_id,level_code)
        VALUES($1,$2,$3,$3,$4,$5,$4,'Tier-2')""", identity(prefix + "-customer"), workspace,
        "合成容量公司-" + prefix + "-客户", identity(prefix + "SALES"), team)


async def seed_load(owner, password, companies=2):
    fixture = await seed(owner, {"test_password": password})
    prefixes = company_prefixes(companies)
    password_hash = encode_password(password)
    async with owner.transaction():
        for prefix in prefixes[2:]:
            await seed_extra_company(owner, prefix, password_hash)
    await owner.execute("""UPDATE control.tenant_module SET enabled=(module_code NOT IN ('ai','feishu')),
        source='quality-synthetic-no-external'""")
    # Finite, explicit fixture request admission; enough for the largest allowed
    # workload without turning a throttle response into a latency observation.
    await owner.execute("""UPDATE control.tenant_http_limit SET rate_per_minute=10000,
        burst=10000,tokens=10000 WHERE bucket='read'""")
    actors, details = {}, {}
    for prefix in prefixes:
        for suffix in ("SALES", "MANAGER", "FDE", "ADMIN"):
            row = await owner.fetchrow("SELECT * FROM security.resolve_account_actor($1,$2,NULL)",
                                       "local-" + prefix.lower(), prefix + suffix)
            assert row, "Synthetic persona could not be resolved"
            actors[prefix + suffix] = IdentityRepository._actor(row).context
        sales = actors[prefix + "SALES"]
        workspace, user, team = sales.workspace_id, sales.user_id, sales.team_ids[0]
        async with owner.transaction():
            await set_request_context(owner, actors[prefix + "ADMIN"])
            await owner.execute("""INSERT INTO crm.customer(workspace_id,name,normalized_name,
                owner_user_ref_id,owner_team_id,created_by_user_ref_id,level_code,attributes)
                SELECT $1::uuid,$4||n,$4||n,$2::uuid,$3::uuid,$2::uuid,'Tier-2',
                  jsonb_build_object('quality_load_ordinal',n,'synthetic',true)
                FROM generate_series(1,$5::integer)n""", workspace, user, team,
                "QUALITY-" + prefix + "-CUSTOMER-", CUSTOMERS)
            await owner.execute("""INSERT INTO crm.customer_ownership(customer_id,workspace_id,owner_user_ref_id,state)
                SELECT id,workspace_id,owner_user_ref_id,'claimed' FROM crm.customer WHERE workspace_id=$1::uuid
                ON CONFLICT(customer_id) DO UPDATE SET
                  state='claimed',owner_user_ref_id=EXCLUDED.owner_user_ref_id""", workspace)
            await owner.execute("""INSERT INTO crm.customer_sales_member(
                workspace_id,customer_id,user_ref_id,added_by_user_ref_id)
                SELECT workspace_id,id,owner_user_ref_id,$2::uuid FROM crm.customer WHERE workspace_id=$1::uuid
                ON CONFLICT DO NOTHING""", workspace, user)
            await owner.execute("""INSERT INTO crm.opportunity(workspace_id,customer_id,name,amount,probability,
                owner_user_ref_id,owner_team_id,created_by_user_ref_id,expected_close_year,expected_close_quarter)
                SELECT workspace_id,id,$4||(attributes->>'quality_load_ordinal'),10000,30,$2::uuid,$3::uuid,$2::uuid,
                  extract(year FROM current_date)::integer,extract(quarter FROM current_date)::integer
                FROM crm.customer WHERE workspace_id=$1::uuid
                  AND (attributes->>'quality_load_ordinal')::integer<=$5""", workspace, user, team,
                "QUALITY-" + prefix + "-OPPORTUNITY-", OPPORTUNITIES)
            await owner.execute("""INSERT INTO crm.opportunity_participant(workspace_id,opportunity_id,
                user_ref_id,participant_role,source_code,assigned_by_user_ref_id)
                SELECT workspace_id,id,$2::uuid,'fde','manual',$3::uuid FROM crm.opportunity
                WHERE workspace_id=$1::uuid""", workspace, actors[prefix + "FDE"].user_id, user)
            form_definition = await owner.fetchval("""INSERT INTO config.form_definition(workspace_id,
                form_code,name,object_type) VALUES($1::uuid,'quality-synthetic','合成性能拜访','visit')
                RETURNING id""", workspace)
            form = await owner.fetchval("""INSERT INTO config.form_version(form_definition_id,version_no,status)
                VALUES($1,1,'active') RETURNING id""", form_definition)
            await owner.execute("""INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,
                recorder_user_ref_id,recorder_team_id,form_version_id,status,interaction_at,follow_up_record,
                next_action,confirmed_at,confirmed_by_user_ref_id,recording_role_code_snapshot,recording_team_id_snapshot)
                SELECT o.workspace_id,o.customer_id,o.id,$2::uuid,$3::uuid,$4,'confirmed',
                  clock_timestamp()-interval '1 day','固定合成性能记录，不是真实业务，不调用模型',
                  '核对合成资料',clock_timestamp(),$2::uuid,'sales',$3::uuid
                FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id
                WHERE o.workspace_id=$1::uuid AND (c.attributes->>'quality_load_ordinal')::integer<=$5""",
                workspace, user, team, form, ACTIVE_CUSTOMERS)
            details[prefix] = str(await owner.fetchval("""SELECT id FROM crm.customer WHERE workspace_id=$1::uuid
                AND attributes->>'quality_load_ordinal'='1'""", workspace))
    await owner.execute("""ANALYZE crm.customer,crm.customer_ownership,crm.customer_sales_member,
        crm.opportunity,crm.opportunity_participant,activity.visit,activity.visit_opportunity""")
    return fixture, actors, details


@dataclass(frozen=True)
class Case:
    company: str
    persona: str
    category: str
    path: str

    @property
    def name(self):
        return f"{self.company}:{self.persona}:{self.category}"


def cases(details):
    result = []
    for company in details:
        for persona in ("SALES", "MANAGER", "FDE"):
            result.extend([
                Case(company, persona, "map", "/api/v1/customer-assets/map"),
                Case(company, persona, "permissions", "/api/v1/auth/permissions"),
            ])
        result.extend([
            Case(company, "SALES", "customers", "/api/v1/customers?page_size=50"),
            Case(company, "MANAGER", "customers", "/api/v1/customers?page_size=50"),
            Case(company, "SALES", "detail", "/api/v1/customers/" + details[company]),
            Case(company, "FDE", "detail", "/api/v1/customers/" + details[company]),
            Case(company, "ADMIN", "console_opportunities", "/api/v1/console/opportunities?limit=50"),
            Case(company, "ADMIN", "permission_directory", "/api/v1/console/permissions/options"),
        ])
    return result


def timing(response):
    result = {}
    for item in response.headers.get("server-timing", "").split(","):
        name, _, rest = item.strip().partition(";dur=")
        if rest:
            result[name] = float(rest.split(";", 1)[0])
    assert set(result) == {"app", "db", "pool", "audit"}, "Missing production timing headers"
    queries = int(response.headers["x-db-queries"])
    assert queries > 0 and all(value >= 0 and math.isfinite(value) for value in result.values())
    return result, queries


def validate_response(case, response, details):
    assert response.status_code == 200, "Unexpected HTTP status"
    body = response.json()
    for foreign in details.keys() - {case.company}:
        assert "QUALITY-" + foreign + "-" not in response.text, "Foreign business rows escaped"
        assert str(identity(foreign)) not in response.text, "Foreign workspace escaped"
    if case.category in {"customers", "map", "console_opportunities"}:
        expected = ACTIVE_CUSTOMERS if case.category == "map" else 50
        assert len(body["items"]) == expected, "Unexpected page/map count"
        assert all(item["name"].startswith("QUALITY-" + case.company + "-") for item in body["items"])
        if case.category == "console_opportunities":
            assert body["total"] == OPPORTUNITIES and body["total_amount"] == OPPORTUNITIES * 10000
    elif case.category == "detail":
        assert body["id"] == details[case.company] and len(body["visits"]) == 1
    elif case.category == "permissions":
        assert body["workspace_id"] == str(identity(case.company))
        assert body["user_id"] == str(identity(case.company + case.persona))
    elif case.category == "permission_directory":
        assert len(body["accounts"]) == 5 and len(body["teams"]) == 2
        assert all(item["account_code"].startswith(case.company) for item in body["accounts"])


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return None
    return {"samples": len(ordered), "p50": round(ordered[math.ceil(len(ordered) * .5) - 1], 3),
            "p95": round(ordered[math.ceil(len(ordered) * .95) - 1], 3), "max": round(ordered[-1], 3)}


def summarize(samples):
    result = []
    for name in sorted({sample["case"] for sample in samples}):
        group = [sample for sample in samples if sample["case"] == name]
        measured = [sample for sample in group if sample["phase"] == "load"]
        success = [sample for sample in measured if sample["ok"]]
        result.append({"case": name, "first_request": next((r for r in group if r["phase"] == "first"), None),
                       "warmup": next((r for r in group if r["phase"] == "warmup"), None),
                       "load_requests": len(measured),
                       "error_rate": sum(not r["ok"] for r in measured) / max(1, len(measured)),
                       "http_ms": distribution([r["elapsed_ms"] for r in measured]),
                       "server_ms": {metric: distribution([r["timing"][metric] for r in success])
                                     for metric in ("app", "db", "pool", "audit")},
                       "sql_queries": distribution([r["queries"] for r in success]),
                       "response_bytes": distribution([r["bytes"] for r in measured])})
    return result


class ProbeWorker(Worker):
    """Keep real claims, leases, transactions, effects and acknowledgements."""
    def __init__(self, database, companies):
        super().__init__(database)
        self.completed = []
        self.companies = {str(identity(company)) for company in companies}
        self.completed_companies = Counter()

    async def _handle_job(self, job):
        assert job.job_type == JOB_TYPE and job.workspace_id in self.companies
        # Fixed synthetic work prevents the first probe completing before the
        # concurrent HTTP clients enter their request paths. No provider exists.
        await asyncio.sleep(.03)
        async with self.database.transaction(self._job_actor(job)) as connection:
            assert await connection.fetchval("SELECT count(*) FROM crm.customer") == CUSTOMERS + 1
            await record_job_effect(connection, job.workspace_id)
        self.completed.append(perf_counter())
        self.completed_companies[job.workspace_id] += 1


async def exercise(owner, api_settings, worker_settings, actors, details, password, rounds, report):
    from sales_backend.main import app
    worker_db = Database(worker_settings)
    await worker_db.connect()
    worker = ProbeWorker(worker_db, details)
    background = None
    samples = report["samples"]
    scheduled = cases(details)
    try:
        async with AsyncExitStack() as stack:
            stack.enter_context(patch("sales_backend.main.get_settings", return_value=api_settings))
            # Real ASGI transport is allowed; accidental external HTTP is an error.
            async def forbidden_async(*args, **kwargs):
                report["external_http_attempts"] += 1
                raise AssertionError("External HTTP forbidden in synthetic acceptance")

            def forbidden_sync(*args, **kwargs):
                report["external_http_attempts"] += 1
                raise AssertionError("External HTTP forbidden in synthetic acceptance")

            stack.enter_context(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_async))
            stack.enter_context(patch.object(httpx.HTTPTransport, "handle_request", forbidden_sync))
            await stack.enter_async_context(app.router.lifespan_context(app))
            for db in (app.state.database, worker_db):
                async with db.connection() as connection:
                    assert not await connection.fetchval(
                        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                    assert await connection.fetchval("SELECT count(*) FROM crm.customer") == 0
            clients = {}
            # Each synthetic company represents a separate client network. Its
            # four personas share one stable RFC 5737 peer, retaining the real
            # account/network login throttle without forwarding-header overrides.
            peers = {company: (f"192.0.2.{index}", 123) for index, company in
                     enumerate(company_prefixes(len(details)), start=1)}
            report["login_peer_topology"] = {
                "networks": len(peers), "personas_per_network": 4,
                "address_range": "RFC 5737 TEST-NET-1", "forwarding_headers": False,
            }
            for name in actors:
                company = next(prefix for prefix in details if name in {prefix + role for role in
                    ("ADMIN", "SALES", "MANAGER", "FDE")})
                client = await stack.enter_async_context(httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app, client=peers[company]),
                    base_url="http://quality.local", trust_env=False))
                path = "/api/v1/console/auth/login" if name.endswith("ADMIN") else "/api/v1/auth/password/login"
                started = perf_counter()
                response = await client.post(path, headers={"Origin": "http://quality.local"}, json={
                    "account_code": name, "workspace": "local-" + company.lower(), "password": password})
                login = {"persona": name, "phase": "login", "status": response.status_code,
                         "ok": response.status_code == 200,
                         "elapsed_ms": round((perf_counter()-started)*1000, 3)}
                report["logins"].append(login)
                assert response.status_code == 200, "Synthetic password login failed"
                login["timing"], login["queries"] = timing(response)
                client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
                clients[name] = client
            # Cross-company object references must fail through the same real route.
            companies = list(details)
            for index, company in enumerate(companies):
                other = companies[(index + 1) % len(companies)]
                response = await clients[company + "SALES"].get("/api/v1/customers/" + details[other])
                assert response.status_code == 404, "Foreign detail became visible"
            semaphore = asyncio.Semaphore(8)

            async def request(case, phase):
                async with semaphore:
                    started = perf_counter()
                    sample = {"case": case.name, "phase": phase, "ok": False, "status": None, "bytes": 0}
                    try:
                        # An outer bound also applies to in-process ASGI, which is
                        # not governed by httpx's network transport timeout.
                        async with asyncio.timeout(45):
                            response = await clients[case.company + case.persona].get(case.path)
                        sample.update(status=response.status_code, bytes=len(response.content))
                        sample["timing"], sample["queries"] = timing(response)
                        validate_response(case, response, details)
                        sample["ok"] = True
                    except Exception as exc:
                        sample["error"] = type(exc).__name__  # No response, token, DSN or query text.
                    finally:
                        sample["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
                        samples.append(sample)

            for phase in ("first", "warmup"):
                for case in scheduled:
                    await request(case, phase)
            assert all(sample["ok"] for sample in samples), "First/warmup functional checks failed"
            job_ids = []
            jobs_per_company = max(2, JOBS // len(companies))
            for company in companies:
                actor = actors[company + "MANAGER"]
                payload = json.dumps({"workspace_id": actor.workspace_id, "user_id": actor.user_id,
                                      "role": actor.role.value, "data_scope": actor.data_scope.value,
                                      "team_ids": list(actor.team_ids)})
                job_ids.extend(row["id"] for row in await owner.fetch(
                    """INSERT INTO ops.job(workspace_id,job_type,payload)
                    SELECT $1::uuid,$2,$3::jsonb FROM generate_series(1,$4::integer) RETURNING id""",
                    actor.workspace_id, JOB_TYPE, payload, jobs_per_company))

            async def progress():
                for _ in job_ids:
                    assert await worker.run_once(job_types=(JOB_TYPE,)), "Probe job was not claimed"

            load_started = perf_counter()
            background = asyncio.create_task(progress())
            # Rotate the fixed order each round so one company/persona is not
            # consistently last behind the limited pool.
            ordered = [scheduled[(index + round_no) % len(scheduled)]
                       for round_no in range(rounds) for index in range(len(scheduled))]
            await asyncio.gather(*(request(case, "load") for case in ordered))
            load_ended = perf_counter()
            await background
            report["load_wall_ms"] = round((load_ended - load_started)*1000, 3)
            report["worker"] = {"jobs": len(job_ids), "effects": len(worker.completed),
                "companies_completed": len(worker.completed_companies),
                "completed_during_http_load": sum(load_started <= stamp <= load_ended for stamp in worker.completed)}
            states = await owner.fetch("SELECT status,attempts FROM ops.job WHERE id=ANY($1::uuid[])", job_ids)
            assert len(states) == len(job_ids) and all(
                row["status"] == "succeeded" and row["attempts"] == 1 for row in states)
            assert await owner.fetchval(
                "SELECT count(*) FROM ops.job_effect WHERE job_id=ANY($1::uuid[])", job_ids) == len(job_ids)
            assert set(worker.completed_companies) == worker.companies
            assert report["worker"]["completed_during_http_load"] > 0, "No other-company job progressed during HTTP"
            assert all(sample["ok"] for sample in samples), "Mixed load returned errors or incorrect data"
            assert report["external_http_attempts"] == 0
    finally:
        if background is not None and not background.done():
            background.cancel()
            await asyncio.gather(background, return_exceptions=True)
        await worker_db.close()


async def run(args):
    if args.port != 5432:
        raise ValueError("Only loopback test port 5432 is allowed; 15432 is never accepted")
    config = {"host": "127.0.0.1", "port": 5432, "user": args.user, "command_timeout": 300}
    suffix = uuid4().hex[:12]
    name = "salesbuddy_quality_" + suffix
    roles = {service: "sb_quality_" + suffix + "_" + service for service in ("api", "worker", "platform", "feishu")}
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git is required to identify the code under observation")
    report = {"format": "salesbuddy-quality-load-v1", "status": "running", "port": 5432,
        "revision": subprocess.check_output(  # noqa: S603 -- Resolved Git; fixed read-only arguments, no shell.
            [git, "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(  # noqa: S603 -- Fixed read-only Git status arguments.
            [git, "status", "--porcelain"], cwd=ROOT, text=True).strip()),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "runtime_policy_sha256": hashlib.sha256(
            (ROOT / "tools/installation/runtime_permissions.json").read_bytes()).hexdigest(),
        "fixture": {"companies": args.companies, "personas_exercised": args.companies * 4,
                    "customers_per_company": CUSTOMERS + 1,
                    "opportunities_per_company": OPPORTUNITIES, "active_customers_per_company": ACTIVE_CUSTOMERS},
        "load": {"cases": args.companies * 12, "first_per_case": 1, "warmup_per_case": 1, "rounds": args.rounds,
                 "concurrency": 8, "api_pool": 4, "worker_pool": 2},
        "interpretation": ("Local sample percentiles only; not a production SLA. "
                           "First request is per case, not a flushed PostgreSQL cache."),
        "logins": [], "samples": [], "external_http_attempts": 0,
        "cleanup": {"database_removed": False, "roles_removed": False}, "production_changed": False}
    admin = owner = None
    created = False
    roles_owned = False
    failure = None
    try:
        async with asyncio.timeout(900):
            admin = await asyncpg.connect(database="postgres", **config)
            assert int(await admin.fetchval("SHOW port")) == 5432
            await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
            created = True
            owner = await asyncpg.connect(database=name, **config)
            assert await owner.fetchval("SELECT current_database()") == name
            await migrate(owner)
            report["migration_count"] = await owner.fetchval("SELECT count(*) FROM ops.migration_checksum")
            passwords = await provision_new(owner, database=name, roles=roles)
            roles_owned = True
            await verify_roles(owner, roles)
            password = secrets.token_urlsafe(30)
            _, actors, details = await seed_load(owner, password, args.companies)
            def dsn(service):
                return f"postgresql://{roles[service]}:{quote(passwords[service])}@127.0.0.1:5432/{name}"
            api_settings = settings(dsn("api"))
            worker_settings = replace(api_settings, database_url=dsn("worker"), database_max_pool_size=2)
            emit({"phase": "load_started", "fixture": report["fixture"], "load": report["load"]})
            await exercise(owner, api_settings, worker_settings, actors, details, password, args.rounds, report)
            await verify_roles(owner, roles)
            report["status"] = "passed"
    except BaseException as exc:
        report["status"], report["failure_type"] = "failed", type(exc).__name__
        report["failure_sqlstate"] = getattr(exc, "sqlstate", None)
        report["failure_location"] = [Path(frame.filename).name + ":" + str(frame.lineno)
                                      for frame in traceback.extract_tb(exc.__traceback__)]
        failure = exc
    finally:
        try:
            if owner is not None:
                await owner.close()
            if admin is not None:
                if created:
                    await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
                    report["cleanup"]["database_removed"] = True
                if roles_owned:
                    for role in roles.values():
                        await admin.execute(f'DROP ROLE "{role}"')
                report["cleanup"]["roles_removed"] = not await admin.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($1::text[]))", list(roles.values()))
                await admin.close()
        except Exception as exc:
            report["status"], report["cleanup_error_type"] = "failed", type(exc).__name__
            failure = failure or exc
        report["summary"] = summarize(report["samples"])
        report["http_status_counts"] = dict(Counter(str(row["status"]) for row in report["samples"]))
        if args.output:
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        emit(report)
    if failure:
        raise RuntimeError("Synthetic quality load failed; inspect the structured report") from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", required=True, help="Maintenance role on the disposable test cluster")
    parser.add_argument("--rounds", type=int, choices=range(1, 51), default=8)
    parser.add_argument("--companies", type=int, choices=range(2, 31), default=2,
                        help="Synthetic active tenants, each with four exercised personas")
    parser.add_argument("--output", type=Path, help="New report file; refuses to replace an existing file")
    asyncio.run(run(parser.parse_args()))
