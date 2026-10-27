"""All management queries are tenant scoped; immutable versions are the audit log."""
from datetime import UTC, datetime
from uuid import uuid4

from sales_backend.domain.asr import AsrError, digest, normalized_items


async def current(connection):
    row = await connection.fetchrow('''SELECT v.* FROM config.asr_hotword_current c
        JOIN config.asr_hotword_version v USING(workspace_id,version)
        WHERE c.workspace_id=common.current_workspace_id()''')
    return dict(row) if row else {'version': 0, 'items': []}


async def version(connection, number):
    row = await connection.fetchrow('''SELECT * FROM config.asr_hotword_version
        WHERE workspace_id=common.current_workspace_id() AND version=$1''', number)
    if not row:
        raise AsrError('ASR_OPERATION_NOT_FOUND', 404)
    return dict(row)


async def versions(connection):
    return [dict(r) for r in await connection.fetch('''SELECT version,created_at,created_by::text,reason,source_version
        FROM config.asr_hotword_version WHERE workspace_id=common.current_workspace_id()
        ORDER BY version DESC LIMIT 100''')]


async def operation(connection, operation_id):
    row = await connection.fetchrow('''SELECT * FROM config.asr_hotword_operation
        WHERE workspace_id=common.current_workspace_id() AND actor_id=common.current_user_ref_id()
        AND operation_id=$1::uuid''', str(operation_id))
    return dict(row) if row else None


async def save(connection, actor, body, *, source_version=None):
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('asr-hotwords:'||$1,282))", actor.workspace_id)
    requested = body.model_dump(mode='json')
    request_digest = digest(requested)
    prior = await operation(connection, body.operation_id)
    if prior:
        if prior['request_digest'] != request_digest:
            raise AsrError('ASR_OPERATION_CONFLICT')
        return {**await version(connection, prior['version']), 'replayed': True}
    existing = await current(connection)
    if existing['version'] != body.expected_version:
        raise AsrError('ASR_HOTWORD_VERSION_CONFLICT')
    items = normalized_items((await version(connection, source_version))['items']
                             if source_version else body.items)
    # Reuse a removed term's stable identity. Foreign/unknown IDs are never adopted.
    histories = await connection.fetch('''SELECT items FROM config.asr_hotword_version
        WHERE workspace_id=common.current_workspace_id() ORDER BY version''')
    known = {i['id']: i for h in histories for i in h['items']}
    by_name = {i['term'].casefold(): i['id'] for i in known.values()}
    old_by_id = {i['id']: i for i in existing['items']}
    now = datetime.now(UTC).isoformat()
    for item in items:
        if item.get('id') and item['id'] not in known:
            raise AsrError('ASR_OPERATION_NOT_FOUND', 404)
        item['id'] = item.get('id') or by_name.get(item['term'].casefold()) or str(uuid4())
        old = old_by_id.get(item['id'])
        same = old and all(old.get(k) == v for k, v in item.items())
        item.update(updated_by=old['updated_by'] if same else actor.user_id,
                    updated_at=old['updated_at'] if same else now)
    # IDs generated after normalization can collide when restoring a removed term.
    if len({i['id'] for i in items}) != len(items):
        raise AsrError('ASR_HOTWORD_DUPLICATE', 422)
    number = existing['version']
    if not number or items != existing['items']:
        number += 1
        await connection.execute('''INSERT INTO config.asr_hotword_version
            (workspace_id,version,items,created_by,reason,source_version) VALUES($1::uuid,$2,$3::jsonb,$4::uuid,$5,$6)''',
            actor.workspace_id, number, items, actor.user_id,
            ('恢复历史版本' if source_version else body.reason), source_version)
        await connection.execute('''INSERT INTO config.asr_hotword_current(workspace_id,version) VALUES($1::uuid,$2)
            ON CONFLICT(workspace_id) DO UPDATE SET version=EXCLUDED.version''', actor.workspace_id, number)
    await connection.execute('''INSERT INTO config.asr_hotword_operation
        (workspace_id,actor_id,operation_id,request_digest,version) VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5)''',
        actor.workspace_id, actor.user_id, str(body.operation_id), request_digest, number)
    return {**await version(connection, number), 'replayed': False}
