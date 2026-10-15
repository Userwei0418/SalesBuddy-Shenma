"""Complete subject-scoped profile facts, before UI pagination."""

from datetime import date
from collections import defaultdict
from decimal import Decimal
from sales_backend.domain.finance_totals import actual_totals, date_scoped_actuals

from sales_backend.repositories.profile_scope import resolve_scope


class ProfilePerformanceRepository:
    async def scope(self, connection, actor, **kwargs):
        return await resolve_scope(connection, actor, **kwargs)

    async def members(self, connection, actor, scope):
        return scope['member_ids']

    async def targets(self, connection, actor, scope, period_start):
        return {r['kind']: r['amount'] for r in await connection.fetch(
            """SELECT kind,amount FROM crm.sales_target WHERE workspace_id=$1::uuid
             AND period_type='quarter' AND period_start=$2 AND kind IN ('collection','recognized')
             AND scope_type=$3 AND user_ref_id IS NOT DISTINCT FROM $4::uuid
             AND team_id IS NOT DISTINCT FROM $5::uuid
             AND (scope_type<>'department' OR department_code='sales')""",
            actor.workspace_id, period_start, scope['scope'], scope['user_id'], scope['team_id'])}

    async def facts(self, connection, members, year, as_of, visit_start, scope, *,
                    target_start, target_end, structure_start=None, structure_end=None):
        row = await connection.fetchrow(
            """WITH opportunities AS (
              SELECT o.amount,o.status,o.created_at FROM crm.opportunity o
              WHERE o.deleted_at IS NULL AND o.status IN ('open','won')
                AND (($2='team' AND o.owner_team_id=ANY($3::uuid[])) OR
                  ($2<>'team' AND o.owner_user_ref_id=ANY($1::uuid[])))
            ), structure_opportunities AS (
              SELECT * FROM opportunities WHERE
                ($6::date IS NULL OR created_at>=($6::date::timestamp AT TIME ZONE 'Asia/Shanghai'))
                AND ($7::date IS NULL OR created_at<(($7::date+1)::timestamp AT TIME ZONE 'Asia/Shanghai'))
            ), visits AS (
              SELECT follow_up_score,customer_id FROM activity.visit WHERE recorder_user_ref_id=ANY($1::uuid[])
                 AND deleted_at IS NULL AND status IN ('confirmed','archived')
                 AND interaction_at >= ($4::date::timestamp AT TIME ZONE 'Asia/Shanghai')
                 AND interaction_at < (($5::date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
            ) SELECT
              (SELECT COALESCE(sum(amount),0) FROM opportunities WHERE status='open') AS active_opportunity_amount,
              (SELECT COALESCE(sum(amount),0) FROM opportunities WHERE status='won') AS won_amount,
              (SELECT avg(follow_up_score) FROM visits) AS followup_score,
              (SELECT count(*) FROM visits) AS followup_count,
              (SELECT count(DISTINCT customer_id) FROM visits) AS followup_customer_count,
              (SELECT count(follow_up_score) FROM visits) AS followup_evaluated_count,
              (SELECT count(*) FROM structure_opportunities) AS opportunity_count,
              (SELECT count(*) FROM structure_opportunities WHERE amount>=0) AS opportunity_evaluated_count,
              (SELECT count(*) FROM structure_opportunities WHERE amount>=500000) AS opportunity_ab_count
            """, members, scope['scope'], scope['team_ids'], visit_start, as_of, structure_start, structure_end)
        values = dict(row)
        facts = [dict(r) for r in await connection.fetch("""SELECT a.* FROM crm.effective_actual a
          WHERE security.customer_reference(a.customer_id) IS NOT NULL
            AND (a.occurred_on IS NULL OR a.occurred_on<=$4::date)
            AND (($2='team' AND a.owner_team_id=ANY($3::uuid[])) OR
                 ($2<>'team' AND a.owner_user_ref_id=ANY($1::uuid[])))""",members,scope['scope'],scope['team_ids'],as_of)]
        # Target selection is explicitly a complete quarter, independent of the
        # query timestamp. Exact-date retention remains date scoped with warnings.
        quarter_facts, warning = date_scoped_actuals(facts,target_start,target_end)
        projection = {**actual_totals(quarter_facts),**warning}
        values.update(recognized=projection['recognized_amount'],collection=projection['collection_amount'],finance_actuals=projection)
        previous, previous_warning = date_scoped_actuals(facts,date(year-1,1,1),date(year-1,12,31))
        current, current_warning = date_scoped_actuals(facts,date(year,1,1),min(as_of,date(year,12,31)))
        prior_by_customer, current_by_customer = defaultdict(list), defaultdict(list)
        for r in previous: prior_by_customer[r['customer_id']].append(r)
        for r in current: current_by_customer[r['customer_id']].append(r)
        cohort = {cid:actual_totals(rows) for cid,rows in prior_by_customer.items()}
        prior_complete = bool(cohort) and all(v['recognized_complete'] for v in cohort.values()) and not previous_warning['unallocated_period_count']
        cohort = {cid:v for cid,v in cohort.items() if v['recognized_amount'] is not None and Decimal(v['recognized_amount'])>0}
        before_sum = sum((Decimal(v['recognized_amount']) for v in cohort.values()),Decimal(0)) if cohort and prior_complete else None
        now = [actual_totals(current_by_customer[cid]) for cid in cohort]
        current_complete = prior_complete and bool(now) and all(v['recognized_complete'] for v in now) and not current_warning['unallocated_period_count']
        now_sum = sum((Decimal(v['recognized_amount']) for v in now),Decimal(0)) if current_complete else None
        values.update(retention_previous=before_sum,retention_current=now_sum,retention_customers=len(cohort),
                      retention_precision={**current_warning,'previous_unallocated_period_count':previous_warning['unallocated_period_count']},
                      retention_complete=current_complete and not previous_warning['unallocated_period_count'])
        return values
