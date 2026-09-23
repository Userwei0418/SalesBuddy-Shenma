"""Visit file records and queue scheduling in the same transaction."""


async def enqueue(connection, actor, import_id):
    await connection.execute(
        """INSERT INTO ops.job(workspace_id,job_type,aggregate_type,aggregate_id,payload,priority)
        VALUES($1::uuid,'visit.import','visit_import',$2::uuid,$3::jsonb,90)""",
        actor.workspace_id,
        import_id,
        {
            "user_id": actor.user_id,
            "role": actor.role.value,
            "data_scope": actor.data_scope.value,
            "team_ids": list(actor.team_ids),
        },
    )


async def create_import(connection, actor, import_id, filename, path, size):
    await connection.execute(
        "INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,"
        "filename,file_path,file_size,storage_key) "
        "VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5,$6,$5)",
        import_id,
        actor.workspace_id,
        actor.user_id,
        filename,
        str(path),
        size,
    )
    await enqueue(connection, actor, import_id)


async def import_detail(connection, actor, import_id):
    row = await connection.fetchrow(
        "SELECT id::text,filename,status,extracted_text,error_message FROM activity.visit_import "
        "WHERE id=$1::uuid AND (created_by_user_ref_id=$2::uuid OR security.is_fde_actor())",
        str(import_id),
        actor.user_id,
    )
    return dict(row) if row else None


async def retry_import(connection, actor, import_id):
    row = await connection.fetchrow(
        "SELECT status FROM activity.visit_import WHERE id=$1::uuid AND created_by_user_ref_id=$2::uuid FOR UPDATE",
        str(import_id),
        actor.user_id,
    )
    if not row:
        raise LookupError("文件任务不存在")
    if row["status"] == "failed":
        await connection.execute(
            "UPDATE activity.visit_import SET status='queued',error_message=NULL WHERE id=$1::uuid", str(import_id)
        )
        await enqueue(connection, actor, str(import_id))
    return {"id": str(import_id), "status": "queued" if row["status"] == "failed" else row["status"]}


async def original_location(connection, actor, import_id):
    # RLS also permits explicitly authorized historical visit readers.
    row = await connection.fetchrow(
        "SELECT id,filename,file_size,file_path,storage_profile,storage_driver,storage_key,content_sha256 "
        "FROM activity.visit_import WHERE id=$1::uuid",
        str(import_id),
    )
    return dict(row) if row else None
