"""Customer-owned actuals; no forecast/ACV inference, all totals computed before pagination."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sales_backend.domain.agent import RoleCode
from sales_backend.repositories.historical_customer_assets import read_historical_assets


def today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def period_start(period, as_of=None):
    day = as_of or today()
    return date(day.year, 1, 1) if period == "year" else None


def can_manage(actor):
    return actor.role in {RoleCode.MANAGER, RoleCode.SUPERVISOR}


class CustomerAssetRepository:
    async def read(
        self,
        connection,
        *,
        period="year",
        basis="entries",
        kind=None,
        customer_id=None,
        opportunity_id=None,
        team_id=None,
        owner_id=None,
        customer_ids=None,
        offset=0,
        limit=50,
    ):
        if basis not in {"entries", "historical", "auto"}:
            raise ValueError("Unknown asset basis")
        as_of = today()
        args = [period_start(period, as_of), as_of, kind, customer_id, opportunity_id, team_id, owner_id, customer_ids]
        cte = """WITH facts AS (
          SELECT a.*,COALESCE(c.name,reference.value->>'name') AS customer_name,
            c.data_kind,u.display_name AS owner_name,
            o.name AS opportunity_name,t.name AS team_name,conf.display_name AS confirmed_by
          FROM crm.customer_actual a LEFT JOIN crm.customer c ON c.id=a.customer_id
          LEFT JOIN LATERAL (SELECT security.customer_reference(a.customer_id) AS value) reference ON true
          LEFT JOIN crm.opportunity o ON o.id=a.opportunity_id
          LEFT JOIN platform.user_ref u ON u.id=COALESCE(o.owner_user_ref_id,c.owner_user_ref_id)
          LEFT JOIN platform.team t ON t.id=COALESCE(o.owner_team_id,c.owner_team_id)
          LEFT JOIN platform.user_ref conf ON conf.id=a.confirmed_by_user_ref_id
          WHERE a.voided_at IS NULL AND reference.value IS NOT NULL
            AND ($1::date IS NULL OR a.occurred_on >= $1) AND a.occurred_on <= $2
            AND ($3::text IS NULL OR a.kind=$3) AND ($4::uuid IS NULL OR a.customer_id=$4)
            AND ($5::uuid IS NULL OR a.opportunity_id=$5)
            AND ($6::uuid IS NULL OR COALESCE(o.owner_team_id,c.owner_team_id)=$6)
            AND ($7::uuid IS NULL OR COALESCE(o.owner_user_ref_id,c.owner_user_ref_id)=$7)
            AND ($8::uuid[] IS NULL OR a.customer_id=ANY($8::uuid[]))
        ) """
        summary = dict(
            await connection.fetchrow(
                cte
                + """SELECT
          sum(amount) FILTER(WHERE kind='recognized') AS recognized_amount,
          sum(amount) FILTER(WHERE kind='collection') AS collection_amount,
          count(*) FILTER(WHERE kind='recognized') AS recognized_count,
          count(*) FILTER(WHERE kind='collection') AS collection_count,
          count(DISTINCT customer_id) AS customer_count,count(*) AS entry_count,
          count(*) FILTER(WHERE opportunity_id IS NULL) AS unlinked_count,
          min(occurred_on) AS first_date FROM facts""",
                *args,
            )
        )
        if customer_id:
            query = """SELECT id::text,customer_id::text,customer_name,opportunity_id::text,opportunity_name,
              kind,amount,occurred_on,source_ref,note,created_at,confirmed_by,data_kind
              FROM facts ORDER BY occurred_on DESC,created_at DESC,id LIMIT $9 OFFSET $10"""
            total = summary["entry_count"]
        else:
            query = """SELECT customer_id::text,customer_name,
              string_agg(DISTINCT owner_name,'、' ORDER BY owner_name) AS owner_name,
              string_agg(DISTINCT team_name,'、' ORDER BY team_name) AS team_name,data_kind,
              sum(amount) FILTER(WHERE kind='recognized') AS recognized_amount,
              sum(amount) FILTER(WHERE kind='collection') AS collection_amount,
              count(*) AS entry_count,max(occurred_on) AS latest_date
              FROM facts GROUP BY customer_id,customer_name,data_kind
              ORDER BY sum(amount) DESC,customer_id LIMIT $9 OFFSET $10"""
            total = summary["customer_count"]
        historical_summary, historical_page = await read_historical_assets(
            connection, args, customer_id=customer_id, limit=limit, offset=offset)
        selected_basis = ("historical" if historical_summary["entry_count"] else "entries") if basis == "auto" else basis
        if selected_basis == "historical":
            summary = historical_summary
            total = summary["entry_count"] if customer_id else summary["customer_count"]
            rows = await historical_page()
        else:
            rows = await connection.fetch(cte + query, *args, limit, offset)
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
            items=[dict(r) for r in rows],
            total=total,
            has_more=offset + len(rows) < total,
            as_of=as_of,
            period=period,
            basis=selected_basis,
            historical_count=historical_summary["entry_count"],
            start_date=period_start(period, as_of),
            view="entries" if customer_id else "customers",
        )

    async def create(self, connection, actor, data):
        if not can_manage(actor):
            raise PermissionError("仅管理人员可确认经营实绩")
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
            return dict(id=str(old["id"]), replayed=True, voided=old["voided_at"] is not None)
        record = await connection.fetchrow(
            """INSERT INTO crm.customer_actual
          (workspace_id,customer_id,opportunity_id,kind,amount,occurred_on,source_ref,note,request_id,
           confirmed_by_user_ref_id) VALUES($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10::uuid)
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
        )
        return dict(id=record["id"], replayed=False, voided=False)

    async def void(self, connection, actor, record_id, reason):
        if not can_manage(actor):
            raise PermissionError("仅管理人员可作废经营实绩")
        row = await connection.fetchrow(
            "SELECT id,voided_at FROM crm.customer_actual WHERE id=$1 FOR UPDATE", record_id
        )
        if not row:
            raise LookupError("记录不存在或不在当前权限范围内")
        if not row["voided_at"]:
            await connection.execute(
                """UPDATE crm.customer_actual SET voided_at=clock_timestamp(),
              voided_by_user_ref_id=$2::uuid,void_reason=$3 WHERE id=$1""",
                record_id,
                actor.user_id,
                reason,
            )
        return dict(id=str(record_id), voided=True)
