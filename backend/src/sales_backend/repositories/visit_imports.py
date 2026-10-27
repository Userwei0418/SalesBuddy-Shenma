"""Visit file records and queue scheduling in the same transaction."""


async def enqueue(connection, actor, import_id):
    await connection.execute(
        """INSERT INTO ops.job(workspace_id,job_type,aggregate_type,aggregate_id,payload,priority)
        VALUES($1::uuid,'visit.import','visit_import',$2::uuid,$3::jsonb,90)""",
        actor.workspace_id,
        import_id,
        {
            "workspace_id": actor.workspace_id,
            "user_id": actor.user_id,
            "role": actor.role.value,
            "data_scope": actor.data_scope.value,
            "team_ids": list(actor.team_ids),
        },
    )


async def create_import(connection, actor, import_id, filename, path, size, location=None):
    location = location or {'driver': 'local', 'profile': 'legacy-local', 'key': str(path), 'sha256': None}
    await connection.execute(
        "INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,"
        "filename,file_path,file_size,storage_key,storage_driver,storage_profile,content_sha256) "
        "VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5,$6,$7,$8,$9,$10)",
        import_id,
        actor.workspace_id,
        actor.user_id,
        filename,
        str(path) if location['driver']=='local' else '',
        size,
        location['key'], location['driver'], location['profile'], location['sha256'],
    )
    await enqueue(connection, actor, import_id)


async def import_detail(connection, actor, import_id):
    row = await connection.fetchrow(
        "SELECT id::text,filename,status,"
        "CASE WHEN created_by_user_ref_id=$2::uuid THEN evidence ELSE NULL END AS own_evidence,"
        "CASE WHEN created_by_user_ref_id=$2::uuid OR security.authorization_import('visit.download_original',id) "
        "THEN extracted_text ELSE NULL END AS extracted_text,"
        "CASE WHEN created_by_user_ref_id=$2::uuid THEN error_message "
        "WHEN error_message IS NOT NULL THEN '材料识别失败' ELSE NULL END AS error_message "
        "FROM activity.visit_import "
        "WHERE id=$1::uuid AND security.authorization_import('visit.read',id)",
        str(import_id), actor.user_id,
    )
    if not row:
        return None
    result = dict(row)
    evidence = result.pop('own_evidence', None)
    if result['status'] != 'succeeded' and isinstance(evidence, list):
        for entry in evidence:
            if isinstance(entry, dict) and entry.get('source') == 'asr_checkpoint':
                segments = entry.get('segments', [])
                if isinstance(segments, list) and all(isinstance(s, dict) and isinstance(s.get('text'), str) for s in segments):
                    result['partial_text'] = '\n'.join(s['text'] for s in segments)
                    result['progress'] = {'completed_segments': len(segments),
                                          'total_segments': len(entry.get('segment_sha256', []))}
                break
    return result


async def require_retry_access(connection, actor, import_id, *, lock=False):
    row = await connection.fetchrow(
        "SELECT status FROM activity.visit_import WHERE id=$1::uuid AND created_by_user_ref_id=$2::uuid "
        "AND workspace_id=common.current_workspace_id()" + (" FOR UPDATE" if lock else ""),
        str(import_id),
        actor.user_id,
    )
    if not row:
        raise LookupError("文件任务不存在")
    if not await connection.fetchval("SELECT security.authorization_import('visit.retry_import',$1::uuid)", str(import_id)):
        raise PermissionError("当前账号未获重试此材料的授权")
    return row


async def retry_import(connection, actor, import_id):
    row = await require_retry_access(connection, actor, import_id, lock=True)
    if row["status"] == "failed":
        active = await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM ops.job WHERE workspace_id=common.current_workspace_id() "
            "AND aggregate_type='visit_import' AND aggregate_id=$1::uuid AND job_type='visit.import' "
            "AND status IN ('queued','running'))", str(import_id),
        )
        if active:
            return {"id": str(import_id), "status": "failed"}
        await connection.execute(
            "UPDATE activity.visit_import SET status='queued',error_message=NULL WHERE id=$1::uuid", str(import_id)
        )
        await enqueue(connection, actor, str(import_id))
    return {"id": str(import_id), "status": "queued" if row["status"] == "failed" else row["status"]}


async def original_location(connection, actor, import_id):
    # RLS also permits explicitly authorized historical visit readers.
    row = await connection.fetchrow(
        "SELECT id,filename,file_size,file_path,storage_profile,storage_driver,storage_key,content_sha256 "
        "FROM activity.visit_import WHERE id=$1::uuid AND security.authorization_import('visit.download_original',id) AND security.authorization_import('visit.read',id)",
        str(import_id),
    )
    return dict(row) if row else None
