"""Customer-owned actuals; no forecast/ACV inference, all totals computed before pagination."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from collections import defaultdict
from pydantic_core import to_jsonable_python
from sales_backend.domain.finance_totals import actual_totals, date_scoped_actuals
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.authorization_checks import require_permission


def today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def period_start(period, as_of=None):
    day = as_of or today()
    return date(day.year, 1, 1) if period == "year" else None


async def can_manage(connection, customer_id=None, opportunity_id=None):
    if not await connection.fetchval("SELECT security.management_actor() AND security.authorization_has('access.console')"):
        return False
    if not (await AuthorizationRepository().effective(connection)).allows('actual.create'):
        return False
    if customer_id and not await connection.fetchval("SELECT security.authorization_customer('actual.create',$1::uuid)",str(customer_id)):
        return False
    return not opportunity_id or bool(await connection.fetchval("SELECT security.authorization_opportunity('actual.create',$1::uuid)",str(opportunity_id)))


class CustomerAssetRepository:
    async def create_permission(self, connection, data):
        await require_permission(connection, 'actual.create', customer_id=data['customer_id'])
        if data.get('opportunity_id'):
            await require_permission(connection, 'actual.create', opportunity_id=data['opportunity_id'])

    async def void_permission(self, connection, record_id):
        row = await connection.fetchrow('SELECT customer_id,opportunity_id FROM crm.customer_actual WHERE id=$1',record_id)
        if not row:
            raise LookupError('记录不存在或不在当前权限范围内')
        await require_permission(connection,'actual.void',customer_id=row['customer_id'])
        if row['opportunity_id']:
            await require_permission(connection,'actual.void',opportunity_id=row['opportunity_id'])

    async def require_operator(self, connection):
        if not await connection.fetchval("SELECT security.management_actor() AND security.authorization_has('access.console')"):
            raise PermissionError("旧实绩接口仅允许运营进行历史证据修复；新实际请使用运营期间确认")

    async def read(
        self,
        connection,
        *,
        period="year",
        basis="effective",
        kind=None,
        customer_id=None,
        opportunity_id=None,
        team_id=None,
        owner_id=None,
        customer_ids=None,
        offset=0,
        limit=50,
    ):
        if basis not in {"effective", "entries", "historical", "auto"}:
            raise ValueError("Unknown asset basis")
        as_of = today()
        scoped = [dict(r) for r in await connection.fetch("""SELECT a.*,false AS can_void,
          COALESCE(c.name,reference.value->>'name') AS customer_name,c.data_kind,
          u.display_name AS owner_name,o.name AS opportunity_name,t.name AS team_name,
          a.confirmed_at AS created_at,NULL::text AS confirmed_by
          FROM crm.effective_actual a LEFT JOIN crm.customer c ON c.id=a.customer_id
          LEFT JOIN LATERAL (SELECT security.customer_reference(a.customer_id) AS value) reference ON true
          LEFT JOIN crm.opportunity o ON o.id=a.opportunity_id
          LEFT JOIN platform.user_ref u ON u.id=a.owner_user_ref_id
          LEFT JOIN platform.team t ON t.id=a.owner_team_id
          WHERE reference.value IS NOT NULL
           AND ($1::text IS NULL OR a.kind=$1) AND ($2::uuid IS NULL OR a.customer_id=$2)
           AND ($3::uuid IS NULL OR a.opportunity_id=$3) AND ($4::uuid IS NULL OR a.owner_team_id=$4)
           AND ($5::uuid IS NULL OR a.owner_user_ref_id=$5) AND ($6::uuid[] IS NULL OR a.customer_id=ANY($6::uuid[]))
          ORDER BY a.year DESC,a.quarter DESC,a.occurred_on DESC NULLS LAST,a.id""",
          kind,customer_id,opportunity_id,team_id,owner_id,customer_ids)]
        facts, warning = date_scoped_actuals(scoped, period_start(period,as_of), as_of)
        summary = {**actual_totals(facts), **warning,
                   'customer_count':len({r['customer_id'] for r in facts}), 'entry_count':len(facts),
                   'unlinked_count':sum(r['opportunity_id'] is None for r in facts),
                   'first_date':min((r['occurred_on'] for r in facts if r['occurred_on']),default=None)}
        if customer_id:
            all_rows = facts
        else:
            customers = defaultdict(list)
            for row in facts: customers[row['customer_id']].append(row)
            all_rows = []
            for cid, group in customers.items():
                all_rows.append({'customer_id':str(cid),'customer_name':group[0]['customer_name'],
                    'owner_name':' / '.join(sorted({r['owner_name'] for r in group if r['owner_name']})),
                    'team_name':' / '.join(sorted({r['team_name'] for r in group if r['team_name']})),
                    'data_kind':group[0]['data_kind'],**actual_totals(group),'entry_count':len(group),
                    'latest_date':max((r['occurred_on'] for r in group if r['occurred_on']),default=None)})
        total = len(all_rows)
        rows = all_rows[offset:offset+limit]
        # Portfolio is deliberately independent of actual-date filters and map
        # activity. Keep customer_count for the existing actuals pagination.
        portfolio = await connection.fetchrow(
            """WITH customers AS MATERIALIZED (
              SELECT c.id FROM security.customer_portfolio_scope() c
              WHERE ($1::uuid IS NULL OR c.id=$1)
                AND ($2::uuid IS NULL OR EXISTS(SELECT 1 FROM crm.opportunity o
                  WHERE o.id=$2 AND o.customer_id=c.id AND o.deleted_at IS NULL))
                AND ($3::uuid IS NULL OR c.owner_team_id=$3 OR EXISTS (
                  SELECT 1 FROM platform.team_membership tm JOIN platform.user_ref member ON member.id=tm.user_ref_id
                  WHERE tm.user_ref_id=c.claimant_id AND tm.team_id=$3
                    AND tm.workspace_id=c.workspace_id AND member.status='active'
                    AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to))
                AND ($4::uuid IS NULL OR c.claimant_id=$4)
                AND ($5::uuid[] IS NULL OR c.id=ANY($5::uuid[]))
            ), opportunities AS (
              SELECT o.amount FROM crm.opportunity o JOIN customers c ON c.id=o.customer_id
              WHERE o.deleted_at IS NULL AND o.status='open'
                AND ($2::uuid IS NULL OR o.id=$2)
            ) SELECT (SELECT count(*) FROM customers) AS portfolio_customer_count,
                COALESCE(sum(amount) FILTER(WHERE amount>=0),0) AS acv_amount,
                count(*) FILTER(WHERE amount IS NULL OR amount<0) AS unknown_acv_count
              FROM opportunities""",
            customer_id, opportunity_id, team_id, owner_id, customer_ids,
        )
        summary.update(dict(portfolio))
        return dict(
            summary=summary,
            items=to_jsonable_python(rows),
            total=total,
            has_more=offset + len(rows) < total,
            as_of=as_of,
            period=period,
            basis="effective",
            historical_count=sum(r["source"]=="historical" for r in facts),
            start_date=period_start(period, as_of),
            view="entries" if customer_id else "customers",
        )

    async def create(self, connection, actor, data):
        await self.require_operator(connection)
        await connection.execute('SELECT security.finance_write_lock()')
        await require_permission(connection, 'actual.create', customer_id=data['customer_id'])
        customer = await connection.fetchval(
            "SELECT id FROM crm.customer WHERE id=$1 AND deleted_at IS NULL", data["customer_id"]
        )
        if not customer:
            raise LookupError("客户不存在或不在当前权限范围内")
        if data.get("opportunity_id") and not await connection.fetchval(
            "SELECT id FROM crm.opportunity WHERE id=$1 AND customer_id=$2 AND deleted_at IS NULL",
            data["opportunity_id"],
            customer,
        ):
            raise ValueError("商机不属于当前客户，请重新选择")
        if data.get('opportunity_id'):
            await require_permission(connection, 'actual.create', opportunity_id=data['opportunity_id'])
        # Same request may be retried after a lost response, but never reused for changed content.
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", str(data["request_id"]))
        old = await connection.fetchrow(
            "SELECT * FROM crm.customer_actual WHERE workspace_id=$1::uuid AND request_id=$2",
            actor.workspace_id,
            data["request_id"],
        )
        if old:
            if any(
                old[k] != data.get(k)
                for k in ("customer_id", "opportunity_id", "kind", "amount", "occurred_on", "source_ref", "note")
            ):
                raise FileExistsError("同一次提交的内容已变化，请刷新后重新确认")
            if old.get("tax_basis", "unknown") != data.get("tax_basis", "unknown"):
                raise FileExistsError("同一次提交的税口径已变化，请刷新后重新确认")
            return dict(id=str(old["id"]), replayed=True, voided=old["voided_at"] is not None)
        record = await connection.fetchrow(
            """INSERT INTO crm.customer_actual
          (workspace_id,customer_id,opportunity_id,kind,amount,occurred_on,source_ref,note,request_id,
           confirmed_by_user_ref_id,tax_basis) VALUES($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10::uuid,$11)
          RETURNING id::text""",
            actor.workspace_id,
            customer,
            data.get("opportunity_id"),
            data["kind"],
            data["amount"],
            data["occurred_on"],
            data["source_ref"],
            data["note"],
            data["request_id"],
            actor.user_id,
            data.get("tax_basis", "unknown"),
        )
        return dict(id=record["id"], replayed=False, voided=False)

    async def void(self, connection, actor, record_id, reason):
        await self.require_operator(connection)
        await connection.execute('SELECT security.finance_write_lock()')
        await require_permission(connection, 'actual.void')
        row = await connection.fetchrow(
            "SELECT id,voided_at,customer_id,opportunity_id FROM crm.customer_actual WHERE id=$1 FOR UPDATE", record_id
        )
        if not row:
            raise LookupError("记录不存在或不在当前权限范围内")
        await require_permission(connection, 'actual.void', customer_id=row['customer_id'])
        if row['opportunity_id']:
            await require_permission(connection, 'actual.void', opportunity_id=row['opportunity_id'])
        if not row["voided_at"]:
            await connection.execute(
                """UPDATE crm.customer_actual SET voided_at=clock_timestamp(),
              voided_by_user_ref_id=$2::uuid,void_reason=$3 WHERE id=$1""",
                record_id,
                actor.user_id,
                reason,
            )
        return dict(id=str(record_id), voided=True)
