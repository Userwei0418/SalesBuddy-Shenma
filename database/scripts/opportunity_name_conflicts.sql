-- Read-only maintenance report. Results may contain customer business names;
-- keep them outside Git and resolve conflicts in a separately approved data repair.
BEGIN READ ONLY;
SELECT workspace_id,customer_id,
       lower(regexp_replace(name,'\s+','','g')) AS normalized_name,
       jsonb_agg(jsonb_build_object('id',id,'name',name) ORDER BY created_at,id) AS conflicts
FROM crm.opportunity WHERE deleted_at IS NULL
GROUP BY workspace_id,customer_id,lower(regexp_replace(name,'\s+','','g'))
HAVING count(*)>1;
ROLLBACK;
