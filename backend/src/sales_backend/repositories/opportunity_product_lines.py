"""One product-line projection without multiplying opportunity/finance rows."""

from sales_backend.db import json_value

# Internal SQL only. The caller supplies an already authorized crm.opportunity o.
# Frozen material membership wins; renaming a dictionary label does not rewrite
# a transaction snapshot. Disabled intent selections remain readable/removable.
PRODUCT_LINES_SQL = """(
 WITH active_lines AS MATERIALIZED (
  SELECT m.id,m.catalog_snapshot FROM crm.opportunity_material m
  WHERE m.opportunity_id=o.id AND m.workspace_id=o.workspace_id AND m.is_active
 ), chosen AS (
  SELECT DISTINCT ON (catalog_snapshot->>'product_line_id')
   catalog_snapshot->>'product_line_id' AS id,
   catalog_snapshot->>'product_line_name' AS frozen_name
  FROM active_lines ORDER BY catalog_snapshot->>'product_line_id',active_lines.id
 ), selected AS (
  SELECT id,frozen_name FROM chosen
  UNION ALL SELECT intent_id::text,NULL::text FROM unnest(o.intent_product_line_ids) intent_id
  WHERE NOT EXISTS(SELECT 1 FROM active_lines)
 )
 SELECT COALESCE(jsonb_agg(jsonb_build_object('id',s.id,'code',COALESCE(i.item_code,''),
  'name',COALESCE(i.item_label,s.frozen_name,'历史产品线'),'is_active',COALESCE(i.is_active,false))
  ORDER BY COALESCE(i.sort_order,0),s.id),'[]'::jsonb)
 FROM selected s LEFT JOIN config.dictionary_item i ON i.id::text=s.id
  AND i.dictionary_id IN (SELECT d.id FROM config.dictionary d
   WHERE d.workspace_id=o.workspace_id AND d.code='material_product_line')
)"""

PRODUCT_LINE_DISPLAY_SQL = """COALESCE((SELECT string_agg(p->>'name',' / ' ORDER BY n)
 FROM jsonb_array_elements(pl.items) WITH ORDINALITY AS e(p,n)),o.product_line)"""


def line_projection(*, legacy, intent_ids, intent_lines, material_count, product_lines):
    source = 'materials' if material_count else 'intent' if intent_ids else 'legacy' if legacy else 'none'
    return {
        'product_lines': product_lines, 'product_line_source': source,
        'intent_product_line_ids': [str(value) for value in intent_ids],
        'intent_product_lines': intent_lines, 'legacy_product_line': legacy,
        'product_line': ' / '.join(row['name'] for row in product_lines) if product_lines else legacy,
    }


async def attach_product_lines(connection, rows):
    if not rows:
        return rows
    facts = await connection.fetch(
        f"""SELECT o.id::text,o.product_line AS legacy_product_line,o.intent_product_line_ids,
        {PRODUCT_LINES_SQL} AS product_lines,
        (SELECT count(*) FROM crm.opportunity_material m WHERE m.opportunity_id=o.id
          AND m.workspace_id=o.workspace_id AND m.is_active) AS material_count,
        COALESCE((SELECT jsonb_agg(jsonb_build_object('id',i.id,'code',i.item_code,
          'name',i.item_label,'is_active',i.is_active) ORDER BY i.sort_order,i.id)
          FROM config.dictionary_item i JOIN config.dictionary d ON d.id=i.dictionary_id
          WHERE i.id=ANY(o.intent_product_line_ids) AND d.workspace_id=o.workspace_id
          AND d.code='material_product_line'),'[]'::jsonb) AS intent_product_lines
        FROM crm.opportunity o WHERE o.id=ANY($1::uuid[])
        AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL""",  # noqa: S608
        [str(row['id']) for row in rows],
    )
    projected = {str(row['id']): line_projection(
        legacy=row['legacy_product_line'], intent_ids=row['intent_product_line_ids'],
        intent_lines=json_value(row['intent_product_lines']), material_count=row['material_count'],
        product_lines=json_value(row['product_lines']),
    ) for row in facts}
    for row in rows:
        if str(row['id']) in projected:
            row.update(projected[str(row['id'])])
    return rows


async def prepare_intent_lines(connection, data, before, *, has_materials):
    old = sorted(str(value) for value in (before or {}).get('intent_product_line_ids', []))
    if 'intent_product_line_ids' not in data:
        return old, []
    desired = sorted(str(value) for value in data['intent_product_line_ids'])
    if desired == old:
        return old, []
    if has_materials:
        raise ValueError('已有物料时产品线由物料自动汇总，请在无物料时维护意向产品线')
    added = sorted(set(desired) - set(old))
    if added:
        # Serialize with catalog maintenance; SELECT does not require UPDATE RLS.
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('material_catalog:'||common.current_workspace_id()::text,212))")
        valid = await connection.fetch(
            """SELECT i.id::text FROM config.dictionary_item i JOIN config.dictionary d ON d.id=i.dictionary_id
            WHERE i.id=ANY($1::uuid[]) AND d.workspace_id=common.current_workspace_id()
            AND d.code='material_product_line' AND d.status='active' AND i.is_active""", added,
        )
        if {row['id'] for row in valid} != set(added):
            raise ValueError('请选择本公司有效的意向产品线；停用项只能保留或移除')
    return desired, [{'field': 'intent_product_line_ids', 'label': '意向产品线', 'before': old, 'after': desired}]


async def snapshot_intent_changes(connection, changes):
    """Capture labels at the write boundary; never hydrate stored history later."""
    selected = [change for change in changes if isinstance(change, dict)
                and change.get('field') == 'intent_product_line_ids'
                and isinstance(change.get('before'), list) and isinstance(change.get('after'), list)]
    if not selected:
        return changes
    ids = sorted({str(value) for change in selected for side in ('before', 'after') for value in change[side]})
    # Removal-only edits also need this lock: an inactive item's name is still
    # part of the original evidence, even if no new selection needs validation.
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('material_catalog:'||common.current_workspace_id()::text,212))")
    rows = await connection.fetch(
        """SELECT i.id::text,i.item_code AS code,i.item_label AS name,i.is_active
        FROM config.dictionary_item i JOIN config.dictionary d ON d.id=i.dictionary_id
        WHERE i.id=ANY($1::uuid[]) AND d.workspace_id=common.current_workspace_id()
        AND d.code='material_product_line'""", ids,
    ) if ids else []
    labels = {row['id']: dict(row) for row in rows}

    def snapshot(values):
        return [{**labels.get(str(value), {'id': str(value), 'code': None, 'name': None, 'is_active': None}),
                 'status': 'recorded' if str(value) in labels else 'unavailable'} for value in values]

    return [{**change, 'display_snapshot': {
        'version': 1, 'basis': 'directory_at_change',
        'before': snapshot(change['before']), 'after': snapshot(change['after']),
    }} if any(change is item for item in selected) else change for change in changes]


async def legacy_for_save(connection, data, before):
    """Preserve omitted history and absorb old clients echoing derived display."""
    old = (before or {}).get('product_line')
    if 'product_line' not in data:
        return old
    value = data['product_line']
    # 7142 always echoes an empty display field, including with material_lines=[].
    # NULL and that empty echo both mean no legacy text; preserve the stored NULL.
    if value == old or (value == '' and old is None):
        return old
    if before:
        row = dict(before)
        await attach_product_lines(connection, [row])
        if row.get('product_line_source') in {'materials', 'intent'}:
            if value not in (None, old, row['product_line']):
                raise ValueError('产品线由统一目录关联，请使用意向产品线或物料选择')
            return old
    if 'intent_product_line_ids' in data or data.get('material_lines') is not None:
        if value not in (None, old):
            raise ValueError('新版商机请从统一目录选择产品线，历史文案只读保留')
        return old
    # Older clients may still create/edit unmapped historical text. It never
    # creates dictionary records or becomes an inferred ID association.
    if value is not None and len(value) > 200:
        raise ValueError('历史产品线文案不能超过200字，新录入请使用统一目录')
    return value
