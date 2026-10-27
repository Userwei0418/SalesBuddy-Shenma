"""Knowledge storage: current RLS reads and narrowly granted database writes."""

from uuid import UUID, uuid4

from sales_backend.contracts.knowledge import (
    KnowledgeCatalog,
    KnowledgeContribution,
    KnowledgeMaterial,
    KnowledgeMaterialVersion,
    KnowledgeStoredFile,
)
from sales_backend.db import json_value
from sales_backend.domain.knowledge import CATEGORY_LABELS, resolve_contribution
from sales_backend.domain.knowledge_access import KnowledgeAccessEntry, KnowledgeACL, KnowledgeACLGrant

SCOPE_LABELS = {
    "all": "全部当前可见材料",
    "personal": "本人材料（含已共享）",
    "team": "共享给当前有效团队的材料",
    "shared": "他人共享给我的材料（含团队和企业共享）",
}
SOURCE = "salesbuddy.internal_materials"

# Static query fragments only. All user-supplied filters use positional parameters.
VISIBLE_COLUMNS = """
 m.id AS material_id,m.content_name,m.revision AS material_revision,
 m.latest_version_id,m.latest_version_id AS version_id,m.active_version_id,m.created_at,m.updated_at,
 v.version_no,v.uploaded_at,v.state,av.state AS active_version_state,a.revision AS acl_revision,
 v.contribution->'selection'->>'content_category' AS content_category,
 v.contribution->'selection'->>'content_scope' AS content_scope,
 v.contribution->'selection'->>'product_code' AS product_code,
 COALESCE((SELECT item->>'label' FROM jsonb_array_elements(c.value->'products') item
  WHERE item->>'code'=v.contribution->'selection'->>'product_code'),
  v.contribution->'source'->>'product_label') AS product_label,
 v.contribution->'selection'->>'industry_code' AS industry_code,
 (SELECT item->>'label' FROM jsonb_array_elements(c.value->'industries') item
  WHERE item->>'code'=v.contribution->'selection'->>'industry_code') AS industry_label,
 (v.contribution->'selection'->>'customer_id')::uuid AS customer_id,
 v.contribution->>'customer_name' AS customer_name,v.contribution->>'opportunity_name' AS opportunity_name,
 v.contributor_user_id,p.value->>'contributor_name' AS contributor_name,
 CASE WHEN v.contribution->>'kind'='legacy_import'
   THEN 'provider:'||COALESCE(NULLIF(btrim(v.contribution->'source'->>'provider_name'),''),'unknown')
   ELSE 'user:'||v.contributor_user_id::text END AS contributor_code,
 CASE WHEN v.contribution->>'kind'='legacy_import'
   THEN COALESCE(NULLIF(btrim(v.contribution->'source'->>'provider_name'),''),'未填写')
   ELSE COALESCE(p.value->>'contributor_name','未填写') END AS contributor_label,
 (COALESCE(v.contribution->>'kind','')<>'legacy_import'
   AND v.contributor_user_id=common.current_user_ref_id()) AS _mine,
 (SELECT ordinal FROM jsonb_array_elements(c.value->'products') WITH ORDINALITY AS product(item,ordinal)
   WHERE item->>'code'=v.contribution->'selection'->>'product_code') AS _product_order,
 p.value->>'sharing_label' AS sharing_label,p.value AS _scope,
 CASE WHEN v.contribution->>'kind'='legacy_import' THEN v.contribution->'source' ELSE NULL END AS source
"""
VISIBLE = "SELECT " + VISIBLE_COLUMNS + """,
 security.knowledge_can(m.id,'edit') AS can_edit,security.knowledge_can(m.id,'manage') AS can_manage
FROM knowledge.material m JOIN knowledge.version v ON v.id=m.latest_version_id
JOIN knowledge.material_acl a ON a.material_id=m.id
LEFT JOIN knowledge.version av ON av.id=m.active_version_id
LEFT JOIN knowledge.catalog c ON c.workspace_id=m.workspace_id
CROSS JOIN LATERAL (SELECT security.knowledge_projection(m.id) AS value OFFSET 0) p
"""
FILTERED = (
    # Each table retains its live RLS policy. Materializing the authorized rows
    # prevents a low row estimate from rescanning an RLS join once per material.
    # No cached grants or privileged read function replaces those policies.
    """WITH materials AS MATERIALIZED (
 SELECT * FROM knowledge.material WHERE workspace_id=common.current_workspace_id()
), versions AS MATERIALIZED (
 SELECT v.* FROM knowledge.version v WHERE v.workspace_id=common.current_workspace_id()
 AND v.id IN (SELECT latest_version_id FROM materials UNION SELECT active_version_id FROM materials)
), acls AS MATERIALIZED (
 SELECT * FROM knowledge.material_acl WHERE workspace_id=common.current_workspace_id()
), catalog AS MATERIALIZED (
 SELECT * FROM knowledge.catalog WHERE workspace_id=common.current_workspace_id()
), visible AS MATERIALIZED (SELECT """  # noqa: S608 -- only static SQL fragments
    + VISIBLE_COLUMNS
    + """
 FROM materials m JOIN versions v ON v.workspace_id=m.workspace_id AND v.id=m.latest_version_id
 JOIN acls a ON a.workspace_id=m.workspace_id AND a.material_id=m.id
 LEFT JOIN versions av ON av.workspace_id=m.workspace_id AND av.id=m.active_version_id
 LEFT JOIN catalog c ON c.workspace_id=m.workspace_id
 CROSS JOIN LATERAL (SELECT security.knowledge_projection(m.id) AS value OFFSET 0) p
"""
    + """), filtered AS MATERIALIZED (
 SELECT * FROM visible WHERE
 ($1::text='' OR strpos(lower(concat_ws(' ',content_name,customer_name,opportunity_name,
   product_label,product_code,contributor_label)),lower($1))>0)
 AND ($2::text IS NULL OR content_category=$2) AND ($3::text IS NULL OR product_code=$3)
 AND ($4::text IS NULL OR state=$4)
 AND ($5::text='all' OR COALESCE((_scope->>$5)::boolean,false))
 AND ($6::text='all' OR created_at>=transaction_timestamp()-interval '30 days')
 AND ($7::boolean IS NULL OR COALESCE(active_version_state='retrievable',false)=$7)
 AND ($8::boolean IS NULL OR COALESCE(content_scope IN ('customer','public'),false)=$8)
 AND ($9::boolean IS NULL OR _mine=$9)
 AND ($10::text IS NULL OR contributor_code=$10)
) """
)


def filter_args(filters):
    filters = filters or {}
    return (
        filters.get("q", ""),
        filters.get("content_category"),
        filters.get("product_code"),
        filters.get("state"),
        filters.get("scope", "all"),
        filters.get("period", "all"),
        filters.get("retrievable"),
        filters.get("external"),
        filters.get("mine"),
        filters.get("contributor_code"),
    )


def public_row(row):
    return {
        k: json_value(value) if k == "source" and value is not None else value
        for k, value in row.items()
        if k not in {"_scope", "_mine", "_product_order"}
    }


class KnowledgeRepository:
    async def lock(self, connection):
        # All knowledge mutations serialize catalog/ACL/name/receipt validation.
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('knowledge:'||common.current_workspace_id()::text,0))"
        )

    async def require_feature(self, connection, action):
        if not await connection.fetchval("SELECT security.knowledge_feature($1)", "knowledge." + action):
            raise PermissionError("KNOWLEDGE_ACCESS_DENIED")

    async def require_material(self, connection, material_id, action="read"):
        if not await connection.fetchval("SELECT security.knowledge_can($1,$2)", material_id, "read"):
            raise LookupError("KNOWLEDGE_MATERIAL_NOT_FOUND")
        if not await connection.fetchval("SELECT security.knowledge_can($1,$2)", material_id, action):
            raise PermissionError("KNOWLEDGE_ACCESS_DENIED")

    async def retrieval_entries(self, connection, material_ids, version_ids=None):
        """Load exactly the selected current versions under database ACL/RLS."""
        rows = await connection.fetch(
            """SELECT m.workspace_id,m.id AS material_id,m.active_version_id,
                      v.id AS version_id,v.version_no,v.contribution,v.uploaded_at,
                      v.contributor_user_id,v.state,m.content_name,
                      a.owner_user_id,a.revision AS acl_revision,a.grants,a.denied_user_ids
                 FROM knowledge.material m
                 JOIN knowledge.version v ON v.workspace_id=m.workspace_id
                    AND v.material_id=m.id
                 JOIN knowledge.material_acl a ON a.workspace_id=m.workspace_id AND a.material_id=m.id
                WHERE m.workspace_id=common.current_workspace_id()
                  AND m.id=ANY($1::uuid[]) AND m.deleted_at IS NULL
                  AND (($2::uuid[] IS NULL AND v.id=m.active_version_id AND v.state='retrievable')
                       OR ($2::uuid[] IS NOT NULL AND v.id=ANY($2::uuid[]) AND v.state<>'deleted'))
                  AND security.knowledge_can(m.id,'read')""",
            list(material_ids),
            list(version_ids) if version_ids is not None else None,
        )
        if len(rows) != len(set(material_ids)):
            raise LookupError("KNOWLEDGE_VERSION_UNAVAILABLE")
        entries = []
        for row in rows:
            if json_value(row["contribution"]).get("kind") == "legacy_import":
                raise LookupError("KNOWLEDGE_VERSION_UNAVAILABLE")
            material = KnowledgeMaterial(
                company_id=row["workspace_id"],
                material_id=row["material_id"],
                active_version_id=row["active_version_id"],
                deleted=False,
            )
            contribution = KnowledgeContribution.model_validate(json_value(row["contribution"]))
            version = KnowledgeMaterialVersion(
                company_id=row["workspace_id"],
                material_id=row["material_id"],
                version_id=row["version_id"],
                version_no=row["version_no"],
                contribution=contribution,
                uploaded_at=row["uploaded_at"],
                contributor_user_id=row["contributor_user_id"],
                state=row["state"],
            )
            grants = tuple(KnowledgeACLGrant.model_validate(g) for g in json_value(row["grants"]))
            acl = KnowledgeACL(
                company_id=row["workspace_id"],
                material_id=row["material_id"],
                owner_user_id=row["owner_user_id"],
                revision=row["acl_revision"],
                grants=grants,
                denied_user_ids=frozenset(row["denied_user_ids"] or ()),
            )
            entries.append(KnowledgeAccessEntry(material=material, version=version, acl=acl))
        return entries

    async def provider_bindings(self, connection, material_ids):
        rows = await connection.fetch(
            """SELECT material_id,version_id,provider_dataset_id,provider_document_id,
                          NULL::text AS configuration_fingerprint
                 FROM knowledge.provider_binding
                WHERE workspace_id=common.current_workspace_id()
                  AND material_id=ANY($1::uuid[]) AND state='active'
                UNION ALL
               SELECT i.material_id,i.version_id,f.provider_dataset_id,f.provider_document_id,
                      i.configuration_fingerprint
                 FROM knowledge.ingestion i JOIN knowledge.ingestion_file f
                   ON f.workspace_id=i.workspace_id AND f.ingestion_id=i.id
                WHERE i.workspace_id=common.current_workspace_id() AND i.material_id=ANY($1::uuid[])
                  AND i.status='succeeded' AND f.status='parsed'""",
            list(material_ids),
        )
        return [dict(row) for row in rows]

    async def bind_provider(self, connection, material_id, version_id, dataset_id, document_id):
        legacy = await connection.fetchval(
            "SELECT contribution->>'kind'='legacy_import' FROM knowledge.version WHERE id=$1 AND material_id=$2",
            version_id,
            material_id,
        )
        if legacy:
            raise ValueError("KNOWLEDGE_IMPORT_NOT_RETRIEVABLE")
        row = await connection.fetchval(
            "SELECT security.knowledge_bind_provider($1,$2,$3,$4)",
            material_id,
            version_id,
            dataset_id,
            document_id,
        )
        return json_value(row)

    async def provider_binding(self, connection, material_id, version_id):
        """Read one provider mapping only for a material manager.

        Provider document identifiers are operational metadata. They are not
        exposed through the ordinary material read path; managers use this
        endpoint to verify the exact version mapping before enabling retrieval.
        """
        await self.require_material(connection, material_id, "manage")
        row = await connection.fetchrow(
            """SELECT material_id,version_id,provider_dataset_id,provider_document_id,state,observed_at
                 FROM knowledge.provider_binding
                WHERE workspace_id=common.current_workspace_id()
                  AND material_id=$1 AND version_id=$2""",
            material_id,
            version_id,
        )
        return dict(row) if row is not None else None

    async def catalog(self, connection):
        row = await connection.fetchrow("SELECT workspace_id,revision,value FROM knowledge.catalog")
        if row is None:
            raise LookupError("KNOWLEDGE_CATALOG_UNCONFIGURED")
        return KnowledgeCatalog(company_id=row["workspace_id"], revision=row["revision"], **json_value(row["value"]))

    async def save_catalog(self, connection, expected, values):
        await connection.fetchval("SELECT security.knowledge_save_catalog($1,$2::jsonb)", expected, values)
        return (await self.catalog(connection)).model_dump(mode="json")

    async def file_metadata(self, connection, file_id):
        row = await connection.fetchrow(
            "SELECT id AS file_id,filename,media_type,size_bytes,sha256 FROM knowledge.file WHERE id=$1", file_id
        )
        if row is None:
            raise LookupError("KNOWLEDGE_FILE_NOT_FOUND")
        return dict(row)

    async def archive(self, connection, filename, media_type, content):
        file_id = await connection.fetchval(
            "SELECT security.knowledge_store_file($1,$2,$3,$4)", uuid4(), filename, media_type, content
        )
        return await self.file_metadata(connection, file_id)

    async def resolve(self, connection, selection, material_id):
        # SQL validates current CRM/file rights and catalog again, including direct SQL callers.
        trusted = KnowledgeContribution.model_validate(
            json_value(
                await connection.fetchval(
                    "SELECT security.knowledge_resolve($1::jsonb,$2)", selection.model_dump(mode="json"), material_id
                )
            )
        )
        from sales_backend.contracts.knowledge import KnowledgeCRMReference

        references = {}
        for kind in ("customer", "opportunity"):
            ref_id = getattr(selection, kind + "_id")
            references[kind] = (
                None
                if ref_id is None
                else KnowledgeCRMReference(
                    company_id=trusted.company_id, id=ref_id, kind=kind, name=getattr(trusted, kind + "_name")
                )
            )
        files = {}
        for file_id in selection.content.file_ids:
            metadata = await self.file_metadata(connection, file_id)
            files[file_id] = KnowledgeStoredFile(
                company_id=trusted.company_id, storage_provider="postgres", storage_key=str(file_id), **metadata
            )
        resolved = resolve_contribution(
            selection,
            await self.catalog(connection),
            company_id=trusted.company_id,
            attachable_files=files,
            **references,
        )
        if resolved != trusted:
            raise RuntimeError("KNOWLEDGE_CONTRACT_MISMATCH")
        return resolved

    async def save(self, connection, selection, material_id=None, expected=None):
        await self.resolve(connection, selection, material_id)
        receipt = json_value(
            await connection.fetchval(
                "SELECT security.knowledge_save_material($1,$2,$3::jsonb)",
                material_id,
                expected,
                selection.model_dump(mode="json"),
            )
        )
        row = await connection.fetchrow(
            "SELECT uploaded_at,contributor_user_id FROM knowledge.version WHERE id=$1", UUID(receipt["version_id"])
        )
        return {**receipt, **dict(row)}

    async def list_materials(self, connection, limit=50, offset=0, filters=None):
        args = filter_args(filters)
        rows = await connection.fetch(
            FILTERED  # noqa: S608 -- static SQL; filters are parameterized
            + """, page AS MATERIALIZED (
              SELECT * FROM filtered ORDER BY updated_at DESC,material_id LIMIT $11 OFFSET $12
            ), summary AS (
              SELECT (SELECT count(*) FROM filtered) AS _total,
                security.knowledge_can_create() AS _can_create,
                COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.label,x.code) FROM (
                  SELECT DISTINCT product_code AS code,COALESCE(product_label,product_code) AS label
                  FROM visible) x),'[]'::jsonb) AS _products
            )
            SELECT p.*,
              CASE WHEN p.material_id IS NOT NULL THEN security.knowledge_can(p.material_id,'edit') END AS can_edit,
              CASE WHEN p.material_id IS NOT NULL THEN security.knowledge_can(p.material_id,'manage') END AS can_manage,
              s._total,s._can_create,s._products
            FROM summary s LEFT JOIN page p ON true ORDER BY p.updated_at DESC,p.material_id
            """,
            *args,
            limit,
            offset,
        )
        # Summary survives an empty/out-of-range page. Facets cover the complete
        # authorized catalogue, not the page or the selected product filter.
        summary = rows[0]
        return {
            "items": [
                public_row({k: v for k, v in row.items() if k not in {"_total", "_can_create", "_products"}})
                for row in rows if row["material_id"] is not None
            ],
            "total": summary["_total"],
            "limit": limit,
            "offset": offset,
            "scope_label": SCOPE_LABELS[args[4]],
            "capabilities": {"create": summary["_can_create"]},
            "product_options": json_value(summary["_products"]),
        }

    async def detail(self, connection, material_id):
        await self.require_material(connection, material_id)
        row = await connection.fetchrow(VISIBLE + " WHERE m.id=$1", material_id)
        if row is None:
            raise LookupError("KNOWLEDGE_MATERIAL_NOT_FOUND")
        versions = await connection.fetch(
            """SELECT workspace_id AS company_id,material_id,id AS version_id,version_no,contribution,
               uploaded_at,contributor_user_id,state FROM knowledge.version
               WHERE material_id=$1 ORDER BY version_no DESC""",
            material_id,
        )
        files = await connection.fetch(
            """SELECT l.version_id,f.id AS file_id,f.filename,f.media_type,f.size_bytes,f.sha256,
                      (f.media_type IN ('text/plain','text/markdown','text/csv')
                       OR (f.media_type='application/pdf'
                           AND substring(f.content FROM 1 FOR 5)=decode('255044462d','hex'))
                       OR (f.media_type='image/png'
                           AND substring(f.content FROM 1 FOR 8)=decode('89504e470d0a1a0a','hex'))
                       OR (f.media_type='image/jpeg'
                           AND substring(f.content FROM 1 FOR 3)=decode('ffd8ff','hex'))
                       OR (f.media_type='image/webp'
                           AND substring(f.content FROM 1 FOR 4)=decode('52494646','hex')
                           AND substring(f.content FROM 9 FOR 4)=decode('57454250','hex')))
                       AS preview_supported
               FROM knowledge.version_file l JOIN knowledge.file f ON f.id=l.file_id
               WHERE l.material_id=$1 ORDER BY l.version_id,l.ordinal""",
            material_id,
        )
        return {
            **public_row(row),
            "versions": [
                {
                    **dict(v),
                    "contribution": json_value(v["contribution"]),
                    "files": [
                        {k: value for k, value in f.items() if k != "version_id"}
                        for f in files
                        if f["version_id"] == v["version_id"]
                    ],
                }
                for v in versions
            ],
        }

    async def acl(self, connection, material_id):
        await self.require_material(connection, material_id, "manage")
        row = await connection.fetchrow(
            """SELECT workspace_id AS company_id,material_id,owner_user_id,revision,grants,denied_user_ids
               FROM knowledge.material_acl WHERE material_id=$1""",
            material_id,
        )
        return {**dict(row), "grants": json_value(row["grants"])}

    async def acl_candidates(self, connection, material_id, subject_kind, query, limit, offset):
        await self.require_material(connection, material_id, "manage")
        return json_value(
            await connection.fetchval(
                "SELECT security.knowledge_acl_candidates($1::uuid,$2::text,$3::text,$4::integer,$5::integer)",
                material_id,
                subject_kind,
                query,
                limit,
                offset,
            )
        )

    async def save_acl(self, connection, material_id, expected, grants, denied):
        revision = await connection.fetchval(
            "SELECT security.knowledge_save_acl($1,$2,$3::jsonb,$4::uuid[])",
            material_id,
            expected,
            grants,
            denied,
        )
        return {"material_id": material_id, "acl_revision": revision}

    async def download(self, connection, file_id, version_id):
        # Require this exact version relation, even when another relation allows the same file.
        row = await connection.fetchrow(
            """SELECT f.id AS file_id,f.filename,f.media_type,f.size_bytes,f.sha256,f.content
               FROM knowledge.file f JOIN knowledge.version_file l ON l.file_id=f.id
               JOIN knowledge.version v ON v.id=l.version_id
               WHERE f.id=$1 AND l.version_id=$2 AND v.state<>'deleted'
               AND security.knowledge_can(l.material_id,'read')""",
            file_id,
            version_id,
        )
        if row is None:
            raise LookupError("KNOWLEDGE_FILE_NOT_FOUND")
        return dict(row)

    async def create_work(self, connection, work_id, actor, payload):
        await connection.fetchval("SELECT security.knowledge_create_work($1,$2::jsonb)", work_id, payload)
        return {"job_id": str(work_id), "status": "succeeded"}

    async def require_work(self, connection, work_id, *, manage=False):
        if not await connection.fetchval("SELECT security.knowledge_work_can($1,false)", work_id):
            raise LookupError("KNOWLEDGE_WORK_NOT_FOUND")
        if manage and not await connection.fetchval("SELECT security.knowledge_work_can($1,true)", work_id):
            raise PermissionError("KNOWLEDGE_ACCESS_DENIED")

    async def work(self, connection, work_id, actor_user_id=None):
        row = await connection.fetchrow(
            """SELECT w.*,security.knowledge_work_can(w.id,true) AS can_manage,
                      security.knowledge_work_recipients(w.id) AS recipients
                 FROM knowledge.work w WHERE w.workspace_id=common.current_workspace_id() AND w.id=$1""",
            work_id,
        )
        return self._work_row(row) if row else None

    async def list_works(self, connection, actor_user_id=None, limit=20, offset=0):
        rows = await connection.fetch(
            """SELECT w.*,security.knowledge_work_can(w.id,true) AS can_manage,
                      security.knowledge_work_recipients(w.id) AS recipients
                 FROM knowledge.work w WHERE w.workspace_id=common.current_workspace_id()
                ORDER BY w.created_at DESC,w.id DESC LIMIT $1 OFFSET $2""",
            limit,
            offset,
        )
        total = await connection.fetchval(
            "SELECT count(*) FROM knowledge.work WHERE workspace_id=common.current_workspace_id()"
        )
        return {"items": [self._work_row(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    async def work_candidates(self, connection, work_id, subject_kind, query, limit, offset):
        return json_value(
            await connection.fetchval(
                "SELECT security.knowledge_work_candidates($1,$2,$3,$4,$5)",
                work_id,
                subject_kind,
                query,
                limit,
                offset,
            )
        )

    async def publish_work(self, connection, work_id, expected_revision, recipients):
        await connection.fetchval(
            "SELECT security.knowledge_publish_work($1,$2,$3::jsonb)",
            work_id,
            expected_revision,
            recipients,
        )
        return await self.work(connection, work_id)

    async def work_download(self, connection, work_id, file_id):
        row = await connection.fetchrow(
            """SELECT f.id AS file_id,f.filename,f.media_type,f.size_bytes,f.sha256,f.content
                 FROM knowledge.work_file l JOIN knowledge.file f
                   ON f.workspace_id=l.workspace_id AND f.id=l.file_id
                WHERE l.workspace_id=common.current_workspace_id() AND l.work_id=$1 AND l.file_id=$2
                  AND security.knowledge_work_can(l.work_id,false)""",
            work_id,
            file_id,
        )
        if row is None:
            raise LookupError("KNOWLEDGE_FILE_NOT_FOUND")
        return dict(row)

    @staticmethod
    def _work_row(row):
        payload = json_value(row["manifest"])
        return {
            "work_id": row["id"],
            "job_id": row["id"],
            "owner_user_id": row["owner_user_id"],
            "can_manage": row["can_manage"],
            "revision": row["revision"],
            "recipients": json_value(row["recipients"]),
            "parent_work_id": row["parent_work_id"],
            "title": payload.get("title"),
            "purpose": payload.get("purpose"),
            "status": "succeeded",
            "format": payload.get("format", "docx"),
            "audience": payload.get("audience"),
            "version_no": row["version_no"],
            "error": None,
            "files": tuple(payload.get("files") or ()),
            "sources": tuple(payload.get("sources") or ()),
        }

    async def stats(self, connection, filters=None):
        row = await connection.fetchrow(
            FILTERED  # noqa: S608 -- static SQL; filters are parameterized
            + """
          SELECT transaction_timestamp() AS observed_at,
          count(*) AS total,
          count(*) FILTER(WHERE content_scope IN ('customer','public')) AS external,
          count(DISTINCT contributor_code) FILTER(WHERE contributor_code<>'provider:unknown') AS contributors,
          count(*) FILTER(WHERE _mine) AS mine,
          count(*) FILTER(WHERE created_at>=transaction_timestamp()-interval '30 days') AS added_30d,
          count(*) FILTER(WHERE active_version_state='retrievable') AS retrievable,
          count(*) FILTER(WHERE state='failed') AS failed,
          COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM (
            SELECT content_category AS code,count(*) AS count FROM filtered GROUP BY content_category
            ORDER BY content_category) x),'[]'::jsonb) AS categories,
          COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM (
            SELECT product_code AS code,COALESCE(product_label,product_code) AS label,count(*) AS count
            FROM filtered GROUP BY product_code,product_label
            ORDER BY min(_product_order) NULLS LAST,product_code) x),'[]'::jsonb) AS products,
          COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM (
            SELECT contributor_code AS code,contributor_label AS label,
              CASE WHEN contributor_code LIKE 'user:%' THEN min(contributor_user_id::text)::uuid END
                AS contributor_user_id,contributor_label AS contributor_name,count(*) AS count FROM filtered
            GROUP BY contributor_code,contributor_label ORDER BY count(*) DESC,contributor_code) x),
            '[]'::jsonb) AS contributions,
          COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM (
            SELECT state AS code,state,count(*) AS count FROM filtered
            WHERE state IN ('saved','syncing','parsing','failed') GROUP BY state ORDER BY state) x),
            '[]'::jsonb) AS alerts,
          (SELECT jsonb_agg(to_jsonb(x)) FROM (
            SELECT to_char(day,'YYYY-MM-DD') AS date,
              (SELECT count(*) FROM filtered WHERE created_at<LEAST(
                (day+interval '1 day') AT TIME ZONE 'Asia/Shanghai',transaction_timestamp())) AS count,
              (SELECT count(*) FROM filtered WHERE content_scope IN ('customer','public')
                AND created_at<LEAST((day+interval '1 day') AT TIME ZONE 'Asia/Shanghai',
                  transaction_timestamp())) AS external_count
            FROM generate_series(
             date_trunc('day',transaction_timestamp() AT TIME ZONE 'Asia/Shanghai')-interval '29 days',
             date_trunc('day',transaction_timestamp() AT TIME ZONE 'Asia/Shanghai'),interval '1 day') day
            ORDER BY day) x) AS trend
          FROM filtered
        """,
            *filter_args(filters),
        )
        observed_at = row["observed_at"]
        categories = json_value(row["categories"])
        for item in categories:
            item["label"] = CATEGORY_LABELS[item["code"]]
        alerts = json_value(row["alerts"])
        labels = {"saved": "已保存，等待解析接入", "syncing": "同步中", "parsing": "解析中", "failed": "处理失败"}
        for item in alerts:
            item["message"] = labels[item["state"]]
        return {
            "source": SOURCE,
            "observed_at": observed_at,
            "scope_label": SCOPE_LABELS[filter_args(filters)[4]],
            "time_zone": "Asia/Shanghai",
            "filters": filters or {},
            "metrics": {
                key: {"status": "available", "count": row[key], "source": SOURCE, "observed_at": observed_at}
                for key in ("total", "added_30d", "retrievable", "failed", "external", "contributors", "mine")
            },
            "categories": categories,
            "products": json_value(row["products"]),
            "contributions": json_value(row["contributions"]),
            "trend": json_value(row["trend"]),
            "alerts": alerts,
        }
