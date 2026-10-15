-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V224__competency_review_generations.sql.
-- Original SHA-256: cdf83dd1f26db9cb0d68d6d78fd4091f37c7f0183c14cd5835fe8cec938920b3. See shenma_whole_121_manifest.json.
BEGIN;
-- SOURCE ONLY: not applied by this delivery. V160 and V222 are both supported.
ALTER TABLE insight.sales_competency_review
 ADD COLUMN generation_no integer,
 ADD COLUMN trigger_type text,
 ADD COLUMN trigger_id uuid,
 ADD COLUMN trigger_version integer,
 ADD COLUMN input_fingerprint text,
 ADD COLUMN retry_of_review_id uuid,
 ADD COLUMN job_id uuid,
 ADD COLUMN evaluation_state text NOT NULL DEFAULT 'legacy_unknown';
-- Only historical generation metadata is backfilled. This does not assert that
-- old inputs were frozen. Preserve original updated_at and all queue rows.
DO $backfill$
DECLARE trigger_state "char";
BEGIN
 SELECT tgenabled INTO trigger_state FROM pg_trigger
 WHERE tgrelid='insight.sales_competency_review'::regclass
  AND tgname='trg_sales_competency_review_touch' AND NOT tgisinternal
  AND tgfoid='common.touch_runtime_updated_at()'::regprocedure
  AND tgtype=19 AND tgnargs=0 AND tgqual IS NULL AND tgattr::text=''
  AND tgenabled IN ('O','D','R','A');
 IF NOT FOUND THEN RAISE EXCEPTION 'V224_UNKNOWN_REVIEW_TOUCH_TRIGGER'; END IF;
 ALTER TABLE insight.sales_competency_review DISABLE TRIGGER trg_sales_competency_review_touch;
 WITH numbered AS (
  SELECT id,row_number() OVER(PARTITION BY workspace_id,subject_user_ref_id,review_date
   ORDER BY created_at,id)::integer AS number FROM insight.sales_competency_review
 ) UPDATE insight.sales_competency_review r SET generation_no=n.number FROM numbered n WHERE r.id=n.id;
 IF trigger_state='O' THEN
  ALTER TABLE insight.sales_competency_review ENABLE TRIGGER trg_sales_competency_review_touch;
 ELSIF trigger_state='R' THEN
  ALTER TABLE insight.sales_competency_review ENABLE REPLICA TRIGGER trg_sales_competency_review_touch;
 ELSIF trigger_state='A' THEN
  ALTER TABLE insight.sales_competency_review ENABLE ALWAYS TRIGGER trg_sales_competency_review_touch;
 END IF;
 -- D deliberately remains disabled. DDL and backfill roll back together on failure.
END $backfill$;
ALTER TABLE insight.sales_competency_review
 ALTER COLUMN generation_no SET NOT NULL,
 ALTER COLUMN generation_no SET DEFAULT 1,
 DROP CONSTRAINT sales_competency_review_workspace_id_subject_user_ref_id_re_key,
 ADD CONSTRAINT competency_generation_unique UNIQUE(workspace_id,subject_user_ref_id,review_date,generation_no),
 ADD CONSTRAINT competency_generation_positive CHECK(generation_no>0),
 ADD CONSTRAINT competency_trigger_check CHECK(trigger_type IS NULL OR trigger_type IN ('manual','visit.archived','visit.supplemented')),
 ADD CONSTRAINT competency_state_check CHECK(evaluation_state IN ('legacy_unknown','assessed','partially_assessed','insufficient_data')),
 ADD CONSTRAINT competency_v2_snapshot_check CHECK(input_fingerprint IS NULL OR COALESCE((
  input_fingerprint ~ '^[0-9a-f]{64}$' AND trigger_type IS NOT NULL AND job_id IS NOT NULL
  AND input_snapshot->>'contract_version'='sales.competency.v2'
  AND input_snapshot->>'input_fingerprint'=input_fingerprint
  AND jsonb_typeof(input_snapshot->'facts')='object'
  AND jsonb_typeof(input_snapshot->'score_basis')='object'
  AND jsonb_typeof(input_snapshot->'source_hashes')='object'),false));
CREATE INDEX competency_generation_fingerprint ON insight.sales_competency_review
 (workspace_id,subject_user_ref_id,review_date,input_fingerprint,generation_no DESC)
 WHERE input_fingerprint IS NOT NULL;

CREATE FUNCTION insight.protect_competency_generation() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF OLD.input_fingerprint IS NOT NULL THEN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'COMPETENCY_GENERATION_IMMUTABLE' USING ERRCODE='23514'; END IF;
  IF (to_jsonb(NEW)-ARRAY['status','overall_score','dimension_scores','summary','strengths','improvements',
    'evidence','input_snapshot','model_id','reviewed_at','error_code','error_detail','evaluation_state','updated_at'])
   IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','overall_score','dimension_scores','summary','strengths','improvements',
    'evidence','input_snapshot','model_id','reviewed_at','error_code','error_detail','evaluation_state','updated_at'])
   OR (NEW.input_snapshot-ARRAY['score_summary','inference_route'])
    IS DISTINCT FROM (OLD.input_snapshot-ARRAY['score_summary','inference_route'])
   OR (OLD.status='succeeded' AND (to_jsonb(NEW)-'updated_at') IS DISTINCT FROM (to_jsonb(OLD)-'updated_at')) THEN
   RAISE EXCEPTION 'COMPETENCY_GENERATION_IMMUTABLE' USING ERRCODE='23514';
  END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER competency_generation_immutable BEFORE UPDATE OR DELETE ON insight.sales_competency_review
 FOR EACH ROW EXECUTE FUNCTION insight.protect_competency_generation();
REVOKE ALL ON FUNCTION insight.protect_competency_generation() FROM PUBLIC;

-- Keep the original function identity, SECURITY DEFINER attributes and ACL.
-- This retires only daily *creation*, never generic maintenance or legacy retry.
CREATE OR REPLACE FUNCTION ops.enqueue_daily_sales_competency_reviews() RETURNS integer
 LANGUAGE plpgsql SECURITY DEFINER
 SET search_path TO 'pg_catalog','platform','config','insight','ops' AS $$
BEGIN
 RETURN 0;
END; $$;

-- Patch only the candidate predicate of the installed, audited claim body.
-- The original definition supplies every attribute; CREATE OR REPLACE preserves ACL.
DO $migration$
DECLARE fn oid:='ops.claim_job(text,integer,text[],boolean)'::regprocedure;
 body text; definition text; anchor text; addition text; changed text;
BEGIN
 SELECT prosrc,pg_get_functiondef(oid) INTO body,definition FROM pg_proc WHERE oid=fn;
 IF md5(body) NOT IN ('539a9cfcc5b7bc918435470741fa2b9b','da40c5fd0edc3fddb7da970548b28338') THEN
  RAISE EXCEPTION 'V224_UNRECOGNIZED_CLAIM_BODY';
 END IF;
 anchor:=$anchor$   AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=candidate.id)
$anchor$;
 addition:=$addition$   -- BEGIN GROWTH V224: same subject is serial before acquiring a paid lease.
   AND (candidate.job_type<>'sales_competency.review' OR NOT EXISTS (
    SELECT 1 FROM ops.job predecessor
    JOIN insight.sales_competency_review earlier ON earlier.id=predecessor.aggregate_id
     AND earlier.workspace_id=predecessor.workspace_id
    JOIN insight.sales_competency_review current_review ON current_review.id=candidate.aggregate_id
     AND current_review.workspace_id=candidate.workspace_id
    WHERE predecessor.workspace_id=candidate.workspace_id
     AND predecessor.job_type='sales_competency.review'
     AND predecessor.aggregate_type='sales_competency_review'
     AND predecessor.id<>candidate.id AND earlier.subject_user_ref_id=current_review.subject_user_ref_id
     AND predecessor.status IN ('queued','failed','running')
     AND ((earlier.review_date,earlier.generation_no,earlier.id,predecessor.created_at,predecessor.id)
       <(current_review.review_date,current_review.generation_no,current_review.id,candidate.created_at,candidate.id)
       OR (predecessor.status='running' AND predecessor.locked_until>clock_timestamp()))
   ))
   -- END GROWTH V224
$addition$;
 IF (length(body)-length(replace(body,anchor,'')))<>length(anchor)
  OR (length(definition)-length(replace(definition,body,'')))<>length(body) THEN
  RAISE EXCEPTION 'V224_CLAIM_ANCHOR_MISMATCH';
 END IF;
 changed:=replace(body,anchor,anchor||addition);
 IF replace(changed,addition,'') IS DISTINCT FROM body THEN
  RAISE EXCEPTION 'V224_CLAIM_SCOPE_MISMATCH';
 END IF;
 EXECUTE replace(definition,body,changed);
END $migration$;
-- Compensation after a business grant was revoked. This is not a general
-- review write API: only the current exact worker lease can close its own row.
CREATE FUNCTION ops.fail_competency_review(p_job_id uuid,p_lease_token uuid,
 p_error_code text,p_retryable boolean) RETURNS TABLE(review_id uuid,status text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $failure$
DECLARE claimed record; review record; next_status text;
BEGIN
 IF p_job_id IS NULL OR p_lease_token IS NULL OR p_retryable IS NULL
  OR p_error_code IS NULL OR p_error_code !~ '^[A-Za-z0-9_.:-]{1,100}$' THEN
  RAISE EXCEPTION 'INVALID_COMPETENCY_FAILURE' USING ERRCODE='22023';
 END IF;
 SELECT j.* INTO claimed FROM ops.job j WHERE j.id=p_job_id
  AND j.workspace_id=common.current_workspace_id()
  AND j.payload->>'user_id'=common.current_user_ref_id()::text
  AND j.job_type='sales_competency.review' AND j.aggregate_type='sales_competency_review'
  AND j.status='running' AND j.lease_token=p_lease_token AND j.locked_until>clock_timestamp()
  AND security.workspace_is_active(j.workspace_id) FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'COMPETENCY_FAILURE_LEASE_INVALID' USING ERRCODE='P7409'; END IF;
 SELECT r.id,r.status INTO review FROM insight.sales_competency_review r
  WHERE r.id=claimed.aggregate_id AND r.workspace_id=claimed.workspace_id
   AND r.subject_user_ref_id=common.current_user_ref_id()
   AND (r.job_id=claimed.id OR (r.job_id IS NULL AND r.input_fingerprint IS NULL)) FOR UPDATE;
 IF NOT FOUND OR review.status NOT IN ('queued','running','failed') THEN
  RAISE EXCEPTION 'COMPETENCY_FAILURE_REVIEW_INVALID' USING ERRCODE='P7409';
 END IF;
 IF claimed.locked_until<=clock_timestamp() THEN
  RAISE EXCEPTION 'COMPETENCY_FAILURE_LEASE_INVALID' USING ERRCODE='P7409';
 END IF;
 next_status:=CASE WHEN p_retryable AND claimed.attempts<claimed.max_attempts THEN 'queued' ELSE 'failed' END;
 UPDATE insight.sales_competency_review r SET status=next_status,error_code=p_error_code,
  error_detail='后台复盘未完成，请重新生成或联系管理员'
  WHERE r.id=review.id AND r.workspace_id=claimed.workspace_id AND r.status IN ('queued','running','failed');
 RETURN QUERY SELECT review.id::uuid,next_status;
END $failure$;

-- Default privileges may have granted unrelated roles. Remove every initial
-- grant, then inherit only direct EXECUTE grantees of the existing typed claim.
-- PUBLIC/Feishu and grant options are never copied; the owner stays the same.
DO $failure_acl$
DECLARE permission record; function_owner name;
 signature text:='ops.fail_competency_review(uuid,uuid,text,boolean)';
BEGIN
 SELECT r.rolname INTO STRICT function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
  WHERE p.oid='ops.claim_job(text,integer,text[],boolean)'::regprocedure;
 FOR permission IN
  SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=signature::regprocedure
 LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',signature,
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END);
 END LOOP;
 EXECUTE format('ALTER FUNCTION %s OWNER TO %I',signature,function_owner);
 FOR permission IN
  SELECT DISTINCT r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='ops.claim_job(text,integer,text[],boolean)'::regprocedure
   AND a.privilege_type='EXECUTE' AND a.grantee<>p.proowner
   AND NOT r.rolsuper AND NOT r.rolbypassrls
   AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member')
   AND has_schema_privilege(r.oid,'ops','USAGE')
   AND has_table_privilege(r.oid,'ops.job','UPDATE')
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',signature,permission.rolname);
 END LOOP;
END $failure_acl$;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V372','Frozen sales competency generations and subject serial admission');
COMMIT;
