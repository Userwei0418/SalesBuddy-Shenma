"""Private audio projection: never infer ownership from names or customer owners."""
from pathlib import PurePath

AUDIO_PATTERN = r"\.(mp3|wav|m4a|aac|ogg|flac|amr|webm)$"
AUDIO_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
              ".ogg": "audio/ogg", ".flac": "audio/flac", ".amr": "audio/amr", ".webm": "audio/webm"}


async def own_audio_location(connection, visit_id):
    row = await connection.fetchrow(
        """SELECT i.id,i.workspace_id,i.created_by_user_ref_id,i.filename,i.file_size,i.file_path,
        i.storage_profile,i.storage_driver,i.storage_key,i.content_sha256,
        v.id AS visit_id,v.recorder_user_ref_id
        FROM activity.visit v JOIN activity.visit_import i ON i.id=v.source_import_id AND i.workspace_id=v.workspace_id
        WHERE v.id=$1::uuid AND v.workspace_id=common.current_workspace_id()
        AND i.workspace_id=common.current_workspace_id()
        AND i.created_by_user_ref_id=common.current_user_ref_id()
        AND v.recorder_user_ref_id=common.current_user_ref_id()
        AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
        AND security.authorization_visit('visit.read',v.id) AND i.filename ~* $2""",
        visit_id, AUDIO_PATTERN,
    )
    return dict(row) if row else None


def audio_metadata(row):
    return {"id": str(row["id"]), "filename": row["filename"], "size_bytes": row["file_size"],
            "mime_type": AUDIO_MIME[PurePath(row["filename"]).suffix.lower()],
            "playback_path": f"/visits/{row['visit_id']}/audio"}
