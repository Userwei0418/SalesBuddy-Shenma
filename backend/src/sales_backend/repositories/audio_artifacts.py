"""Durable artifacts produced by external audio transcription."""

async def save_transcript(connection, actor, artifact_id, result, purpose, filename, model):
    await connection.execute(
        """
        INSERT INTO agent.artifact (
          id, workspace_id, artifact_type, schema_code, schema_version, status,
          payload, evidence, confidence, model_ref, created_by_user_ref_id
        ) VALUES ($1::uuid, $2::uuid, 'audio_transcript', 'audio_transcript.v1', 1,
          'confirmed', $3::jsonb, $4::jsonb, 1, $5, $6::uuid)
        """,
        artifact_id,
        actor.workspace_id,
        {"text": result.text, "purpose": purpose, "duration_seconds": result.duration_seconds},
        [{
            "source": "senseaudio_asr",
            "trace_id": result.trace_id,
            "filename": filename,
            "normalized_format": "wav_16khz_mono",
        }],
        model,
        actor.user_id,
    )
