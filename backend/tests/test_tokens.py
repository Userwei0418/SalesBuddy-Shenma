from __future__ import annotations

import unittest
from dataclasses import replace

from sales_backend.auth.tokens import TokenError, TokenService
from sales_backend.config import Settings
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode


def settings() -> Settings:
    return Settings(
        app_env="test",
        database_url="postgresql://test",
        senseaudio_base_url="https://api.senseaudio.cn",
        senseaudio_api_key="test-key",
        asr_model="senseaudio-asr-lite-1.5-260319",
        tts_model="senseaudio-tts-1.5-260319",
        llm_model="senseaudio-s2-lite",
        timeout_seconds=1,
        max_retries=0,
        access_token_secret="test-secret-at-least-thirty-two-characters-long",
        access_token_issuer="sales-saas",
        access_token_audience="sales-mini-program",
        access_token_minutes=30,
        refresh_token_days=30,
        auth_mode="demo",
        demo_workspace="demo-sales-workspace",
        database_min_pool_size=1,
        database_max_pool_size=2,
        worker_poll_seconds=0.01,
        worker_lock_seconds=90,
        worker_id="test-worker",
    )


class TokenTests(unittest.TestCase):
    def test_round_trip_preserves_server_scope(self) -> None:
        actor = ActorContext(
            workspace_id="00000000-0000-0000-0000-000000000001",
            user_id="02000000-0000-0000-0000-000000000002",
            role=RoleCode.SUPERVISOR,
            data_scope=DataScope.TEAM,
            team_ids=("01000000-0000-0000-0000-000000000002",),
        )
        service = TokenService(settings())
        issued = service.issue(actor, session_id="session-1")
        decoded, session_id = service.decode_access_token(issued.access_token)
        self.assertEqual(decoded, actor)
        self.assertEqual(session_id, "session-1")
        self.assertEqual(len(service.hash_refresh_token(issued.refresh_token)), 64)

    def test_rejects_token_signed_by_another_secret(self) -> None:
        actor = ActorContext(
            workspace_id="00000000-0000-0000-0000-000000000001",
            user_id="02000000-0000-0000-0000-000000000004",
            role=RoleCode.SALES,
            data_scope=DataScope.SELF,
        )
        token = TokenService(settings()).issue(actor, session_id="session-1").access_token
        other = replace(settings(), access_token_secret="another-secret-at-least-thirty-two-characters")
        with self.assertRaises(TokenError):
            TokenService(other).decode_access_token(token)


def test_console_binding_is_only_a_session_reference():
    from sales_backend.auth.tokens import SessionReference
    import pytest
    service = TokenService(settings())
    reference = SessionReference(
        '00000000-0000-0000-0000-000000000001',
        '02000000-0000-0000-0000-000000000004',
        '03000000-0000-0000-0000-000000000005',
    )
    binding = service.issue_console_binding(reference)
    assert service.decode_console_binding(binding) == reference
    with pytest.raises(TokenError):
        service.decode_access_token(binding)
    with pytest.raises(TokenError):
        service.identify_access_session_for_revocation(binding)
    with pytest.raises(TokenError):
        TokenService(replace(settings(), access_token_secret='another-independent-secret-32-characters')).decode_console_binding(binding)


def test_expired_access_can_only_identify_its_own_revocation_target():
    import pytest
    actor = ActorContext(
        workspace_id='00000000-0000-0000-0000-000000000001',
        user_id='02000000-0000-0000-0000-000000000004',
        role=RoleCode.SALES, data_scope=DataScope.SELF,
    )
    sid = '03000000-0000-0000-0000-000000000005'
    service = TokenService(settings())
    expired = TokenService(replace(settings(), access_token_minutes=-1)).issue(actor, session_id=sid)
    reference = service.identify_access_session_for_revocation(expired.access_token)
    assert (reference.workspace_id, reference.user_id, reference.session_id) == (actor.workspace_id, actor.user_id, sid)
    assert not hasattr(reference, 'role') and not hasattr(reference, 'data_scope')
    with pytest.raises(TokenError):
        service.decode_access_token(expired.access_token)
    with pytest.raises(TokenError):
        service.decode_console_binding(expired.access_token)
