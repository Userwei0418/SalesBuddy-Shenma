"""Persistent, synthetic-only local acceptance seed; never a production initializer.

Call ``await seed_acceptance(maintenance_connection, password=secret)`` after
migration. The caller creates and labels the database and reconciles runtime
grants, then serves the API with a separate NOSUPERUSER/NOBYPASSRLS role.
No connection, database, service, grant or external binding is created here.
Replays insert missing seed rows only; existing business edits and passwords stay.
"""

from __future__ import annotations

import json
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from sales_backend.auth.passwords import encode_password, validate_password

DATABASE_MARKER = "shenma-isolated-acceptance-v1"
WORKSPACE_EXTERNAL_ID = "shenma-acceptance"
FOREIGN_WORKSPACE_EXTERNAL_ID = "shenma-acceptance-other"
SEED_VERSION = "shenma-acceptance-v1"


def seed_id(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"salesbuddy:{SEED_VERSION}:{key}")


async def _guard(connection) -> str:
    row = await connection.fetchrow("""SELECT current_database() AS name,
        shobj_description(oid,'pg_database') AS marker
        FROM pg_database WHERE datname=current_database()""")
    if not row or not re.fullmatch(r"shenma_acceptance_[a-z0-9_]+", row["name"]):
        raise ValueError("Seed requires a dedicated shenma_acceptance_* database")
    if row["marker"] != DATABASE_MARKER:
        raise ValueError("Database lacks the explicit Shenma acceptance marker")
    if not await connection.fetchval(
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
    ):
        raise ValueError("Seed needs the maintenance connection, never the runtime connection")
    return row["name"]


async def seed_acceptance(connection, *, password: str) -> dict:
    """Insert an isolated role/scope dataset and return non-sensitive references.

    Lower-case login examples are accepted by native login's uppercase account
    normalization. ``SMXS`` and ``SMXS2`` share North; ``SMXS3`` belongs to East.
    SMZG supervises North only; SMZJL is company-wide; SMYY/SMADMIN use original
    company roles without invented grants. SMOTHER belongs to a second company.
    Password is used only for new credentials; this function never resets it.
    """
    validate_password(password)
    database = await _guard(connection)
    accounts = []
    subjects = []
    async with connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", SEED_VERSION)
        await connection.execute("SET LOCAL lock_timeout='5s'")
        await connection.execute("SET LOCAL statement_timeout='30s'")
        unexpected = await connection.fetchval(
            "SELECT count(*) FROM platform.workspace WHERE external_workspace_id<>ALL($1::text[])",
            [WORKSPACE_EXTERNAL_ID, FOREIGN_WORKSPACE_EXTERNAL_ID],
        )
        if unexpected:
            raise ValueError("Dedicated acceptance database contains an unrelated company; refusing seed")
        form = await connection.fetchval(
            "SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1"
        )
        if form is None:
            raise ValueError("Migrate system configuration before seeding acceptance data")

        for external, name in [(WORKSPACE_EXTERNAL_ID, "神码隔离验收公司"),
                               (FOREIGN_WORKSPACE_EXTERNAL_ID, "神码隔离验证第二公司")]:
            workspace = seed_id(external)
            await connection.execute("""INSERT INTO platform.workspace(id,external_workspace_id,name,attributes)
                VALUES($1,$2,$3,jsonb_build_object('acceptance_seed',$4::text)) ON CONFLICT(id) DO NOTHING""",
                workspace, external, name, SEED_VERSION)
            # Label is not enough: a colliding identity must not attach to another tenant.
            actual = await connection.fetchval(
                "SELECT external_workspace_id FROM platform.workspace WHERE id=$1", workspace
            )
            if actual != external:
                raise ValueError("Acceptance workspace identity collision")
            for code, title in [("north", "验收北区销售"), ("east", "验收华东销售")]:
                await connection.execute("""INSERT INTO platform.team(id,workspace_id,code,name,attributes)
                    VALUES($1,$2,$3,$4,jsonb_build_object('kind','sales','acceptance_seed',$5::text))
                    ON CONFLICT(id) DO NOTHING""", seed_id(external+":"+code), workspace, code, title, SEED_VERSION)

        specs = [
            ("smxs", "销售一", "sales", "north", WORKSPACE_EXTERNAL_ID),
            ("smxs2", "销售二", "sales", "north", WORKSPACE_EXTERNAL_ID),
            ("smxs3", "华东销售", "sales", "east", WORKSPACE_EXTERNAL_ID),
            ("smzg", "北区主管", "supervisor", "north", WORKSPACE_EXTERNAL_ID),
            ("smzjl", "销售总经理", "manager", "north", WORKSPACE_EXTERNAL_ID),
            ("smyy", "运营管理员", "operations", None, WORKSPACE_EXTERNAL_ID),
            ("smadmin", "系统管理员", "administrator", None, WORKSPACE_EXTERNAL_ID),
            ("smother", "第二公司销售", "sales", "north", FOREIGN_WORKSPACE_EXTERNAL_ID),
        ]
        for login, name, role, team_code, external in specs:
            workspace, user = seed_id(external), seed_id("user:"+login)
            team = seed_id(external+":"+team_code) if team_code else None
            created = await connection.fetchval("""INSERT INTO platform.user_ref
                (id,workspace_id,external_user_id,account_code,display_name,attributes)
                VALUES($1,$2,$3,$4,$5,jsonb_build_object('acceptance_seed',$6::text))
                ON CONFLICT(id) DO NOTHING RETURNING id""",
                user, workspace, "acceptance:"+login, login.upper(), name, SEED_VERSION)
            if created:
                await connection.execute("""INSERT INTO platform.role_binding
                    (id,workspace_id,user_ref_id,role_code,data_scope_code,team_id)
                    VALUES($1,$2,$3,$4,$5,$6)""", seed_id("role:"+login), workspace, user, role,
                    {"sales":"self","supervisor":"team"}.get(role,"workspace"), team)
                if team:
                    await connection.execute("""INSERT INTO platform.team_membership
                        (id,workspace_id,user_ref_id,team_id,membership_role)
                        VALUES($1,$2,$3,$4,$5)""", seed_id("membership:"+login), workspace, user, team, role)
                await connection.execute("""INSERT INTO platform.password_credential
                    (user_ref_id,workspace_id,password_hash,must_change_password)
                    VALUES($1,$2,$3,false)""", user, workspace, encode_password(password))
            accounts.append(dict(login=login, account_code=login.upper(), user_id=str(user),
                                 workspace_external_id=external, workspace_id=str(workspace),
                                 role=role, team_id=str(team) if team else None))

        # Realistic invented organizations; all descendants inherit synthetic_trial
        # provenance so existing external notification/report boundaries remain intact.
        scenarios = [
            ("smxs", "北辰智造技术有限公司", "生产知识库与智能检索试点", 380000, 30),
            ("smxs2", "云川信息服务有限公司", "企业客服助手一期", 520000, 50),
            ("smxs3", "澄海供应链科技有限公司", "供应链运营问答平台", 260000, 10),
            ("smother", "星禾数字服务有限公司", "第二公司隔离验证项目", 190000, 30),
        ]
        for login, customer_name, project_name, amount, probability in scenarios:
            person = next(a for a in accounts if a["login"] == login)
            workspace, user, team = (UUID(person[k]) for k in ("workspace_id","user_id","team_id"))
            cid, oid = seed_id("customer:"+login), seed_id("opportunity:"+login)
            marker = json.dumps({"synthetic_trial": dict(batch_id=SEED_VERSION,
                root_customer_id=str(cid), source="isolated_acceptance_seed")})
            # Give immutable recorder snapshots their real synthetic identity,
            # without granting the runtime role maintenance privileges.
            for setting, value in {"app.workspace_id":str(workspace),"app.user_ref_id":str(user),
                                   "app.role_code":"sales","app.team_ids":str(team)}.items():
                await connection.execute("SELECT set_config($1,$2,true)", setting, value)
            await connection.execute("""INSERT INTO crm.customer
                (id,workspace_id,name,normalized_name,owner_user_ref_id,owner_team_id,
                 created_by_user_ref_id,level_code,data_kind,import_meta,demand_summary,next_action)
                VALUES($1,$2,$3,$3,$4,$5,$4,'Tier-2','demo',$6::jsonb,
                 '客户希望降低内部资料检索和重复咨询成本，先选择一个部门试点。','确认试点范围和验收标准')
                ON CONFLICT(id) DO NOTHING""", cid,workspace,customer_name,user,team,marker)
            await connection.execute("""INSERT INTO crm.opportunity
                (id,workspace_id,customer_id,name,amount,probability,expected_close_date,
                 owner_user_ref_id,owner_team_id,created_by_user_ref_id)
                VALUES($1,$2,$3,$4,$5,$6,current_date+45,$7,$8,$7) ON CONFLICT(id) DO NOTHING""",
                oid,workspace,cid,project_name,amount,probability,user,team)
            visits = []
            for index, days in enumerate((0, 2, 6)):
                vid = seed_id(f"visit:{login}:{index}")
                await connection.execute("""INSERT INTO activity.visit
                    (id,workspace_id,customer_id,opportunity_id,recorder_user_ref_id,recorder_team_id,
                     created_by_user_ref_id,form_version_id,status,interaction_at,follow_up_record,next_action,
                     confirmed_by_user_ref_id,confirmed_at,archived_at,follow_up_score)
                    VALUES($1,$2,$3,$4,$5,$6,$5,$7,'archived',
                     date_trunc('day',clock_timestamp())-$8::integer*interval '1 day'+interval '10 hours',
                     $9,'由销售整理需求清单，客户下周确认试点人员和资料范围',
                     $5,clock_timestamp(),clock_timestamp(),$10)
                    ON CONFLICT(id) DO NOTHING""",vid,workspace,cid,oid,user,team,form,days,
                    ["已与业务负责人交流，确认当前资料分散在多个系统，首期聚焦高频问题检索。",
                     "技术人员完成方案讨论，关注权限隔离与数据更新频率，待提供样例资料。",
                     "客户认可先小范围验证，明确以回答准确性和使用效率作为评估指标。"][index], 80+index*3)
                visits.append(str(vid))
            tid = seed_id("task:"+login)
            inserted_task = await connection.fetchval("""INSERT INTO workflow.task
                (id,workspace_id,title,description,customer_id,opportunity_id,source_visit_id,
                 creator_user_ref_id,creator_team_id,due_at,association_kind,status)
                VALUES($1,$2,'确认试点资料范围','与客户确认首期知识资料清单和业务验收口径',
                 $3,$4,$5,$6,$7,clock_timestamp()+interval '3 days','customer','pending_execution')
                ON CONFLICT(id) DO NOTHING RETURNING id""",tid,workspace,cid,oid,UUID(visits[0]),user,team)
            if inserted_task:
                await connection.execute("""INSERT INTO workflow.task_assignee
                    (task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility)
                    VALUES($1,$2,$3,$4,'sales','owner')""",tid,workspace,user,team)
            subjects.append(dict(login=login, workspace_id=str(workspace), customer_id=str(cid),
                                 opportunity_id=str(oid), visit_ids=visits, task_id=str(tid)))
        counts = {}
        for table in ("platform.workspace", "platform.user_ref", "crm.customer", "crm.opportunity",
                      "activity.visit", "workflow.task"):
            # Only the fixed table literals above participate; callers supply no SQL.
            counts[table] = await connection.fetchval("SELECT count(*) FROM " + table)  # noqa: S608
    return dict(database=database, seed_version=SEED_VERSION, accounts=accounts,
                subjects=subjects, counts=counts, external_side_effects=False)
