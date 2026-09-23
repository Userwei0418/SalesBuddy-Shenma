BEGIN;
SET LOCAL check_function_bodies = on;
SET LOCAL search_path = pg_catalog, public;

-- Replace this existing function in place: its owner and explicit/default ACL
-- are retained by CREATE OR REPLACE. No RLS policy or write predicate changes.
-- Memberships are evaluated once per call, not persisted or trusted from a
-- client-provided authorization cache. The current clock still governs every
-- role, department and participation validity interval.
-- PL/pgSQL retains the statement plan across nested authorization calls. Its
-- inputs (current identity, clock and memberships) are evaluated on each call;
-- no authorization result is cached. The combined query in a SQL function was
-- still costly inside the outer authorization functions in the measured path;
-- the equivalent PL/pgSQL statement reduced that nested-call execution cost.
CREATE OR REPLACE FUNCTION security.fde_customer_in_scope(p_customer uuid) RETURNS boolean
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 BEGIN
 RETURN (WITH identity AS MATERIALIZED (
   SELECT common.current_workspace_id() AS workspace_id,
     common.current_user_ref_id() AS user_id, common.current_role_code() AS role_code
   WHERE common.current_role_code() IN ('fde','fde_lead')
 ), participants AS MATERIALIZED (
   SELECT DISTINCT p.user_ref_id
   FROM identity i
   JOIN crm.opportunity o ON o.workspace_id=i.workspace_id
     AND o.customer_id=p_customer AND o.deleted_at IS NULL
   JOIN crm.opportunity_participant p ON p.workspace_id=o.workspace_id AND p.opportunity_id=o.id
   WHERE p.participant_role='fde'
     AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 ), people AS MATERIALIZED (
   SELECT user_id FROM identity
   UNION SELECT user_ref_id FROM participants
 ), memberships AS MATERIALIZED (
   SELECT DISTINCT u.id AS user_id, tm.team_id, rb.role_code
   FROM identity i
   JOIN people person ON true
   JOIN platform.user_ref u ON u.id=person.user_id AND u.workspace_id=i.workspace_id
   JOIN platform.role_binding rb ON rb.user_ref_id=u.id AND rb.workspace_id=u.workspace_id
   JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=u.workspace_id
   WHERE u.status='active' AND u.deleted_at IS NULL
     AND rb.role_code IN ('fde','fde_lead') AND tm.membership_role IN ('fde','fde_lead')
     AND (rb.role_code<>'fde_lead' OR tm.membership_role='fde_lead')
     AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)
     AND t.status='active' AND t.deleted_at IS NULL
     AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
     AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 ), actor_memberships AS MATERIALIZED (
   SELECT m.team_id FROM memberships m JOIN identity i
     ON m.user_id=i.user_id AND m.role_code=i.role_code
 )
 SELECT EXISTS (
   SELECT 1 FROM identity i JOIN participants p ON true
   JOIN memberships member ON member.user_id=p.user_ref_id
   WHERE EXISTS (SELECT 1 FROM actor_memberships)
     AND (p.user_ref_id=i.user_id OR (i.role_code='fde_lead'
       AND EXISTS (SELECT 1 FROM actor_memberships mine WHERE mine.team_id=member.team_id)))
 ));
 END;
 $$;

COMMIT;
