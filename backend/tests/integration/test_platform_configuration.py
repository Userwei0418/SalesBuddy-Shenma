"""Tenant configuration projections through the actual restricted platform API."""

from uuid import UUID, uuid4

import asyncpg
import pytest

from tests.integration.feishu_fixtures import fixture_owner
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_platform_control import BASE, provision
# Explicit re-export keeps the shared pytest fixture discoverable in this module.
from tests.integration.test_platform_control import platform as platform

pytestmark = pytest.mark.asyncio


async def read(client, workspace):
    response = await client.get(f"{BASE}/tenants/{workspace}/configuration")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


async def model_release(connection, ws, purpose, snapshot, *, credential=False):
    actor = await connection.fetchval("SELECT administrator_id FROM control.tenant_setup WHERE workspace_id=$1", ws)
    test = uuid4()
    await connection.execute(
        """INSERT INTO config.model_api_test
        (id,workspace_id,purpose,actor_user_ref_id,expected_version,request_digest,config_snapshot)
        VALUES($1,$2,$3,$4,0,$5,$6)""",
        test,
        ws,
        purpose,
        actor,
        "0" * 64,
        snapshot,
    )
    await connection.execute(
        """INSERT INTO config.model_api_release
        (workspace_id,purpose,version_no,test_id,created_by_user_ref_id,config_snapshot,
         api_key_ciphertext,cipher_format,encryption_key_id,api_key_tail)
        VALUES($1,$2,1,$3,$4,$5,$6,$7,$8,$9)""",
        ws,
        purpose,
        test,
        actor,
        snapshot,
        bytes(29) if credential else None,
        "aes256gcm-v1" if credential else None,
        "synthetic-test" if credential else None,
        "test" if credential else None,
    )
    await connection.execute("INSERT INTO config.model_api_current VALUES($1,$2,1)", ws, purpose)


async def test_new_tenant_reports_unconfigured_defaults_without_claiming_ready(platform):
    client, _ = platform
    _, _, created = await provision(client)
    report = await read(client, created["id"])
    assert report["workspace_id"] == created["id"]
    assert report["sampled_at"] and report["version_no"] >= 1
    assert not report["identity_checks"]["activation_complete"]
    assert {m["code"]: m["effective"] for m in report["modules"]} == {"ai": False, "feishu": False, "visit": True}
    assert [x["purpose"] for x in report["model_connections"]] == ["text", "asr", "tts"]
    for item in report["model_connections"]:
        assert item["source"] == "instance_default"
        assert item["version_no"] is None
        assert not item["credential_saved"] and not item["fields_present"]
    assert report["feishu"] == dict(
        mapping_fields_present=False,
        configured=False,
        revision=None,
        enabled=False,
        validated=False,
        validation_pending=False,
        credential_saved=False,
        fields_present=False,
        direction_supported=False,
        enabled_mappings=0,
    )


async def test_configuration_uses_exact_tenant_current_version_and_never_returns_private_values(platform, connection):
    client, _ = platform
    _, _, a = await provision(client)
    _, _, b = await provision(client)
    ws = UUID(a["id"])
    marker = "PRIVATE-CONFIG-MUST-NOT-LEAVE"
    async with fixture_owner(connection):
        await model_release(
            connection,
            ws,
            "text",
            {
                "mode": "custom",
                "provider_name": marker,
                "endpoint_url": "https://private-config.invalid/v1/chat/completions",
                "model": marker,
                "protocol": "chat_completions",
                "unexpected_private": marker,
            },
            credential=True,
        )
        await model_release(
            connection,
            ws,
            "asr",
            {
                "mode": "custom",
                "provider_name": marker,
                "endpoint_url": "https://private-config.invalid/v1/audio/transcriptions",
                "model": "",
                "protocol": "audio_transcriptions",
            },
        )
        await model_release(connection, ws, "tts", {"mode": "disabled"})
        actor = await connection.fetchval("SELECT administrator_id FROM control.tenant_setup WHERE workspace_id=$1", ws)
        cid = uuid4()
        await connection.execute(
            """INSERT INTO config.feishu_connection(id,workspace_id,revision,settings,updated_by,validation_requested)
            VALUES($1,$2,3,$3,$4,true)""",
            cid,
            ws,
            {
                "workspace_id": str(ws),
                "connection_id": str(cid),
                "app_id": marker,
                "base_token": marker,
                "credential_ref": str(uuid4()),
                "direction": "system_to_base",
                "mappings": {"customer": {"enabled": True, "private": marker}, "visit": {"enabled": False}},
                "private": marker,
            },
            actor,
        )
        await connection.execute(
            "INSERT INTO security.feishu_credential(connection_id,workspace_id,ciphertext,encryption_key_id) VALUES($1,$2,$3,$4)",
            cid,
            ws,
            bytes(29),
            marker,
        )
        await connection.execute(
            "UPDATE control.tenant_module SET enabled=true,expires_at=clock_timestamp()-interval '1 minute' WHERE workspace_id=$1 AND module_code='ai'",
            ws,
        )
    report = await read(client, a["id"])
    assert marker not in str(report) and "private-config.invalid" not in str(report)
    text, asr, tts = report["model_connections"]
    assert text["source"] == "tenant_purpose" and text["version_no"] == 1
    assert text["credential_saved"] and text["fields_present"]
    assert not asr["credential_saved"] and not asr["fields_present"]
    assert tts["source"] == "disabled"
    ai = next(m for m in report["modules"] if m["code"] == "ai")
    assert ai["enabled"] and not ai["effective"]
    assert report["feishu"] == dict(
        mapping_fields_present=False,
        configured=True,
        revision=3,
        enabled=False,
        validated=False,
        validation_pending=True,
        credential_saved=True,
        fields_present=True,
        direction_supported=True,
        enabled_mappings=1,
    )
    other = await read(client, b["id"])
    assert all(x["source"] == "instance_default" for x in other["model_connections"])
    assert not other["feishu"]["configured"]
    async with fixture_owner(connection):
        # Reads must not queue a probe, enable connections or rewrite revisions.
        assert await connection.fetchval("SELECT validation_requested FROM config.feishu_connection WHERE id=$1", cid)
        assert await connection.fetchval("SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1", cid) == 0
        assert await connection.fetchval("SELECT count(*) FROM config.model_api_release WHERE workspace_id=$1", ws) == 3


async def test_legacy_presence_is_distinct_from_instance_inheritance_and_invalid_config(platform, connection):
    client, _ = platform
    _, _, created = await provision(client)
    ws = UUID(created["id"])
    async with fixture_owner(connection):
        await connection.execute(
            """INSERT INTO config.agent_runtime_config(workspace_id,provider_base_url,llm_model,asr_model,tts_model,
            api_key_ciphertext,cipher_format,encryption_key_id,api_key_tail,revision_no,encryption_revision)
            VALUES($1,'https://hidden.invalid','synthetic','','synthetic',$2,'aes256gcm-v1','synthetic','test',1,1)""",
            ws,
            bytes(29),
        )
        await model_release(connection, ws, "tts", {"mode": "unexpected"})
    report = await read(client, created["id"])
    text, asr, tts = report["model_connections"]
    assert text["source"] == "tenant_legacy" and text["credential_saved"] and text["fields_present"]
    assert asr["source"] == "tenant_legacy" and not asr["fields_present"]
    assert tts["source"] == "invalid" and not tts["credential_saved"]
    async with fixture_owner(connection):
        await connection.execute("UPDATE config.agent_runtime_config SET enabled=false WHERE workspace_id=$1", ws)
    assert (await read(client, created["id"]))["model_connections"][0]["source"] == "instance_default"


async def test_configuration_requires_live_platform_mfa_and_exposes_no_direct_table_access(platform, connection):
    client, database = platform
    _, _, created = await provision(client)
    path = f"{BASE}/tenants/{created['id']}/configuration"
    assert (await client.get(f"{BASE}/tenants/{uuid4()}/configuration")).status_code == 404
    assert (await client.get(f"{BASE}/tenants/not-a-uuid/configuration")).status_code == 422
    async with await client_for(connection) as company:
        await sign_in(company, "ADMIN001")
        assert (await company.get(path)).status_code == 401
    async with database.connection() as conn:
        for table in ("config.agent_runtime_config", "config.model_api_release", "security.feishu_credential"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.fetch("SELECT * FROM " + table)
    async with fixture_owner(connection):
        await connection.execute(
            "UPDATE control.session SET expires_at=clock_timestamp()-interval '1 second' WHERE operator_id=$1",
            database.operator_id,
        )
    assert (await client.get(path)).status_code in (401, 403)
