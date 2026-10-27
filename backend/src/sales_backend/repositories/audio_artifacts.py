"""Durable artifacts produced by external audio transcription."""

async def save_transcript(connection, actor, artifact_id, result, purpose, filename, model,
                          *, source_text=None, normalization=None, operation_id=None):
    payload = {"text": result.text, "purpose": purpose, "duration_seconds": result.duration_seconds}
    if source_text is not None:
        payload.update(source_text=source_text, normalization=normalization)
    evidence = {"source": "senseaudio_asr", "trace_id": result.trace_id,
                "filename": filename, "normalized_format": "wav_16khz_mono"}
    if operation_id is not None:
        evidence["asr_operation_id"] = operation_id
    await connection.execute(
        """
        INSERT INTO agent.artifact (
          id, workspace_id, artifact_type, schema_code, schema_version, status,
          payload, evidence, confidence, model_ref, created_by_user_ref_id
        ) VALUES ($1::uuid, $2::uuid, 'audio_transcript', 'audio_transcript.v1', 1,
          'confirmed', $3::jsonb, $4::jsonb, 1, $5, $6::uuid)
        ON CONFLICT(id) DO NOTHING
        """,
        artifact_id,
        actor.workspace_id,
        payload,
        [evidence],
        model,
        actor.user_id,
    )
