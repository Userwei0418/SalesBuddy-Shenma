"""Read imported quarterly original amounts separately from dated actual entries."""


async def read_historical_assets(connection, args, *, customer_id=None, limit=50, offset=0):
    # RLS on snapshots/opportunities and customer_reference retain the same tenant
    # and object permissions as the detail reader. Quarter is never a fabricated date.
    cte = """WITH facts AS (
      SELECT s.id,o.customer_id,COALESCE(c.name,ref.value->>'name') AS customer_name,
        c.data_kind,o.id AS opportunity_id,o.name AS opportunity_name,s.kind,
        s.raw_amount * CASE s.source_unit WHEN 'wan_cny' THEN 10000 ELSE 1 END AS amount,
        s.year,s.quarter,s.year::text || ' Q' || s.quarter::text AS period_label,
        s.source_field,s.source_unit,s.raw_amount,s.tax_basis,s.created_at,
        u.display_name AS owner_name,t.name AS team_name
      FROM crm.opportunity_period_actual_snapshot s
      JOIN crm.opportunity o ON o.id=s.opportunity_id AND o.workspace_id=s.workspace_id
      LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
      LEFT JOIN LATERAL (SELECT security.customer_reference(o.customer_id) AS value) ref ON true
      LEFT JOIN platform.user_ref u ON u.id=COALESCE(o.owner_user_ref_id,c.owner_user_ref_id)
      LEFT JOIN platform.team t ON t.id=COALESCE(o.owner_team_id,c.owner_team_id)
      WHERE o.deleted_at IS NULL AND ref.value IS NOT NULL
        AND ($1::date IS NULL OR s.year>=extract(year FROM $1::date))
        AND (s.year,s.quarter)<=(extract(year FROM $2::date),extract(quarter FROM $2::date))
        AND ($3::text IS NULL OR s.kind=$3) AND ($4::uuid IS NULL OR o.customer_id=$4)
        AND ($5::uuid IS NULL OR o.id=$5)
        AND ($6::uuid IS NULL OR COALESCE(o.owner_team_id,c.owner_team_id)=$6)
        AND ($7::uuid IS NULL OR COALESCE(o.owner_user_ref_id,c.owner_user_ref_id)=$7)
        AND ($8::uuid[] IS NULL OR o.customer_id=ANY($8::uuid[]))
    ) """
    summary = dict(await connection.fetchrow(cte + """SELECT
      sum(amount) FILTER(WHERE kind='recognized') AS recognized_amount,
      sum(amount) FILTER(WHERE kind='collection') AS collection_amount,
      count(*) FILTER(WHERE kind='recognized') AS recognized_count,
      count(*) FILTER(WHERE kind='collection') AS collection_count,
      count(DISTINCT customer_id) AS customer_count,count(*) AS entry_count,
      0::bigint AS unlinked_count,NULL::date AS first_date FROM facts""", *args))
    # Summary is needed for source selection even when the caller displays entries.
    async def page():
        if customer_id:
            query = """SELECT id::text,customer_id::text,customer_name,opportunity_id::text,
              opportunity_name,kind,amount,NULL::date AS occurred_on,period_label,
              year,quarter,source_field AS source_ref,source_field,source_unit,raw_amount,
              tax_basis,created_at,data_kind,'historical'::text AS basis
              FROM facts ORDER BY year DESC,quarter DESC,created_at DESC,id LIMIT $9 OFFSET $10"""
        else:
            query = """SELECT customer_id::text,customer_name,data_kind,
              string_agg(DISTINCT owner_name,'、' ORDER BY owner_name) AS owner_name,
              string_agg(DISTINCT team_name,'、' ORDER BY team_name) AS team_name,
              sum(amount) FILTER(WHERE kind='recognized') AS recognized_amount,
              sum(amount) FILTER(WHERE kind='collection') AS collection_amount,
              count(*) AS entry_count,NULL::date AS latest_date,'historical'::text AS basis
              FROM facts GROUP BY customer_id,customer_name,data_kind
              ORDER BY sum(amount) DESC,customer_id LIMIT $9 OFFSET $10"""
        return [dict(row) for row in await connection.fetch(cte + query, *args, limit, offset)]
    return summary, page
