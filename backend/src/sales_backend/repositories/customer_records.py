"""Shared customer/one-opportunity records, always restricted by the caller transaction's RLS."""

from sales_backend.domain.visit_dates import visit_date_fields
from sales_backend.repositories.attribute_overlay import overlay_risk_attributes, overlay_visit_attributes
from sales_backend.repositories.tasks import TaskRepository


async def related_records(connection, customer_id, *, opportunity_id=None):
    visits = await connection.fetch(
        """
            SELECT v.id::text, v.customer_id::text, v.opportunity_id::text, v.interaction_at, v.interaction_mode_code,
                   v.partner_id::text,COALESCE(partner.name,v.partner_name_snapshot) AS partner_name,
                   v.original_recorder_name,manager.display_name AS manager_name,
                   COALESCE((SELECT jsonb_agg(jsonb_build_object('id',linked.id::text,'name',linked.name)
                     ORDER BY linked.name,linked.id) FROM activity.visit_opportunity vo
                     JOIN crm.opportunity linked ON linked.id=vo.opportunity_id AND linked.deleted_at IS NULL
                     WHERE vo.visit_id=v.id),'[]'::jsonb) AS linked_opportunities,
                   v.visit_location, v.duration_minutes, v.expectation_code,
                   v.follow_up_record, v.next_action, v.status,
                   v.contact_name_snapshot, v.contact_title_snapshot,
                   v.attributes, v.import_meta, v.quality_review, v.first_visit_profile,
                   v.is_first_visit, v.follow_up_score, v.visit_goal, v.created_at, v.recorded_on,
                   v.partner_name_snapshot,
                   (SELECT jsonb_agg(jsonb_build_object('id',cu.id::text,'name',cu.display_name))
                    FROM activity.visit_participant vp JOIN platform.user_ref cu ON cu.id=vp.user_ref_id
                    WHERE vp.visit_id=v.id AND cu.workspace_id=v.workspace_id) AS collaborators,
                   u.display_name AS recorder_name, o.name AS opportunity_name,
                   v.customer_type_code_snapshot AS customer_type, creator.display_name AS creator_name
            FROM activity.visit v
            LEFT JOIN platform.user_ref u ON u.id = v.recorder_user_ref_id
            LEFT JOIN platform.user_ref manager ON manager.id=v.manager_user_ref_id
            LEFT JOIN crm.partner partner ON partner.id=v.partner_id
            LEFT JOIN platform.user_ref creator ON creator.id = v.created_by_user_ref_id
            LEFT JOIN crm.opportunity o ON o.id = v.opportunity_id
            WHERE (v.customer_id = $1::uuid OR (v.opportunity_id IS NULL AND EXISTS (
              SELECT 1 FROM activity.visit_opportunity vo JOIN crm.opportunity linked ON linked.id=vo.opportunity_id
              WHERE vo.visit_id=v.id AND linked.customer_id=$1::uuid AND linked.deleted_at IS NULL))
              OR (v.customer_id IS NULL AND v.opportunity_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM crm.opportunity linked WHERE linked.id=v.opportunity_id
                  AND linked.customer_id=$1::uuid AND linked.deleted_at IS NULL)))
              AND v.deleted_at IS NULL
              AND ($2::uuid IS NULL OR v.opportunity_id=$2::uuid OR (v.opportunity_id IS NULL AND EXISTS (
                SELECT 1 FROM activity.visit_opportunity vo WHERE vo.visit_id=v.id AND vo.opportunity_id=$2::uuid)))
            ORDER BY v.interaction_at DESC,v.id
            """,
        customer_id,
        opportunity_id,
    )
    risks = await connection.fetch(
        """
            SELECT id::text, opportunity_id::text, title, description, severity_code, status, evidence,
                   opened_at, due_at, resolution_note, attributes, import_meta,
                   source_code, suggested_action, agent_key, source_run_id
            FROM insight.risk
            WHERE customer_id = $1::uuid AND deleted_at IS NULL
              AND ($2::uuid IS NULL OR opportunity_id=$2::uuid)
            ORDER BY CASE WHEN status IN ('new','pending','in_progress','escalated') THEN 0 ELSE 1 END,
                     opened_at DESC,id
            """,
        customer_id,
        opportunity_id,
    )
    return {
        "visits": [visit_date_fields(overlay_visit_attributes(dict(row))) for row in visits],
        "risks": [overlay_risk_attributes(dict(row)) for row in risks],
        "tasks": await TaskRepository().list(
            connection, status=None, customer_id=customer_id, opportunity_id=opportunity_id, limit=None
        ),
    }
