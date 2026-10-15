-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V239__enterprise_business_identities.sql.
-- Original SHA-256: c8ec91a8e782919c8b182bac1e4fe8a3a6022e889627088e5429c736d9136934. See shenma_whole_121_manifest.json.
BEGIN;
-- Identity is an enterprise property, not a permission or an acquisition source.
ALTER TABLE crm.customer
 ADD COLUMN identity_created_xid xid8,
 ADD COLUMN business_identities text[] NOT NULL DEFAULT ARRAY['customer']::text[],
 ADD COLUMN partner_profile_id uuid,
 ADD COLUMN primary_partner_id uuid,
 ADD CONSTRAINT customer_business_identities_check CHECK (
   business_identities IN (ARRAY['customer']::text[],ARRAY['partner']::text[],ARRAY['customer','partner']::text[])),
 ADD CONSTRAINT customer_partner_only_type_check CHECK ('customer'=ANY(business_identities) OR customer_type_code IS NULL),
 ADD CONSTRAINT customer_self_partner_workspace_fk FOREIGN KEY(workspace_id,partner_profile_id)
   REFERENCES crm.partner(workspace_id,id),
 ADD CONSTRAINT customer_primary_partner_workspace_fk FOREIGN KEY(workspace_id,primary_partner_id)
   REFERENCES crm.partner(workspace_id,id),
 ADD CONSTRAINT customer_self_partner_unique UNIQUE(workspace_id,partner_profile_id),
 ADD CONSTRAINT customer_partner_not_self CHECK (primary_partner_id IS DISTINCT FROM partner_profile_id OR primary_partner_id IS NULL);
-- Existing rows deliberately keep NULL; defaults apply only to later INSERTs.
ALTER TABLE crm.customer ALTER COLUMN identity_created_xid SET DEFAULT pg_current_xact_id();
CREATE FUNCTION security.preserve_customer_creation_xid() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.identity_created_xid IS DISTINCT FROM OLD.identity_created_xid THEN
   RAISE EXCEPTION 'Enterprise creation transaction is immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION security.preserve_customer_creation_xid() FROM PUBLIC,salegent_feishu_worker;
CREATE TRIGGER customer_creation_xid_immutable BEFORE UPDATE OF identity_created_xid ON crm.customer
 FOR EACH ROW EXECUTE FUNCTION security.preserve_customer_creation_xid();
CREATE INDEX customer_business_identities_idx ON crm.customer USING gin(business_identities) WHERE deleted_at IS NULL;
COMMENT ON COLUMN crm.customer.business_identities IS '企业业务身份：customer/partner，可双选；不授予权限，不由来源或文本推断';
COMMENT ON COLUMN crm.customer.partner_profile_id IS '自身稳定伙伴档案；移除伙伴身份保留历史关联，不停用全局伙伴档案';
COMMENT ON COLUMN crm.customer.primary_partner_id IS '关联外部伙伴稳定ID；primary_partner_name仅名称快照，不能代表自身身份';

-- Deferred: bind own partner profile in the same enterprise transaction.
CREATE FUNCTION security.check_customer_partner_profile() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM crm.customer c WHERE c.id=NEW.id AND 'partner'=ANY(c.business_identities)
           AND c.partner_profile_id IS NULL) THEN
   RAISE EXCEPTION '合作伙伴身份必须关联自身伙伴档案' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION security.check_customer_partner_profile() FROM PUBLIC,salegent_feishu_worker;
CREATE CONSTRAINT TRIGGER customer_partner_profile_required AFTER INSERT OR UPDATE ON crm.customer
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION security.check_customer_partner_profile();

-- Provision only this already-authorized enterprise's profile. This never grants
-- partner.manage or edits the name/status of an existing global partner record.
CREATE FUNCTION security.set_customer_business_identity(p_customer uuid,p_roles text[],p_profile uuid)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c crm.customer; p crm.partner; resolved uuid;
BEGIN
 SELECT * INTO c FROM crm.customer WHERE id=p_customer AND workspace_id=common.current_workspace_id()
   AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE no_data_found; END IF;
 IF NOT COALESCE(security.authorization_customer('customer.update',c.id) OR
   (c.created_by_user_ref_id=common.current_user_ref_id() AND c.company_verified_by=common.current_user_ref_id()
    AND c.identity_created_xid=pg_current_xact_id()
    AND security.authorization_allows('customer.create',c.workspace_id,
      common.current_user_ref_id(),ARRAY[c.owner_team_id],false)),false) THEN RAISE insufficient_privilege; END IF;
 IF p_roles IS NULL OR NOT (p_roles IN (ARRAY['customer'],ARRAY['partner'],ARRAY['customer','partner'])) THEN
   RAISE EXCEPTION '请至少选择客户或合作伙伴业务身份' USING ERRCODE='22023'; END IF;
 IF c.partner_profile_id IS NOT NULL AND p_profile IS DISTINCT FROM c.partner_profile_id THEN
   RAISE EXCEPTION '已有自身伙伴档案关联不可替换或清空' USING ERRCODE='22023'; END IF;
 resolved:=c.partner_profile_id;
 IF resolved IS NULL AND p_profile IS NOT NULL AND NOT ('partner'=ANY(p_roles)) THEN
   RAISE EXCEPTION '关联自身伙伴档案需要合作伙伴业务身份' USING ERRCODE='22023'; END IF;
 IF resolved IS NULL AND 'partner'=ANY(p_roles) THEN
   -- Serialize competing enterprise bindings and partner name provisioning.
   PERFORM pg_advisory_xact_lock(hashtextextended('customer-partner:'||c.workspace_id::text,0));
   IF NOT security.authorization_has('partner.read') AND NOT security.authorization_has('partner.manage') THEN
     RAISE insufficient_privilege; END IF;
   IF p_profile IS NOT NULL THEN
     SELECT * INTO p FROM crm.partner WHERE id=p_profile AND workspace_id=c.workspace_id FOR UPDATE;
     IF NOT FOUND OR p.status<>'active' THEN
       RAISE EXCEPTION '请选择本公司有效伙伴档案' USING ERRCODE='22023'; END IF;
     IF EXISTS(SELECT 1 FROM crm.customer WHERE workspace_id=c.workspace_id AND partner_profile_id=p_profile AND id<>c.id) THEN
       RAISE EXCEPTION '伙伴档案已关联其他企业，请打开已有档案' USING ERRCODE='23505'; END IF;
     resolved:=p.id;
   ELSE
     IF EXISTS(SELECT 1 FROM crm.partner WHERE workspace_id=c.workspace_id AND normalized_name=lower(btrim(c.name))) THEN
       RAISE EXCEPTION '同名伙伴档案已存在，请明确选择已有档案ID，不能自动合并' USING ERRCODE='23505'; END IF;
     INSERT INTO crm.partner(workspace_id,name,created_by_user_ref_id,updated_by_user_ref_id)
       VALUES(c.workspace_id,c.name,common.current_user_ref_id(),common.current_user_ref_id()) RETURNING id INTO resolved;
   END IF;
 END IF;
 IF resolved IS NOT NULL AND resolved=c.primary_partner_id THEN
   RAISE EXCEPTION '关联合作伙伴不能选择企业自身伙伴档案' USING ERRCODE='22023'; END IF;
 UPDATE crm.customer SET business_identities=p_roles,partner_profile_id=resolved,
   customer_type_code=CASE WHEN 'customer'=ANY(p_roles) THEN customer_type_code ELSE NULL END
   WHERE id=c.id;
 RETURN resolved;
END $$;
REVOKE ALL ON FUNCTION security.set_customer_business_identity(uuid,text[],uuid) FROM PUBLIC,salegent_feishu_worker;

CREATE OR REPLACE FUNCTION security.customer_reference(p_customer_id uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',c.id,'name',c.name,'business_identities',c.business_identities,'partner_profile_id',c.partner_profile_id,
 'primary_partner_id',c.primary_partner_id,'customer_type_code',c.customer_type_code,'owner_team_id',c.owner_team_id)
 FROM crm.customer c WHERE c.id=p_customer_id AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
 AND security.authorization_allows('customer.reference',c.workspace_id,c.owner_user_ref_id,ARRAY[c.owner_team_id],
  security.authorization_customer('customer.read',c.id));
$$;

CREATE OR REPLACE FUNCTION security.customer_portfolio_scope()
RETURNS TABLE(id uuid,workspace_id uuid,owner_team_id uuid,claimant_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH grants AS MATERIALIZED (
  SELECT * FROM security.authorization_current_grants() WHERE permission_code='actual.read'
 ), teams AS MATERIALIZED (
  SELECT DISTINCT unnest(team_ids) AS id FROM grants WHERE scope_code='teams' AND effect='allow'
 ), memberships AS MATERIALIZED (
  SELECT tm.user_ref_id,tm.team_id FROM platform.team_membership tm
  JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
  WHERE tm.workspace_id=common.current_workspace_id() AND t.status='active' AND t.deleted_at IS NULL
   AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 ), projects AS MATERIALIZED (
  SELECT o.customer_id,p.user_ref_id FROM crm.opportunity o
  JOIN crm.opportunity_participant p ON p.opportunity_id=o.id AND p.workspace_id=o.workspace_id
  WHERE o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
   AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 ), team_customers AS MATERIALIZED (
  SELECT m.customer_id FROM crm.customer_sales_member m JOIN memberships tm ON tm.user_ref_id=m.user_ref_id
  JOIN teams t ON t.id=tm.team_id WHERE m.workspace_id=common.current_workspace_id()
  UNION
  SELECT p.customer_id FROM projects p JOIN memberships tm ON tm.user_ref_id=p.user_ref_id
  JOIN teams t ON t.id=tm.team_id WHERE security.fde_user_is_active(p.user_ref_id)
 ), assigned_customers AS MATERIALIZED (
  SELECT customer_id FROM projects WHERE user_ref_id=common.current_user_ref_id()
  UNION SELECT customer_id FROM crm.opportunity WHERE workspace_id=common.current_workspace_id()
   AND deleted_at IS NULL AND owner_user_ref_id=common.current_user_ref_id()
 )
 SELECT c.id,c.workspace_id,c.owner_team_id,CASE WHEN own.state='claimed' THEN own.owner_user_ref_id END
 FROM crm.customer c LEFT JOIN crm.customer_ownership own ON own.customer_id=c.id AND own.workspace_id=c.workspace_id
 WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
 AND 'customer'=ANY(c.business_identities)
 AND NOT EXISTS(SELECT 1 FROM grants WHERE effect='deny')
 AND (EXISTS(SELECT 1 FROM grants WHERE effect='allow' AND scope_code='workspace')
  OR (EXISTS(SELECT 1 FROM grants WHERE effect='allow' AND scope_code='self')
    AND own.state='claimed' AND own.owner_user_ref_id=common.current_user_ref_id())
  OR (EXISTS(SELECT 1 FROM grants WHERE effect='allow' AND scope_code='assigned')
    AND c.id IN (SELECT customer_id FROM assigned_customers))
  OR c.owner_team_id IN (SELECT id FROM teams) OR c.id IN (SELECT customer_id FROM team_customers));
$$;

-- Directory-safe identity projection only. It confers no customer-detail access.
CREATE FUNCTION security.partner_enterprise_reference(p_partner uuid)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('linked_customer_id',c.id,'business_identities',COALESCE(c.business_identities,ARRAY['partner']))
 FROM crm.partner p LEFT JOIN crm.customer c ON c.workspace_id=p.workspace_id AND c.partner_profile_id=p.id
 WHERE p.id=p_partner AND p.workspace_id=common.current_workspace_id()
 AND (security.authorization_has('partner.read') OR security.authorization_has('partner.manage'));
$$;
REVOKE ALL ON FUNCTION security.partner_enterprise_reference(uuid) FROM PUBLIC,salegent_feishu_worker;

CREATE OR REPLACE FUNCTION security.company_customer_directory_search(p_query text, p_limit integer, p_offset integer, p_industry text, p_claim_status text, p_phonetic_names text[], p_business_identity text)
 RETURNS jsonb
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 WITH grants AS MATERIALIZED (
  SELECT scope_code,team_ids,effect FROM security.authorization_current_grants()
  WHERE permission_code='customer.claim_directory'
 ), bounds AS (
  SELECT LEAST(GREATEST(p_limit,1),100) AS page_size,GREATEST(p_offset,0)::bigint AS page_start
 ), matched AS MATERIALIZED (
  SELECT c.id,c.name FROM crm.customer c
  JOIN crm.customer_ownership o ON o.customer_id=c.id
  WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
   AND NOT EXISTS(SELECT 1 FROM grants WHERE effect='deny')
   AND EXISTS(SELECT 1 FROM grants g WHERE g.effect='allow' AND (
     g.scope_code='workspace'
     OR (g.scope_code='self' AND c.owner_user_ref_id=common.current_user_ref_id())
     OR (g.scope_code='teams' AND g.team_ids && ARRAY[c.owner_team_id])))
   AND (p_business_identity='all' OR p_business_identity=ANY(c.business_identities)
     OR (p_business_identity='customer_partner' AND c.business_identities=ARRAY['customer','partner']))
   AND (p_industry IS NULL OR c.industry_code=p_industry)
   AND (p_claim_status IS NULL
    OR (p_claim_status='unclaimed' AND o.state='unclaimed')
    OR (p_claim_status='claimed' AND o.state='claimed')
    OR (p_claim_status='mine' AND o.state='claimed' AND o.owner_user_ref_id=common.current_user_ref_id())
    OR (p_claim_status='legacy_review' AND o.state='legacy_review')
    OR (p_claim_status='pending' AND (SELECT r.status FROM crm.customer_claim_request r
     WHERE r.customer_id=c.id AND r.applicant_user_ref_id=common.current_user_ref_id()
     ORDER BY r.requested_at DESC,r.id DESC LIMIT 1)='pending'))
   AND (NULLIF(btrim(p_query),'') IS NULL OR strpos(lower(c.name),lower(p_query))>0
    OR c.name IN (SELECT unnest(p_phonetic_names)))
 ), page AS (
  SELECT id,name FROM matched ORDER BY name,id
  LIMIT (SELECT page_size FROM bounds) OFFSET (SELECT page_start FROM bounds)
 ), items AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
   'id',c.id::text,'name',c.name,'industry_code',c.industry_code,'level_code',c.level_code,
   'business_identities',c.business_identities,'partner_profile_id',c.partner_profile_id,
   'primary_partner_id',c.primary_partner_id,'customer_type_code',c.customer_type_code,'team_name',t.name,'owner_name',u.display_name,
   'ownership_state',o.state,'claimed',o.owner_user_ref_id=common.current_user_ref_id(),
   'can_claim',security.can_claim_customer(c.id),
   'can_read_customer',security.authorization_customer('customer.read',c.id),
   'claim_status',(SELECT r.status FROM crm.customer_claim_request r WHERE r.customer_id=c.id
    AND r.applicant_user_ref_id=common.current_user_ref_id() ORDER BY r.requested_at DESC,r.id DESC LIMIT 1)
  ) ORDER BY page.name,page.id),'[]'::jsonb) AS value
  FROM page JOIN crm.customer c ON c.id=page.id
  JOIN crm.customer_ownership o ON o.customer_id=c.id
  LEFT JOIN platform.team t ON t.id=c.owner_team_id
  LEFT JOIN platform.user_ref u ON u.id=o.owner_user_ref_id
 ), totals AS (SELECT count(*) AS value FROM matched)
 SELECT jsonb_build_object('items',items.value,'total',totals.value,
  'has_more',bounds.page_start+jsonb_array_length(items.value)<totals.value,
  'next_offset',CASE WHEN bounds.page_start+jsonb_array_length(items.value)<totals.value
    THEN bounds.page_start+jsonb_array_length(items.value) ELSE NULL END)
 FROM items CROSS JOIN totals CROSS JOIN bounds;
$function$;

REVOKE ALL ON FUNCTION security.company_customer_directory_search(text,integer,integer,text,text,text[],text) FROM PUBLIC,salegent_feishu_worker;
-- Old six-argument callers get the exact same all-enterprise set and new additive fields.
CREATE OR REPLACE FUNCTION security.company_customer_directory_search(p_query text,p_limit integer,p_offset integer,p_industry text,p_claim_status text,p_phonetic_names text[])
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.company_customer_directory_search(p_query,p_limit,p_offset,p_industry,p_claim_status,p_phonetic_names,'all');
$$;

-- New helper EXECUTE grants are installed only by the exact reviewed service
-- policy. Do not infer API/worker roles from table SELECT or replay the legacy
-- reconciliation chain: that can widen a minimal runtime permission contract.
INSERT INTO ops.schema_migration(version,description) VALUES('V387','Explicit enterprise identities and stable partner profile linkage');
COMMIT;
