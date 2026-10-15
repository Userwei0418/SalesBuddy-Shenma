"""Read one frozen sales review with independently authorized source evidence."""
from collections import OrderedDict
from uuid import UUID

from sales_backend.db import json_value
from sales_backend.repositories.competency_lifecycle import can_review
from sales_backend.services.authorization import require_permission
from sales_backend.services.growth_projection import public_review


async def review_detail(connection, actor, review_id):
    await require_permission(connection, 'profile.sales_read')
    row = await connection.fetchrow('''SELECT r.*,u.display_name AS subject_name,u.account_code
      FROM insight.sales_competency_review r JOIN platform.user_ref u
       ON u.id=r.subject_user_ref_id AND u.workspace_id=r.workspace_id
      WHERE r.id=$1::uuid AND r.workspace_id=$2::uuid AND r.status='succeeded'
       AND security.authorization_subject('profile.sales_read','person',r.subject_user_ref_id,NULL)''',
      str(review_id), actor.workspace_id)
    if row is None:
        raise LookupError('评估不存在或不可见')
    row = dict(row)
    snapshot = json_value(row.get('input_snapshot')) or {}
    raw = json_value(row.get('evidence')) or {}
    public = public_review(row)
    grouped = OrderedDict()
    detail_sources = {}
    for code, entries in raw.items():
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            try:
                visit_id = str(UUID(str(item.get('visit_id'))))
            except (ValueError, TypeError):
                visit_id = None
            detail = item.get('detail') if isinstance(item.get('detail'), str) else None
            detail_sources.setdefault(detail, set()).add(visit_id)
            # Unknown historical dimensions still constrain joint provenance;
            # the public dimension whitelist controls display only.
            if code in public['dimension_scores']:
                grouped.setdefault((visit_id, detail), []).append(code)
    ids = sorted({ref for refs in detail_sources.values() for ref in refs if ref})
    readable = {str(r['id']) for r in await connection.fetch('''SELECT id FROM activity.visit
      WHERE workspace_id=$1::uuid AND id=ANY($2::uuid[]) AND deleted_at IS NULL
       AND security.authorization_visit('visit.read',id)''', actor.workspace_id, ids)} if ids else set()
    versions = {str(v.get('visit_id')): v.get('version_no') for v in
                (snapshot.get('facts') or {}).get('visits', []) if isinstance(v, dict)}
    evidence = []
    for (visit_id, detail), codes in grouped.items():
        # A validated statement may cite several visits, including across
        # dimensions. Every copy must disappear if any cited source is lost.
        # Legacy evidence without frozen source linkage cannot prove its scope.
        refs = detail_sources[detail]
        visible = bool(detail) and snapshot.get('contract_version') == 'sales.competency.v2' \
            and None not in refs and refs <= readable and refs <= versions.keys()
        version = versions.get(visit_id)
        evidence.append({'visit_id': visit_id if visible else None,
                         'visit_version': version if visible and type(version) is int else None,
                         'dimension_codes': list(dict.fromkeys(codes)),
                         'detail': detail if visible else None, 'readable': visible})
    total_visible = sum(item['readable'] for item in evidence)
    return {'contract_version': 'sales.growth.v2', 'assessment_kind': 'sales_competency',
            'subject': {'id': str(row['subject_user_ref_id']), 'name': row['subject_name'],
                        'account_code': row['account_code']}, 'review': public,
            'evidence': evidence, 'evidence_summary': {'total': len(evidence), 'readable': total_visible,
                                                       'unavailable': len(evidence)-total_visible},
            'can_review': str(row['subject_user_ref_id']) == actor.user_id and await can_review(connection, actor)}
