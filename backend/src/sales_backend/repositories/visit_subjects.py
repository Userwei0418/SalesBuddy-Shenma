"""SQL subject predicates shared by bounded visit history and detail aggregates.

Customer and opportunity values stay bound as $1/$2. Reads retain the original
RLS-protected tables and exact customer/bridge membership conditions.
"""


def visit_subject_predicate(*, opportunity_scoped=False):
    customer = """(v.customer_id=$1::uuid OR (v.opportunity_id IS NULL AND EXISTS (
        SELECT 1 FROM activity.visit_opportunity vo JOIN crm.opportunity linked ON linked.id=vo.opportunity_id
        WHERE vo.visit_id=v.id AND linked.customer_id=$1::uuid AND linked.deleted_at IS NULL))
        OR (v.customer_id IS NULL AND v.opportunity_id IS NOT NULL AND EXISTS (
          SELECT 1 FROM crm.opportunity linked WHERE linked.id=v.opportunity_id
            AND linked.customer_id=$1::uuid AND linked.deleted_at IS NULL)))"""
    common = f"{customer} AND v.deleted_at IS NULL"
    if not opportunity_scoped:
        return f"({common} OR security.customer_claim_visit_match(v.id,$1::uuid,NULL))"
    # Keep this cheap necessary condition separate from the bridge subquery.
    # PostgreSQL can reject unrelated principal IDs before expensive row-level
    # authorization. The exact customer/bridge predicate below remains intact.
    legacy = """(v.opportunity_id=$2::uuid OR v.opportunity_id IS NULL)
        AND (v.opportunity_id=$2::uuid OR (v.opportunity_id IS NULL AND EXISTS (
          SELECT 1 FROM activity.visit_opportunity vo
          WHERE vo.visit_id=v.id AND vo.opportunity_id=$2::uuid)))
        AND """ + common  # noqa: S608 -- Only a fixed backend predicate is concatenated.
    return f"({legacy} OR security.customer_claim_visit_match(v.id,$1::uuid,$2::uuid))"
