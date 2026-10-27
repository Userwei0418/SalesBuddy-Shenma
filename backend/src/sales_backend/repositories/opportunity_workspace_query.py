# ruff: noqa: S608 -- All identifiers/fragments are server-owned; values are bound.
"""Typed whole-set SQL filtering, grouping and pagination under live RLS.

Only approved date provenance is parsed in Python, using the existing parser;
its full-authorized-set date map is bound back into the SQL projection before
any filters, ordering, groups or pagination. No business rows are filtered or
paginated in Python. API calls use a single repeatable-read transaction.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sales_backend.db import json_value
from sales_backend.domain.opportunity_field_catalog import DEFINITION_VERSION, FIELD_CATALOG
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.opportunity_browse import STAGE_SQL
from sales_backend.repositories.opportunity_business_date import business_created_on, creation_date_projection
from sales_backend.repositories.opportunity_fields import editable_fields
from sales_backend.repositories.opportunity_views import validate_scope

PERSON_KEYS = {'owner', 'team', 'partner', 'customer_owner', 'creator', 'primary_fde_id'}
ARRAY_KEYS = {'customer_source', 'fde_member_ids', 'participants'}
ACTUAL_KEYS = {'actual_recognized', 'actual_collection'}
CASTS = {'text': 'text', 'enum': 'text', 'multi_enum': 'text', 'person': 'uuid', 'people': 'uuid',
         'money': 'numeric', 'number': 'integer', 'boolean': 'boolean', 'date': 'date', 'datetime': 'timestamptz'}
SCALARS = ('name', 'status', 'product_line', 'sales_channel', 'follow_up_plan', 'customer_source', 'priority',
           'is_key', 'trial_status', 'summary', 'note', 'ppl_status', 'external_system_status', 'fde_support_stage', 'fde_progress')


def resolved_period(period, today=None):
    today = today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    year, quarter = (period.year, period.quarter) if period.kind == 'fixed' else (today.year, (today.month - 1) // 3 + 1)
    return {'year': year, 'quarter': quarter, 'timezone': 'Asia/Shanghai'}


def used_fields(config):
    return {'name', 'customer_name', *(column.key for column in config.columns),
            *(item.field for item in config.filters), *(item.field for item in config.sorts),
            *([config.group_by] if config.group_by else []),
            *([config.date_filter.field] if config.date_filter else [])}


class QueryBuilder:
    def __init__(self):
        self.args = []

    def bind(self, value, cast):
        # asyncpg requires typed Python dates/numerics for native scalar/arrays.
        if value is not None:
            if cast.endswith('[]'):
                value = [self.convert(v, cast[:-2]) for v in value]
            else:
                value = self.convert(value, cast)
        self.args.append(value)
        return f'${len(self.args)}::{cast}'

    @staticmethod
    def convert(value, cast):
        if cast == 'numeric':
            return Decimal(value)
        if cast == 'date' and isinstance(value, str):
            return date.fromisoformat(value)
        if cast == 'timestamptz' and isinstance(value, str):
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value


def scope_predicate(builder, scope, actor):
    terms = ["o.workspace_id=common.current_workspace_id()", 'o.deleted_at IS NULL',
             "security.authorization_opportunity('opportunity.read',o.id)"]
    if scope.kind == 'self':
        uid = builder.bind(str(actor.user_id), 'uuid')
        terms.append(f'(o.owner_user_ref_id={uid} OR EXISTS(SELECT 1 FROM crm.opportunity_participant sp '
                     f'WHERE sp.workspace_id=o.workspace_id AND sp.opportunity_id=o.id AND sp.user_ref_id={uid} '
                     "AND sp.participant_role='fde' AND security.fde_user_is_active(sp.user_ref_id) "
                     'AND clock_timestamp()>=sp.valid_from AND clock_timestamp()<sp.valid_to))')
    if scope.selected_team_ids:
        teams = builder.bind([str(ident) for ident in scope.selected_team_ids], 'uuid[]')
        terms.append(f'o.owner_team_id=ANY({teams})')
    if scope.selected_owner_ids:
        owners = builder.bind([str(ident) for ident in scope.selected_owner_ids], 'uuid[]')
        terms.append(f'o.owner_user_ref_id=ANY({owners})')
    return ' AND '.join(terms)


def compile_filter(builder, item):
    field = FIELD_CATALOG[item.field]
    column, operator = 'f."' + item.field + '"', item.operator
    if operator in {'is_empty', 'is_not_empty'}:
        empty = f'({column} IS NULL'
        if item.field in ARRAY_KEYS:
            empty += f' OR cardinality({column})=0'
        elif field['type'] == 'text':
            empty += f" OR btrim({column})=''"
        empty += ')'
        return empty if operator == 'is_empty' else 'NOT ' + empty
    cast = CASTS[field['type']]
    if item.field == 'product_line' and operator in {'eq', 'ne', 'in'}:
        argument = builder.bind(item.value, 'text[]' if operator == 'in' else 'text')
        comparison = '=ANY(' + argument + ')' if operator == 'in' else '=' + argument
        matched = ("(EXISTS(SELECT 1 FROM jsonb_array_elements(f.product_lines) pl WHERE pl->>'name'"
                   + comparison + ") OR (f.product_lines='[]'::jsonb AND f.product_line" + comparison + '))')
        return 'NOT ' + matched if operator == 'ne' else matched
    if operator == 'contains':
        return f'strpos(lower({column}),lower({builder.bind(item.value, cast)}))>0'
    if operator in {'in', 'has_any', 'has_all'}:
        argument = builder.bind(item.value, cast + '[]')
        return f'{column}=ANY({argument})' if operator == 'in' else f'{column} ' + ('&&' if operator == 'has_any' else '@>') + f' {argument}'
    if operator == 'between':
        return f'{column} BETWEEN {builder.bind(item.value[0], cast)} AND {builder.bind(item.value[1], cast)}'
    symbol = {'eq': '=', 'ne': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[operator]
    return f'{column}{symbol}{builder.bind(item.value, cast)}'


def order_terms(key, direction, *, alias='f', group=False):
    assert key in FIELD_CATALOG and direction in {'asc', 'desc'}
    column = f'{alias}."' + ('group_value' if group else key) + '"'
    name = f'{alias}."' + ('group_label' if group else key + '_name') + '"'
    kind = FIELD_CATALOG[key]['type']
    if kind == 'person':
        return [f'{name} COLLATE "C" {direction} NULLS LAST', f'{column} {direction} NULLS LAST']
    if kind == 'enum':
        values = ','.join("'" + o['value'].replace("'", "''") + "'" for o in FIELD_CATALOG[key]['options'])
        return [f'array_position(ARRAY[{values}]::text[],{column}) {direction} NULLS LAST', f'{column} COLLATE "C" {direction} NULLS LAST']
    return [f'{column}' + (' COLLATE "C"' if kind == 'text' else '') + f' {direction} NULLS LAST']


def ordering(config):
    keys = [(sort.field, sort.direction) for sort in config.sorts]
    if config.group_by:
        group_direction = next((direction for key, direction in keys if key == config.group_by), 'asc')
        keys = [(config.group_by, group_direction), *[(k, d) for k, d in keys if k != config.group_by]]
    return ','.join([term for key, direction in keys for term in order_terms(key, direction)] + ['f.id ASC'])


def group_label(key, alias='f'):
    if key in PERSON_KEYS:
        return f'{alias}."{key}_name"'
    if FIELD_CATALOG[key]['type'] == 'enum':
        pairs = ' '.join("WHEN '" + option['value'].replace("'", "''") + "' THEN '" + option['label'].replace("'", "''") + "'"
                         for option in FIELD_CATALOG[key]['options'])
        return f'CASE {alias}."{key}" {pairs} ELSE {alias}."{key}" END'
    if FIELD_CATALOG[key]['type'] == 'boolean':
        return f"CASE WHEN {alias}.\"{key}\" IS NULL THEN NULL WHEN {alias}.\"{key}\" THEN '是' ELSE '否' END"
    return f'{alias}."{key}"::text'


def value_sql(key):
    if key in PERSON_KEYS:
        return f"CASE WHEN p.\"{key}\" IS NULL THEN NULL ELSE jsonb_build_object('id',p.\"{key}\",'name',p.\"{key}_name\") END"
    if key in {'fde_member_ids', 'participants'}:
        return f'p."{key}_value"'
    if FIELD_CATALOG[key]['type'] == 'money':
        return f'p."{key}"::text'
    return f'p."{key}"'


def visit_projection(builder, fields, today):
    """Read the RLS-visible visit set once, then map it to all authorized rows.

    Materialize the authorized link relation before joining visible visits. A
    three-way join here otherwise permits an authorized x visits loop to repeat
    expensive visit_opportunity RLS probes for every pair (observed 6,519 times).
    Both legacy direct ownership and multi-opportunity links participate; UNION
    removes a visit linked through both paths. These CTEs precede all user
    filters/groups/pagination, including when visit facts are only used to sort.
    """
    latest = bool(fields & {'latest_followup_at', 'latest_progress'})
    recent = 'has_followup_7d' in fields
    columns = ['NULL::timestamptz AS latest_followup_at', 'NULL::text AS latest_progress', 'false AS has_followup_7d']
    if not (latest or recent):
        return '', '', ','.join(columns)
    ctes = f''', visible_visits AS MATERIALIZED (
      SELECT v.id,v.workspace_id,v.opportunity_id,v.interaction_at{',v.follow_up_record' if latest else ''}
      FROM activity.visit v WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
       AND v.status IN ('confirmed','archived')
    ), authorized_visit_links AS MATERIALIZED (
      SELECT vo.visit_id,vo.opportunity_id,vo.workspace_id FROM activity.visit_opportunity vo
       JOIN authorized o ON o.id=vo.opportunity_id AND o.workspace_id=vo.workspace_id
      WHERE vo.workspace_id=common.current_workspace_id()
    ), visit_targets AS MATERIALIZED (
      SELECT v.id AS visit_id,o.id AS opportunity_id FROM visible_visits v
       JOIN authorized o ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id
      UNION
      SELECT v.id AS visit_id,vo.opportunity_id FROM visible_visits v
       JOIN authorized_visit_links vo ON vo.visit_id=v.id AND vo.workspace_id=v.workspace_id
    )'''
    joins = []
    if latest:
        ctes += ''', latest_visits AS MATERIALIZED (
          SELECT DISTINCT ON (t.opportunity_id) t.opportunity_id,v.interaction_at,v.follow_up_record
          FROM visit_targets t JOIN visible_visits v ON v.id=t.visit_id
          ORDER BY t.opportunity_id,v.interaction_at DESC NULLS LAST,v.id DESC
        )'''
        joins.append('LEFT JOIN latest_visits latest ON latest.opportunity_id=o.id')
        columns[:2] = ['latest.interaction_at AS latest_followup_at', 'latest.follow_up_record AS latest_progress']
    if recent:
        start = builder.bind(today - timedelta(days=6), 'date')
        end = builder.bind(today + timedelta(days=1), 'date')
        ctes += f''', recent_visits AS MATERIALIZED (
          SELECT DISTINCT t.opportunity_id FROM visit_targets t JOIN visible_visits v ON v.id=t.visit_id
          WHERE v.interaction_at>=({start}::timestamp AT TIME ZONE 'Asia/Shanghai')
           AND v.interaction_at<({end}::timestamp AT TIME ZONE 'Asia/Shanghai')
        )'''
        joins.append('LEFT JOIN recent_visits recent ON recent.opportunity_id=o.id')
        columns[2] = '(recent.opportunity_id IS NOT NULL) AS has_followup_7d'
    return ctes, '\n      '.join(joins), ','.join(columns)


def optional_projection(fields, year, quarter):
    """Avoid unrelated row aggregates without changing whole-set query semantics.

    ``fields`` includes filters, sorts and grouping as well as visible columns.
    Required facts still precede filtering/pagination. Typed empty projections
    retain the internal row shape for unused fields; they never enter payloads.
    Finance metadata and authorization remain unconditional elsewhere.
    """
    joins = []
    demo = '0::integer AS demo_count'
    if 'demo_count' in fields:
        demo = '''(SELECT count(*)::integer FROM crm.opportunity_demo_scenes d
         WHERE d.opportunity_id=o.id AND d.workspace_id=o.workspace_id AND d.deleted_at IS NULL) AS demo_count'''
    fde = "ARRAY[]::uuid[] AS fde_member_ids,'[]'::jsonb AS fde_member_ids_value"
    if 'fde_member_ids' in fields:
        fde = "COALESCE(fde.ids,ARRAY[]::uuid[]) AS fde_member_ids,COALESCE(fde.people,'[]'::jsonb) AS fde_member_ids_value"
        joins.append('''LEFT JOIN LATERAL(SELECT array_agg(p.user_ref_id ORDER BY u.display_name COLLATE "C",p.user_ref_id) AS ids,
       jsonb_agg(jsonb_build_object('id',p.user_ref_id,'name',u.display_name,'is_active',security.fde_user_is_active(u.id))
        ORDER BY u.display_name COLLATE "C",p.user_ref_id) AS people
       FROM crm.opportunity_participant p JOIN platform.user_ref u ON u.id=p.user_ref_id AND u.workspace_id=p.workspace_id
       WHERE NOT o.shared_read_only AND p.workspace_id=o.workspace_id AND p.opportunity_id=o.id AND p.participant_role='fde'
        AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to) fde ON true''')
    participants = "ARRAY[]::uuid[] AS participants,'[]'::jsonb AS participants_value"
    if 'participants' in fields:
        participants = "COALESCE(pp.ids,ARRAY[]::uuid[]) AS participants,COALESCE(pp.people,'[]'::jsonb) AS participants_value"
        joins.append('''LEFT JOIN LATERAL(SELECT array_agg(p.user_ref_id ORDER BY u.display_name COLLATE "C",p.user_ref_id) AS ids,
       jsonb_agg(jsonb_build_object('user_id',p.user_ref_id,'role',p.participant_role,'name',u.display_name,
        'is_active',u.status='active' AND u.deleted_at IS NULL AND (p.participant_role<>'fde' OR security.fde_user_is_active(u.id)))
        ORDER BY u.display_name COLLATE "C",p.user_ref_id) AS people
       FROM crm.opportunity_project_participant p JOIN platform.user_ref u ON u.id=p.user_ref_id AND u.workspace_id=p.workspace_id
       WHERE NOT o.shared_read_only AND p.workspace_id=o.workspace_id AND p.opportunity_id=o.id) pp ON true''')
    forecast = ('NULL::numeric AS forecast_recognized,NULL::numeric AS forecast_collection,'
                'NULL::text AS collection_confidence')
    if fields & {'forecast_recognized', 'forecast_collection', 'collection_confidence'}:
        forecast = ('forecast.recognized_amount AS forecast_recognized,forecast.collection_amount AS forecast_collection,'
                    'forecast.collection_confidence')
        joins.append(f'''LEFT JOIN crm.opportunity_forecast forecast
       ON forecast.opportunity_id=o.id AND forecast.workspace_id=o.workspace_id
        AND forecast.year={year} AND forecast.quarter={quarter}''')
    return '\n      '.join(joins), ','.join((demo, fde, participants, forecast))


def compile_query(request, actor, *, date_map, period, today=None):
    from sales_backend.repositories.opportunity_product_lines import PRODUCT_LINES_SQL, PRODUCT_LINE_DISPLAY_SQL

    config = request.config
    builder = QueryBuilder()
    scope = scope_predicate(builder, config.scope, actor)
    year = builder.bind(period['year'], 'integer')
    quarter = builder.bind(period['quarter'], 'integer')
    dates = builder.bind(date_map, 'jsonb')
    today = today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    fields = used_fields(config)
    projection_fields = fields | ({'forecast_recognized', 'forecast_collection'} if config.active_quarter else set())
    visit_ctes, visit_joins, visit_columns = visit_projection(builder, fields, today)
    optional_joins, optional_columns = optional_projection(projection_fields, year, quarter)
    filters = [compile_filter(builder, item) for item in config.filters]
    if config.active_quarter:
        # The forecast projection already belongs to the selected financial
        # quarter. Filter before summary, grouping and pagination; either
        # positive forecast is sufficient, with no implied status/date filter.
        filters.append('(f.forecast_recognized > 0 OR f.forecast_collection > 0)')
    if config.date_filter:
        dates_filter = config.date_filter
        # Both projections are business calendar dates. Unknown dates remain
        # NULL; never synthesize a close day from a recorded year or quarter.
        filters.append(f'f."{dates_filter.field}" BETWEEN '
                       f'{builder.bind(dates_filter.start, "date")} AND {builder.bind(dates_filter.end, "date")}')
    if config.q:
        q = builder.bind(config.q, 'text')
        filters.append('(' + ' OR '.join(f'strpos(lower(COALESCE(f."{key}",\'\')),lower({q}))>0' for key in ('name', 'customer_name', 'product_line')) + ')')
    limit, offset = builder.bind(request.limit, 'integer'), builder.bind(request.offset, 'integer')
    actual_projection = []
    meta = []
    for kind in ('recognized', 'collection'):
        alias = 'ar' if kind == 'recognized' else 'ac'
        actual_projection.append(f'CASE WHEN {alias}.complete THEN {alias}.subtotal END AS actual_{kind}')
        meta.append(f"'actual_{kind}',jsonb_build_object('complete',COALESCE({alias}.complete,false),"
                    f"'confirmed_subtotal',{alias}.subtotal::text,'missing_material_count',COALESCE({alias}.missing,0),"
                    f"'tax_unknown_period_count',CASE WHEN {alias}.tax_unknown THEN 1 ELSE 0 END,"
                    f"'conflict_period_count',CASE WHEN {alias}.conflict THEN 1 ELSE 0 END)")
    payload = ','.join("'" + key + "'," + value_sql(key) for key in sorted(fields))
    money_summary = """count(*) AS count,CASE WHEN count(*) FILTER(WHERE amount IS NULL)>0 THEN NULL ELSE COALESCE(sum(amount),0)::text END AS amount,
      count(*) FILTER(WHERE amount IS NULL) AS amount_unknown_count,COALESCE(sum(amount),0)::text AS known_amount_subtotal"""
    grouped_cte, groups_sql = '', "'[]'::jsonb"
    row_group = "'group_key',NULL,'group_label',NULL"
    if config.group_by:
        key = config.group_by
        label = group_label(key)
        direction = next((s.direction for s in config.sorts if s.field == key), 'asc')
        order = ','.join(order_terms(key, direction, alias='g', group=True))
        grouped_cte = f''', grouped AS (SELECT f."{key}" AS group_value,{label} AS group_label,{money_summary}
          FROM filtered f GROUP BY f."{key}",{label})'''
        groups_sql = f'''(SELECT COALESCE(jsonb_agg(jsonb_build_object('key',g.group_value,'label',g.group_label,'count',g.count,
          'amount',g.amount,'amount_unknown_count',g.amount_unknown_count,'known_amount_subtotal',g.known_amount_subtotal)
          ORDER BY {order}),'[]'::jsonb) FROM grouped g)'''
        row_group = f"'group_key',p.\"{key}\",'group_label',{group_label(key, 'p')}"
    source_columns = list(dict.fromkeys((*SCALARS, 'id', 'workspace_id', 'customer_id', 'version_no', 'amount',
        'probability', 'stage_code', 'owner_user_ref_id', 'owner_team_id', 'partner_id', 'created_by_user_ref_id',
        'primary_fde_id', 'expected_close_date', 'expected_close_year', 'expected_close_quarter')))
    # Keep roster facts in their original object scope. Mask primary FDE and
    # skip roster joins before user filters, ordering, grouping and pagination.
    sql = f'''WITH authorized AS MATERIALIZED (
      SELECT {','.join('o.' + key for key in source_columns if key != 'product_line')},
       {PRODUCT_LINE_DISPLAY_SQL} AS product_line,pl.items AS product_lines,
       o.product_line AS legacy_product_line,o.intent_product_line_ids,
       CASE WHEN EXISTS(SELECT 1 FROM crm.opportunity_material m WHERE m.opportunity_id=o.id
         AND m.workspace_id=o.workspace_id AND m.is_active) THEN 'materials'
        WHEN cardinality(o.intent_product_line_ids)>0 THEN 'intent'
        WHEN o.product_line IS NOT NULL AND o.product_line<>'' THEN 'legacy' ELSE 'none' END AS product_line_source,
       COALESCE(c.name,security.customer_reference(o.customer_id)->>'name',read_context.value->>'customer_name') AS customer_name,
       COALESCE((read_context.value->>'shared_read_only')::boolean,false) AS shared_read_only,
       c.owner_user_ref_id AS customer_owner,CASE WHEN c.owner_user_ref_id IS NOT NULL THEN COALESCE(co.display_name,'成员') END AS customer_owner_name,
       CASE WHEN o.owner_user_ref_id IS NOT NULL THEN COALESCE(owner.display_name,'成员') END AS owner_name,
       CASE WHEN o.owner_team_id IS NOT NULL THEN COALESCE(t.name,'团队') END AS team_name,
       CASE WHEN o.partner_id IS NOT NULL THEN COALESCE(partner.name,'伙伴') END AS partner_name,
       CASE WHEN o.created_by_user_ref_id IS NOT NULL THEN COALESCE(creator.display_name,'成员') END AS creator_name,
       CASE WHEN o.primary_fde_id IS NOT NULL THEN COALESCE(primary_fde.display_name,'成员') END AS primary_fde_id_name,
       security.authorization_opportunity('opportunity.update',o.id) AS can_edit,
       security.can_manage_fde_members(o.id) AS can_fde,
       (security.authorization_opportunity('actual.read',o.id) AND security.authorization_customer('actual.read',o.customer_id)) AS can_actual,
       (security.management_actor() AND security.authorization_has('access.console')
        AND security.authorization_opportunity('actual.create',o.id) AND security.authorization_customer('actual.create',o.customer_id)) AS can_actual_write
      FROM crm.opportunity o
      CROSS JOIN LATERAL(SELECT {PRODUCT_LINES_SQL} AS items) pl
      CROSS JOIN LATERAL(SELECT security.opportunity_read_context(o.id) AS value) read_context
      LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id AND c.deleted_at IS NULL
      LEFT JOIN platform.user_ref owner ON owner.id=o.owner_user_ref_id AND owner.workspace_id=o.workspace_id
      LEFT JOIN platform.team t ON t.id=o.owner_team_id AND t.workspace_id=o.workspace_id
      LEFT JOIN crm.partner partner ON partner.id=o.partner_id AND partner.workspace_id=o.workspace_id
      LEFT JOIN platform.user_ref co ON co.id=c.owner_user_ref_id AND co.workspace_id=o.workspace_id
      LEFT JOIN platform.user_ref creator ON creator.id=o.created_by_user_ref_id AND creator.workspace_id=o.workspace_id
      LEFT JOIN platform.user_ref primary_fde ON primary_fde.id=o.primary_fde_id AND primary_fde.workspace_id=o.workspace_id
      WHERE {scope}
    ){visit_ctes}, actuals AS MATERIALIZED (
      SELECT a.opportunity_id,a.kind,bool_and(a.complete) AS complete,sum(a.amount) AS subtotal,
       max(a.missing_material_count) AS missing,bool_or(a.tax_unresolved) AS tax_unknown,
       bool_or(a.status IN ('needs_confirmation','stale_confirmation')) AS conflict
      FROM crm.effective_actual a JOIN authorized ao ON ao.id=a.opportunity_id AND ao.workspace_id=a.workspace_id AND ao.can_actual
      WHERE a.year={year} AND a.quarter={quarter} GROUP BY a.opportunity_id,a.kind
    ), projected AS MATERIALIZED (
      SELECT o.id,o.customer_id,o.version_no,{','.join('o.' + key for key in SCALARS)},o.product_lines,
       o.product_line_source,o.intent_product_line_ids,o.legacy_product_line,
       o.id::text AS id_text,o.amount,{STAGE_SQL} AS stage,o.customer_name,
       o.owner_user_ref_id AS owner,o.owner_name,o.owner_team_id AS team,o.team_name,o.partner_id AS partner,o.partner_name,
       o.customer_owner,o.customer_owner_name,o.created_by_user_ref_id AS creator,o.creator_name,
       CASE WHEN NOT o.shared_read_only THEN o.primary_fde_id END AS primary_fde_id,
       CASE WHEN NOT o.shared_read_only THEN o.primary_fde_id_name END AS primary_fde_id_name,NULL::text AS region,
       o.expected_close_date,COALESCE(extract(year FROM o.expected_close_date)::integer,o.expected_close_year) AS expected_close_year,
       COALESCE(extract(quarter FROM o.expected_close_date)::integer,o.expected_close_quarter) AS expected_close_quarter,
       ({dates}->>o.id::text)::date AS business_created_on,
       {visit_columns},
       {optional_columns},
       {','.join(actual_projection)},jsonb_build_object({','.join(meta)}) AS finance_meta,
       o.can_edit,o.can_fde,o.can_actual,o.can_actual_write
      FROM authorized o
      LEFT JOIN actuals ar ON ar.opportunity_id=o.id AND ar.kind='recognized'
      LEFT JOIN actuals ac ON ac.opportunity_id=o.id AND ac.kind='collection'
      {visit_joins}
      {optional_joins}
    ), filtered AS MATERIALIZED (SELECT * FROM projected f WHERE {' AND '.join(filters) or 'true'})
    {grouped_cte}, page AS (SELECT f.*,row_number() OVER(ORDER BY {ordering(config)}) AS row_position
      FROM filtered f ORDER BY {ordering(config)} LIMIT {limit} OFFSET {offset})
    SELECT (SELECT row_to_json(summary) FROM (SELECT {money_summary} FROM filtered) summary) AS summary,
      {groups_sql} AS groups,
      (SELECT COALESCE(jsonb_agg(jsonb_build_object('id',p.id,'customer_id',p.customer_id,'version_no',p.version_no,
        'values',jsonb_build_object({payload},'product_lines',p.product_lines,
          'product_line_source',p.product_line_source,'intent_product_line_ids',p.intent_product_line_ids,
          'legacy_product_line',p.legacy_product_line),'finance_meta',p.finance_meta,
        '_can_edit',p.can_edit,'_can_fde',p.can_fde,'_can_actual',p.can_actual,'_can_actual_write',p.can_actual_write,
        {row_group}) ORDER BY p.row_position),'[]'::jsonb) FROM page p) AS items'''
    # id is represented as UUID for ordering; text filters must use its text projection.
    sql = sql.replace('f."id"', 'f.id_text').replace('p."id"', 'p.id_text')
    return sql, builder.args


async def query_workspace(connection, actor, request):
    permissions = await AuthorizationRepository().effective(connection)
    from sales_backend.services.sales_opportunity_visibility import require_read_feature

    await require_read_feature(connection, permissions)
    if used_fields(request.config) & ACTUAL_KEYS:
        permissions.require('actual.read')
    await validate_scope(connection, actor, request.config, permissions=permissions)
    period = resolved_period(request.config.period)
    date_map = {}
    if 'business_created_on' in used_fields(request.config):
        builder = QueryBuilder()
        predicate = scope_predicate(builder, request.config.scope, actor)
        rows = await connection.fetch('SELECT o.id::text,' + creation_date_projection() + ' AS fact FROM crm.opportunity o WHERE ' + predicate, *builder.args)
        for row in rows:
            value = business_created_on(json_value(row['fact']))
            date_map[row['id']] = value.isoformat() if value else None
    sql, args = compile_query(request, actor, date_map=date_map, period=period)
    result = await connection.fetchrow(sql, *args)
    items = json_value(result['items'])
    for item in items:
        can_edit, can_fde = item.pop('_can_edit'), item.pop('_can_fde')
        can_actual, can_actual_write = item.pop('_can_actual'), item.pop('_can_actual_write')
        permitted = editable_fields(can_edit=can_edit, can_fde=can_fde)
        if item['values'].get('product_line_source') == 'materials':
            permitted = [key for key in permitted if key != 'intent_product_line_ids']
        if can_actual and can_actual_write:
            permitted += sorted(ACTUAL_KEYS)
        item['capabilities'] = {'editable_fields': permitted, 'can_manage_fde_members': bool(can_edit and can_fde)}
        if not can_actual:
            for key in ACTUAL_KEYS:
                item['values'].pop(key, None)
            item.pop('finance_meta', None)
    summary = json_value(result['summary'])
    total = summary['count']
    has_more = request.offset + request.limit < total
    return {'definition_version': DEFINITION_VERSION, 'items': items, 'summary': summary,
            'total': total, 'has_more': has_more, 'next_offset': request.offset + request.limit if has_more else None,
            'groups': json_value(result['groups']), 'resolved_period': period}
