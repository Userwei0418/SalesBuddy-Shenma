# ruff: noqa: S608 -- SQL fragments are server-owned; every user value is bound.
"""Read-only formal follow-ups, using existing RLS and original-material authorization."""
from datetime import UTC, datetime, time, timedelta

from sales_backend.domain.visit_dates import BEIJING, visit_date_fields
from sales_backend.repositories.visits import VisitRepository

SUPPLEMENT_FIELDS = ['partner_name', 'contact_name', 'contact_title', 'interaction_at',
                     'interaction_mode', 'visit_location', 'created_date']
FIRST_FIELDS = ['customer_main_business', 'customer_needs', 'customer_budget', 'contact_role']


def selection(*, scope='all', q='', recorder_id=None, recorder_ids=None, team_id=None, customer_id=None,
              opportunity_id=None, date_basis='interaction', date_from=None, date_to=None, score_min=None,
              score_max=None, visit_id=None):
    args, predicates = [], ["v.deleted_at IS NULL", "v.status IN ('confirmed','archived')"]

    def bind(value, cast):
        args.append(value)
        return f'${len(args)}::{cast}'

    if visit_id:
        predicates.append('v.id=' + bind(visit_id, 'uuid'))
    if scope == 'mine':
        predicates.append('v.recorder_user_ref_id=common.current_user_ref_id()')
    if recorder_id:
        predicates.append('v.recorder_user_ref_id=' + bind(recorder_id, 'uuid'))
    # The Web follow-up page needs a bounded team/member scope without making
    # one request per customer.  Keep all values bound and let the existing
    # visit RLS policy remain the final authorization gate.
    if recorder_ids:
        args.append([str(value) for value in recorder_ids])
        predicates.append(f'v.recorder_user_ref_id=ANY(${len(args)}::uuid[])')
    if team_id:
        predicates.append('v.recorder_team_id=' + bind(team_id, 'uuid'))
    if customer_id:
        customer_ref = bind(customer_id, 'uuid')
        predicates.append(f'''({customer_ref}=v.customer_id OR EXISTS (
          SELECT 1 FROM activity.visit_opportunity vo JOIN crm.opportunity linked
            ON linked.id=vo.opportunity_id AND linked.deleted_at IS NULL
          WHERE vo.visit_id=v.id AND linked.customer_id={customer_ref}
        ) OR (v.customer_id IS NULL AND v.opportunity_id IS NOT NULL AND EXISTS (
          SELECT 1 FROM crm.opportunity linked
          WHERE linked.id=v.opportunity_id AND linked.customer_id={customer_ref} AND linked.deleted_at IS NULL
        )))''')
    if opportunity_id:
        opportunity_ref = bind(opportunity_id, 'uuid')
        predicates.append(f'''({opportunity_ref}=v.opportunity_id OR EXISTS (
          SELECT 1 FROM activity.visit_opportunity vo
          WHERE vo.visit_id=v.id AND vo.opportunity_id={opportunity_ref}
        ))''')
    column = 'v.created_at' if date_basis == 'created' else 'v.interaction_at'
    if date_from:
        predicates.append(column + '>=' + bind(datetime.combine(date_from, time(), BEIJING), 'timestamptz'))
    if date_to:
        predicates.append(column + '<' + bind(datetime.combine(date_to + timedelta(days=1), time(), BEIJING), 'timestamptz'))
    if score_min is not None:
        predicates.append('v.follow_up_score>=' + bind(score_min, 'numeric'))
    if score_max is not None:
        predicates.append('v.follow_up_score<=' + bind(score_max, 'numeric'))
    customer_name = "COALESCE(NULLIF(v.archived_fields->>'customer_name',''),c.name,security.customer_reference(v.customer_id)->>'name')"
    partner_name = "COALESCE(p.name,NULLIF(v.partner_name_snapshot,''))"
    if q:
        term = bind(q, 'text')
        predicates.append(f"""(strpos(lower(concat_ws(' ',{customer_name},{partner_name},u.display_name,
          v.original_recorder_name,v.follow_up_record,v.next_action)),lower({term}))>0
          OR EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.deleted_at IS NULL
            AND (o.id=v.opportunity_id OR EXISTS(SELECT 1 FROM activity.visit_opportunity vo
              WHERE vo.visit_id=v.id AND vo.opportunity_id=o.id))
            AND strpos(lower(o.name),lower({term}))>0))""")
    # The complete filtered set contains only small metadata. Body text and
    # archived JSON are never materialized for summaries or paginated details.
    cte = f"""WITH filtered AS MATERIALIZED (
      SELECT v.id,v.customer_id,v.partner_id,v.opportunity_id,v.recorder_user_ref_id,
        v.created_at,v.interaction_at,v.follow_up_score,u.display_name AS recorder_name,
        {customer_name} AS customer_name,{partner_name} AS partner_name
      FROM activity.visit v LEFT JOIN platform.user_ref u ON u.id=v.recorder_user_ref_id
      LEFT JOIN crm.customer c ON c.id=v.customer_id
      LEFT JOIN crm.partner p ON p.id=v.partner_id
      WHERE {' AND '.join(predicates)}
    )"""
    return cte, args, column.replace('v.', 'f.')


PROJECTION = """SELECT v.id::text,v.status,v.customer_id::text,f.customer_name,v.partner_id::text,f.partner_name,
 v.opportunity_id::text,o.name AS opportunity_name,v.recorder_user_ref_id::text AS recorder_id,f.recorder_name,
 CASE WHEN v.recorder_user_ref_id IS NULL THEN NULL ELSE u.status='active' AND u.deleted_at IS NULL END AS recorder_active,
 v.recording_role_code_snapshot,v.recording_team_id_snapshot::text,
 CASE WHEN v.import_meta->>'import_type'='crm_history' THEN 'crm_history'
 WHEN v.recording_role_code_snapshot IS NOT NULL THEN 'recorded' ELSE 'unknown' END AS record_source,
 v.original_recorder_name,v.manager_user_ref_id::text AS manager_id,manager.display_name AS manager_name,
 v.created_by_user_ref_id::text AS creator_id,creator.display_name AS creator_name,
 v.interaction_at,v.recorded_on,v.created_at,v.interaction_mode_code,v.contact_name_snapshot AS contact_name,
 v.is_first_visit,v.follow_up_score,
 CASE WHEN jsonb_typeof(v.quality_review)='object' AND v.quality_review<>'{}'::jsonb
 THEN jsonb_build_object('follow_up_score',v.quality_review->'follow_up_score',
 'grade',v.quality_review->'grade','next_action_passed',v.quality_review->'next_action_passed') END AS quality_review,
 COALESCE(security.has_customer_access(v.customer_id),false) AS can_open_customer,
 COALESCE(security.has_opportunity_read_access(v.opportunity_id),false) AS can_open_opportunity,
 COALESCE((SELECT jsonb_agg(jsonb_build_object('id',linked.id::text,'name',linked.name,
   'customer_id',linked.customer_id::text,'customer_name',COALESCE(c.name,security.customer_reference(linked.customer_id)->>'name'),
   'can_open',security.has_opportunity_read_access(linked.id)) ORDER BY linked.name,linked.id)
   FROM crm.opportunity linked LEFT JOIN crm.customer c ON c.id=linked.customer_id
   WHERE linked.deleted_at IS NULL AND (linked.id=v.opportunity_id OR EXISTS(
     SELECT 1 FROM activity.visit_opportunity vo WHERE vo.visit_id=v.id AND vo.opportunity_id=linked.id))),'[]'::jsonb) AS linked_opportunities,
 left(v.follow_up_record,600) AS follow_up_record,left(v.next_action,600) AS next_action,true AS is_summary
 FROM page JOIN activity.visit v ON v.id=page.id JOIN filtered f ON f.id=v.id
 LEFT JOIN platform.user_ref u ON u.id=v.recorder_user_ref_id
 LEFT JOIN platform.user_ref manager ON manager.id=v.manager_user_ref_id
 LEFT JOIN platform.user_ref creator ON creator.id=v.created_by_user_ref_id
 LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id"""


def public_record(row):
    item = visit_date_fields(dict(row))
    quality = item.get('quality_review')
    item['quality_review'] = None
    if isinstance(quality, dict) and quality:
        score, grade, passed = (quality.get(k) for k in ('follow_up_score', 'grade', 'next_action_passed'))
        item['quality_review'] = dict(follow_up_score=score if type(score) in (int, float) else None,
                                      grade=grade if isinstance(grade, str) else None,
                                      next_action_passed=passed if type(passed) is bool else None)
    kind = 'customer' if item['customer_id'] else 'partner' if item['partner_id'] else 'opportunity' if item['linked_opportunities'] else 'unknown'
    item['subject_kind'] = kind
    item['subject_id'] = item['customer_id'] or item['partner_id'] or item['opportunity_id']
    item['subject_name'] = ((item['customer_name'] or '历史客户') if kind == 'customer' else
                            (item['partner_name'] or '历史伙伴') if kind == 'partner' else
                            '、'.join(o['name'] for o in item['linked_opportunities']) if kind == 'opportunity' else '历史主体待核对')
    return item


class VisitManagementRepository:
    async def page(self, connection, *, limit=50, offset=0, **filters):
        as_of = datetime.now(UTC)
        cte, args, order = selection(**filters)
        summary = await connection.fetchrow(cte + """, subjects AS (
          SELECT 'customer' kind,customer_id id FROM filtered WHERE customer_id IS NOT NULL
          UNION SELECT 'partner',partner_id FROM filtered WHERE partner_id IS NOT NULL
          UNION SELECT 'customer',o.customer_id FROM filtered f JOIN crm.opportunity o ON
            (o.id=f.opportunity_id OR EXISTS(SELECT 1 FROM activity.visit_opportunity vo
              WHERE vo.visit_id=f.id AND vo.opportunity_id=o.id))
            WHERE o.deleted_at IS NULL AND o.customer_id IS NOT NULL
        ) SELECT count(*)::int total,count(*) FILTER(WHERE follow_up_score IS NOT NULL)::int scored_count,
          (SELECT count(*)::int FROM subjects) subject_count FROM filtered""", *args)
        rows = await self._rows(connection, cte, args, order, limit=limit, offset=offset)
        more = len(rows) > limit
        return dict(items=[public_record(row) for row in rows[:limit]], summary=dict(summary),
                    has_more=more, next_offset=offset + limit if more else None, as_of=as_of)

    async def _rows(self, connection, cte, args, order, *, limit, offset):
        args = [*args, limit + 1, offset]
        sql = cte + f""", page AS MATERIALIZED (SELECT f.id FROM filtered f
          ORDER BY {order} DESC NULLS LAST,f.created_at DESC,f.id DESC
          LIMIT ${len(args)-1} OFFSET ${len(args)}) """
        return await connection.fetch(sql + PROJECTION + f' ORDER BY {order} DESC NULLS LAST,f.created_at DESC,f.id DESC', *args)

    async def recorders(self, connection, *, recorder_q='', limit=50, offset=0, **filters):
        as_of = datetime.now(UTC)
        cte, args, _ = selection(**filters)
        unmapped = await connection.fetchval(cte + ' SELECT count(*)::int FROM filtered WHERE recorder_user_ref_id IS NULL', *args)
        args = [*args, recorder_q, limit + 1, offset]
        rows = await connection.fetch(cte + f""" SELECT u.id::text,u.display_name AS name,u.account_code,
          (u.status='active' AND u.deleted_at IS NULL) AS active,count(*)::int record_count
          FROM filtered f JOIN platform.user_ref u ON u.id=f.recorder_user_ref_id
          WHERE ${len(args)-2}::text='' OR strpos(lower(concat_ws(' ',u.display_name,u.account_code)),lower(${len(args)-2}))>0
          GROUP BY u.id ORDER BY u.display_name,u.id LIMIT ${len(args)-1} OFFSET ${len(args)}""", *args)
        more = len(rows) > limit
        return dict(items=[dict(r) for r in rows[:limit]], unmapped_count=unmapped,
                    has_more=more, next_offset=offset + limit if more else None, as_of=as_of)

    async def detail(self, connection, visit_id):
        cte, args, order = selection(visit_id=visit_id)
        rows = await self._rows(connection, cte, args, order, limit=1, offset=0)
        if not rows:
            raise LookupError('跟进记录不存在或不可见')
        detail = await VisitRepository().detail(connection, visit_id)
        result = {**detail, **public_record(rows[0])}
        result.update(follow_up_record=detail['follow_up_record'], next_action=detail['next_action'])
        permission = await connection.fetchrow("""SELECT v.source_import_id::text,
          v.recorder_user_ref_id=common.current_user_ref_id() AS own,
          security.authorization_has('visit.supplement') AND security.authorization_visit('visit.supplement',v.id)
            AND security.can_write_visit(v.id) AS authorized
          FROM activity.visit v WHERE v.id=$1::uuid""", visit_id)
        reason = ('仅原跟进人可补充辅助信息' if not permission['own'] else
                  '当前账号没有补充此记录的权限' if not permission['authorized'] else
                  '此历史记录未关联客户，暂不支持辅助补充' if not result['customer_id'] else None)
        result.update(can_supplement=reason is None, supplement_unavailable_reason=reason,
                      supplement_fields=[] if reason else SUPPLEMENT_FIELDS + (FIRST_FIELDS if result['is_first_visit'] else []))
        materials = []
        if permission['source_import_id']:
            row = await connection.fetchrow("""SELECT i.id::text,i.filename,i.file_size,
              security.authorization_import('visit.download_original',i.id) AS can_download
              FROM activity.visit v JOIN activity.visit_import i ON i.id=v.source_import_id AND i.workspace_id=v.workspace_id
              WHERE v.id=$1::uuid""", visit_id)
            if row:
                material = dict(row)
                material['download_url'] = f"/api/v1/visit-imports/{material['id']}/original" if material['can_download'] else None
                materials.append(material)
        result.update(original_materials=materials, original_material_status=
                      'available' if any(m['can_download'] for m in materials) else
                      'unavailable' if permission['source_import_id'] else 'none')
        return result
