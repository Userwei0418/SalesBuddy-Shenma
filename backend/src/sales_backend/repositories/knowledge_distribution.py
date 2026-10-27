# ruff: noqa: S608 -- SQL compositions below contain only static fragments; values are bound parameters.
"""Authenticated reads remain RLS constrained; public access uses narrow functions."""

from sales_backend.db import json_value

PUBLICATION = """
 SELECT p.material_id,p.id AS publication_id,p.version_id,v.version_no,v.version_no,p.revision,
 p.enabled,p.description,p.cover_file_id,p.allow_preview,p.allow_download,p.questionnaire,
 p.revision AS questionnaire_version,p.published_at,v.contribution->>'content_name' AS content_name,
 v.contribution->'selection'->>'content_category' AS content_category,
 v.contribution->'selection'->>'product_code' AS product_code,
 (SELECT x->>'label' FROM knowledge.catalog c CROSS JOIN LATERAL jsonb_array_elements(c.value->'products') x
 WHERE c.workspace_id=p.workspace_id AND x->>'code'=v.contribution->'selection'->>'product_code') AS product_label,
 COALESCE(
   CASE WHEN v.contribution->>'kind'='legacy_import'
        THEN v.contribution->'source'->'links'
        ELSE v.contribution->'selection'->'content'->'links'
   END,
   '[]'::jsonb
 ) AS source_links,
 security.kd_admin() AND security.knowledge_can(p.material_id,'manage') AS can_publish,
 security.kd_publication_visible(p.id) AS can_share
 FROM knowledge.distribution_publication p
 JOIN knowledge.version v ON v.workspace_id=p.workspace_id AND v.id=p.version_id
"""
SHARE = """
 SELECT s.id AS share_id,p.material_id,s.publication_id,p.version_id,v.version_no,s.sales_user_id,
 security.kd_sales_name(s.id) AS sales_user_name,
          v.contribution->>'content_name' AS content_name,s.created_at,s.revoked_at
 FROM knowledge.distribution_share s JOIN knowledge.distribution_publication p ON p.id=s.publication_id
 JOIN knowledge.version v ON v.id=p.version_id
"""
FILTER = """ AND ($1::text='all' OR s.sales_user_id=common.current_user_ref_id())
 AND ($2::uuid IS NULL OR p.material_id=$2) AND ($3::uuid IS NULL OR s.sales_user_id=$3)
 AND ($4::timestamptz IS NULL OR {time}>=$4) AND ($5::timestamptz IS NULL OR {time}<$5)
 AND ($6::uuid IS NULL OR p.version_id=$6) """


def analytics_args(query):
    return query.scope, query.material_id, query.sales_user_id, query.date_from, query.date_to, query.version_id


class DistributionRepository:
    async def admin(self, connection):
        return bool(await connection.fetchval("SELECT security.kd_admin()"))

    async def files(self, connection, version_id):
        rows = await connection.fetch(
            """SELECT f.id AS file_id,f.filename,f.media_type,f.size_bytes,
          (f.media_type IN ('text/plain','text/markdown','text/csv')
           OR (f.media_type='application/pdf' AND substring(f.content FROM 1 FOR 5)=decode('255044462d','hex'))
           OR (f.media_type='image/png' AND substring(f.content FROM 1 FOR 8)=decode('89504e470d0a1a0a','hex'))
           OR (f.media_type='image/jpeg' AND substring(f.content FROM 1 FOR 3)=decode('ffd8ff','hex'))
           OR (f.media_type='image/webp'
               AND substring(f.content FROM 1 FOR 4)=decode('52494646','hex')
               AND substring(f.content FROM 9 FOR 4)=decode('57454250','hex')))
           AS preview_supported
          FROM knowledge.version_file vf JOIN knowledge.file f ON f.id=vf.file_id AND f.workspace_id=vf.workspace_id
          WHERE vf.version_id=$1 ORDER BY vf.ordinal""",
            version_id,
        )
        return [dict(r) for r in rows]

    async def publication(self, connection, material_id):
        row = await connection.fetchrow(
            PUBLICATION + " WHERE p.material_id=$1 ORDER BY p.revision DESC LIMIT 1", material_id
        )
        if row is None:
            material = await connection.fetchrow(
                """SELECT id AS material_id,content_name,
             security.kd_admin() AND security.knowledge_can(id,'manage') AS can_publish
             FROM knowledge.material WHERE id=$1""",
                material_id,
            )
            if material is None:
                raise LookupError("KNOWLEDGE_MATERIAL_NOT_FOUND")
            return dict(material)
        result = dict(row)
        result["questionnaire"] = json_value(result["questionnaire"])
        result["source_links"] = json_value(result.get("source_links")) or []
        result["files"] = await self.files(connection, row["version_id"])
        return result

    async def materials(self, connection, q, category, product, limit, offset, date_from=None, date_to=None):
        where = """ WHERE security.kd_publication_visible(p.id)
         AND ($1::text='' OR strpos(lower(concat_ws(' ',v.contribution->>'content_name',p.description)),lower($1))>0)
         AND ($2::text IS NULL OR v.contribution->'selection'->>'content_category'=$2)
         AND ($3::text IS NULL OR v.contribution->'selection'->>'product_code'=$3)
         AND ($4::timestamptz IS NULL OR p.published_at>=$4) AND ($5::timestamptz IS NULL OR p.published_at<$5) """
        rows = await connection.fetch(
            PUBLICATION + where + " ORDER BY p.published_at DESC,p.id LIMIT $6 OFFSET $7",
            q,
            category,
            product,
            date_from,
            date_to,
            limit,
            offset,
        )
        count_sql = "SELECT count(*) FROM (" + PUBLICATION + where + ") x"  # noqa: S608 -- static fragments
        count = await connection.fetchval(count_sql, q, category, product, date_from, date_to)
        items = []
        for row in rows:
            item = dict(row)
            item["questionnaire"] = json_value(item["questionnaire"])
            item["source_links"] = json_value(item.get("source_links")) or []
            item["files"] = await self.files(connection, row["version_id"])
            items.append(item)
        return dict(items=items, total=count, limit=limit, offset=offset, can_manage=await self.admin(connection))

    async def share(self, connection, share_id):
        row = await connection.fetchrow(SHARE + " WHERE s.id=$1", share_id)
        if row is None:
            raise LookupError("KNOWLEDGE_SHARE_UNAVAILABLE")
        return dict(row)

    async def shares(self, connection, query):
        where = " WHERE true " + FILTER.format(time="s.created_at")
        args = analytics_args(query)
        rows = await connection.fetch(
            SHARE + where + " ORDER BY s.created_at DESC,s.id LIMIT $7 OFFSET $8", *args, query.limit, query.offset
        )
        count_sql = "SELECT count(*) FROM (" + SHARE + where + ") x"  # noqa: S608 -- static fragments
        count = await connection.fetchval(count_sql, *args)
        return dict(items=[dict(r) for r in rows], total=count, limit=query.limit, offset=query.offset)

    async def statistics(self, connection, query):
        args = analytics_args(query)
        events = await connection.fetchrow(
            """SELECT count(*) FILTER(WHERE e.kind='visit') AS visits,  -- static filter fragments

          count(DISTINCT e.browser_hash) FILTER(WHERE e.kind='visit') AS unique_visitors_estimate,
          count(*) FILTER(WHERE e.kind='preview') AS previews,
          count(*) FILTER(WHERE e.kind='download') AS download_requests
          FROM knowledge.distribution_event e JOIN knowledge.distribution_share s ON s.id=e.share_id
          JOIN knowledge.distribution_publication p ON p.id=s.publication_id WHERE true """
            + FILTER.format(time="e.occurred_at"),
            *args,
        )
        responses = await connection.fetchval(
            """SELECT count(*) FROM knowledge.distribution_response r
          JOIN knowledge.distribution_share s ON s.id=r.share_id
          JOIN knowledge.distribution_publication p ON p.id=s.publication_id
          WHERE (p.questionnaire->>'enabled')::boolean """
            + FILTER.format(time="r.submitted_at"),
            *args,
        )
        return dict(
            events,
            scope=query.scope,
            questionnaire_submissions=responses,
            observed_at=await connection.fetchval("SELECT clock_timestamp()"),
            can_view_all=await self.admin(connection),
        )

    async def responses(self, connection, query):
        args = analytics_args(query)
        base = """ FROM knowledge.distribution_response r JOIN knowledge.distribution_share s ON s.id=r.share_id
          JOIN knowledge.distribution_publication p ON p.id=s.publication_id
          JOIN knowledge.version v ON v.id=p.version_id
          WHERE (p.questionnaire->>'enabled')::boolean """
        base += FILTER.format(time="r.submitted_at")
        rows = await connection.fetch(
            """SELECT r.id AS response_id,r.share_id,p.material_id,p.id AS publication_id,p.version_id,v.version_no,
          s.sales_user_id,security.kd_sales_name(s.id) AS sales_user_name,
          v.contribution->>'content_name' AS content_name,
          p.revision AS questionnaire_version,p.questionnaire,r.answers,r.submitted_at"""
            + base
            + " ORDER BY r.submitted_at DESC,r.id LIMIT $7 OFFSET $8",
            *args,
            query.limit,
            query.offset,
        )
        items = []
        for row in rows:
            item = dict(row)
            item.update(answers=json_value(item["answers"]), questionnaire=json_value(item["questionnaire"]))
            items.append(item)
        return dict(
            items=items,
            total=await connection.fetchval("SELECT count(*)" + base, *args),
            limit=query.limit,
            offset=query.offset,
            can_view_all=await self.admin(connection),
        )
