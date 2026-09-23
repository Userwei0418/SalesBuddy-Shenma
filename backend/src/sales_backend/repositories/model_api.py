"""Tenant-scoped, append-only model connection releases and test receipts."""

from __future__ import annotations

CREDENTIAL_FIELDS = ("api_key_ciphertext", "cipher_format", "encryption_key_id", "api_key_tail")
SAFE_RELEASE = "r.purpose,r.version_no,r.config_snapshot,r.created_at,r.restored_from_version,r.test_id"


class ModelApiRepository:
    async def lock(self, conn, actor, purpose):
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", f"model-api:{actor.workspace_id}:{purpose}"
        )

    async def current(self, conn, actor, purpose):
        row = await conn.fetchrow(
            """SELECT r.* FROM config.model_api_current c
          JOIN config.model_api_release r USING(workspace_id,purpose,version_no)
          WHERE c.workspace_id=$1::uuid AND c.purpose=$2""",
            actor.workspace_id,
            purpose,
        )
        return dict(row) if row else None

    async def releases(self, conn, actor, purpose):
        rows = await conn.fetch(
            f"""SELECT {SAFE_RELEASE},u.display_name AS created_by
          FROM config.model_api_release r LEFT JOIN platform.user_ref u ON u.id=r.created_by_user_ref_id
          WHERE r.workspace_id=$1::uuid AND r.purpose=$2 ORDER BY r.version_no DESC LIMIT 100""",
            actor.workspace_id,
            purpose,
        )
        return [dict(r) for r in rows]

    async def release_snapshot(self, conn, actor, purpose, version):
        return await conn.fetchval(
            """SELECT config_snapshot FROM config.model_api_release
          WHERE workspace_id=$1::uuid AND purpose=$2 AND version_no=$3""",
            actor.workspace_id,
            purpose,
            version,
        )

    async def test(self, conn, actor, test_id):
        row = await conn.fetchrow(
            """SELECT *,expires_at>clock_timestamp() AS unexpired
          FROM config.model_api_test WHERE workspace_id=$1::uuid AND id=$2::uuid""",
            actor.workspace_id,
            str(test_id),
        )
        return dict(row) if row else None

    async def recent_tests(self, conn, actor, purpose):
        return await conn.fetchval(
            """SELECT count(*) FROM config.model_api_test
          WHERE workspace_id=$1::uuid AND purpose=$2 AND created_at>clock_timestamp()-interval '1 minute'""",
            actor.workspace_id,
            purpose,
        )

    async def reserve(self, conn, actor, purpose, body, digest, credential, guard):
        await conn.execute(
            """INSERT INTO config.model_api_test
          (id,workspace_id,purpose,actor_user_ref_id,expected_version,request_digest,config_snapshot,
           api_key_ciphertext,cipher_format,encryption_key_id,api_key_tail,source_guard,restored_from_version)
          VALUES($1::uuid,$2::uuid,$3,$4::uuid,$5,$6,$7::jsonb,$8,$9,$10,$11,$12,$13)""",
            str(body.request_id),
            actor.workspace_id,
            purpose,
            actor.user_id,
            body.expected_version,
            digest,
            body.configuration.model_dump(),
            *(credential.get(f) for f in CREDENTIAL_FIELDS),
            guard,
            body.restored_from_version,
        )

    async def finish(self, conn, actor, test_id, status, result):
        await conn.execute(
            """UPDATE config.model_api_test SET status=$3,result=$4::jsonb
          WHERE workspace_id=$1::uuid AND id=$2::uuid AND status='running' """,
            actor.workspace_id,
            str(test_id),
            status,
            result,
        )

    async def published_test(self, conn, actor, test_id):
        return await conn.fetchval(
            """SELECT version_no FROM config.model_api_release
          WHERE workspace_id=$1::uuid AND test_id=$2::uuid""",
            actor.workspace_id,
            str(test_id),
        )

    async def publish(self, conn, actor, test):
        version = test["expected_version"] + 1
        await conn.execute(
            """INSERT INTO config.model_api_release
          (workspace_id,purpose,version_no,config_snapshot,api_key_ciphertext,cipher_format,
           encryption_key_id,api_key_tail,test_id,created_by_user_ref_id,restored_from_version)
          VALUES($1::uuid,$2,$3,$4::jsonb,$5,$6,$7,$8,$9::uuid,$10::uuid,$11)""",
            actor.workspace_id,
            test["purpose"],
            version,
            test["config_snapshot"],
            *(test.get(f) for f in CREDENTIAL_FIELDS),
            str(test["id"]),
            actor.user_id,
            test["restored_from_version"],
        )
        await conn.execute(
            """INSERT INTO config.model_api_current(workspace_id,purpose,version_no)
          VALUES($1::uuid,$2,$3) ON CONFLICT(workspace_id,purpose)
          DO UPDATE SET version_no=EXCLUDED.version_no""",
            actor.workspace_id,
            test["purpose"],
            version,
        )
        await self.audit(
            conn,
            actor,
            "publish",
            {
                "purpose": test["purpose"],
                "version": version,
                "test_id": str(test["id"]),
                "restored_from_version": test["restored_from_version"],
            },
        )
        return version

    async def audit(self, conn, actor, operation, metadata):
        await conn.execute(
            """INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,
          action_code,object_type,after_snapshot,result_code,sensitivity)
          VALUES($1::uuid,$2::uuid,$3,$4,'model_api',$5::jsonb,'success','restricted')""",
            actor.workspace_id,
            actor.user_id,
            actor.role.value,
            "admin.model_api." + operation,
            metadata,
        )
