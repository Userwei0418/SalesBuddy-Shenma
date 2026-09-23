from __future__ import annotations

import json
import unittest
from dataclasses import replace

import httpx

from sales_backend.config import Settings
from sales_backend.domain.agent import ChatMessage
from sales_backend.integrations.circuit_breaker import CircuitState, GatewayCircuitBreaker
from sales_backend.integrations.senseaudio import (
    MAX_ASR_FILE_BYTES,
    SenseAudioClient,
    SenseAudioError,
    normalize_audio_upload,
)


def settings() -> Settings:
    return Settings(
        app_env="test",
        database_url="postgresql://test",
        senseaudio_base_url="https://api.senseaudio.cn",
        senseaudio_api_key="test-only-not-a-real-secret",
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


class SenseAudioTests(unittest.IsolatedAsyncioTestCase):
    def test_wechat_octet_stream_mp3_is_normalized(self) -> None:
        filename, mime_type = normalize_audio_upload(
            "tmp_recording",
            "application/octet-stream",
            b"ID3\x04\x00\x00audio",
        )
        self.assertEqual(filename, "tmp_recording.mp3")
        self.assertEqual(mime_type, "audio/mpeg")

    def test_wav_signature_overrides_generic_upload_type(self) -> None:
        filename, mime_type = normalize_audio_upload(
            "recording.bin",
            "application/octet-stream",
            b"RIFF\x00\x00\x00\x00WAVEdata",
        )
        self.assertEqual(filename, "recording.bin.wav")
        self.assertEqual(mime_type, "audio/wav")

    async def test_asr_uses_lite_model_without_exposing_key(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            body = await request.aread()
            self.assertIn(b"senseaudio-asr-lite-1.5-260319", body)
            self.assertEqual(request.headers["Authorization"], "Bearer test-only-not-a-real-secret")
            return httpx.Response(200, json={"text": "拜访记录"}, headers={"x-trace-id": "t1"})

        http_client = httpx.AsyncClient(
            base_url="https://api.senseaudio.cn",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer test-only-not-a-real-secret"},
        )
        client = SenseAudioClient(settings(), client=http_client)
        result = await client.transcribe(filename="visit.mp3", content=b"audio", mime_type="audio/mpeg")
        self.assertEqual(result.text, "拜访记录")
        self.assertEqual(result.trace_id, "t1")
        await http_client.aclose()

    async def test_chat_requests_glm_json_object(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads((await request.aread()).decode())
            self.assertEqual(payload["model"], "senseaudio-s2-lite")
            self.assertEqual(payload["response_format"], {"type": "json_object"})
            self.assertNotIn("max_tokens", payload)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"metric_codes":["visit_count"]}'}}]},
            )

        http_client = httpx.AsyncClient(base_url="https://api.senseaudio.cn", transport=httpx.MockTransport(handler))
        client = SenseAudioClient(settings(), client=http_client)
        result = await client.chat_json(messages=[ChatMessage(role="user", content="今天拜访多少")])
        self.assertEqual(result["metric_codes"], ["visit_count"])
        await http_client.aclose()

    async def test_asr_rejects_file_over_official_limit_before_network(self) -> None:
        http_client = httpx.AsyncClient(base_url="https://api.senseaudio.cn")
        client = SenseAudioClient(settings(), client=http_client)
        with self.assertRaisesRegex(ValueError, "10 MB"):
            await client.transcribe(
                filename="too-large.mp3",
                content=b"x" * (MAX_ASR_FILE_BYTES + 1),
                mime_type="audio/mpeg",
            )
        await http_client.aclose()

    async def test_tts_accepts_current_hex_audio_payload(self) -> None:
        expected = b"ID3\x04\x00\x00"

        async def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads((await request.aread()).decode())
            self.assertEqual(payload["model"], "senseaudio-tts-1.5-260319")
            return httpx.Response(
                200,
                json={
                    "data": {"audio": expected.hex()},
                    "extra_info": {"audio_format": "mp3", "audio_sample_rate": 32000},
                    "trace_id": "tts-hex",
                },
            )

        http_client = httpx.AsyncClient(base_url="https://api.senseaudio.cn", transport=httpx.MockTransport(handler))
        client = SenseAudioClient(settings(), client=http_client)
        result = await client.synthesize(text="查询客户", voice_id="female_0033_b")
        self.assertEqual(result.audio_bytes, expected)
        self.assertEqual(result.trace_id, "tts-hex")
        await http_client.aclose()

    async def test_tts_keeps_base64_compatibility(self) -> None:
        import base64

        expected = b"not-hex-audio"

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"audio": base64.b64encode(expected).decode()}})

        http_client = httpx.AsyncClient(base_url="https://api.senseaudio.cn", transport=httpx.MockTransport(handler))
        client = SenseAudioClient(settings(), client=http_client)
        result = await client.synthesize(text="查询客户", voice_id="female_0033_b")
        self.assertEqual(result.audio_bytes, expected)
        await http_client.aclose()

    async def test_chat_retries_busy_then_succeeds(self) -> None:
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, json={"message": "服务繁忙，请稍后再试"})
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"ok": true}'}}]},
            )

        http_client = httpx.AsyncClient(
            base_url="https://api.senseaudio.cn",
            transport=httpx.MockTransport(handler),
        )
        cfg = replace(settings(), max_retries=2)
        breaker = GatewayCircuitBreaker(failure_threshold=20, recovery_seconds=60)
        client = SenseAudioClient(cfg, client=http_client, breaker=breaker)
        result = await client.chat_json(messages=[ChatMessage(role="user", content="客户数")])
        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls["n"], 2)
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        await http_client.aclose()

    async def test_invalid_json_content_is_retried(self) -> None:
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"title":"ok"}'}}]},
            )

        http_client = httpx.AsyncClient(
            base_url="https://api.senseaudio.cn",
            transport=httpx.MockTransport(handler),
        )
        cfg = replace(settings(), max_retries=1)
        breaker = GatewayCircuitBreaker(failure_threshold=20, recovery_seconds=60)
        client = SenseAudioClient(cfg, client=http_client, breaker=breaker)
        result = await client.chat_json(messages=[ChatMessage(role="user", content="客户数")])
        self.assertEqual(result["title"], "ok")
        self.assertEqual(calls["n"], 2)
        await http_client.aclose()

    async def test_circuit_opens_and_fails_fast(self) -> None:
        calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503, json={"message": "服务繁忙，请稍后再试"})

        http_client = httpx.AsyncClient(
            base_url="https://api.senseaudio.cn",
            transport=httpx.MockTransport(handler),
        )
        cfg = replace(settings(), max_retries=0)
        breaker = GatewayCircuitBreaker(failure_threshold=2, recovery_seconds=60)
        client = SenseAudioClient(cfg, client=http_client, breaker=breaker)
        with self.assertRaises(SenseAudioError):
            await client.chat_json(messages=[ChatMessage(role="user", content="客户数")])
        with self.assertRaises(SenseAudioError):
            await client.chat_json(messages=[ChatMessage(role="user", content="客户数")])
        self.assertEqual(breaker.state, CircuitState.OPEN)
        with self.assertRaisesRegex(SenseAudioError, "circuit open"):
            await client.chat_json(messages=[ChatMessage(role="user", content="客户数")])
        self.assertEqual(calls["n"], 2)
        await http_client.aclose()

    async def test_malformed_http_envelope_retries_with_bounded_attempt_accounting(self) -> None:
        calls, observed = [], []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(200, content=b'{"choices":[')
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"score":75}'}}]})

        class Observer:
            async def start(self, endpoint, model, metadata, attempt):
                return attempt

            async def finish(self, invocation_id, response, error):
                observed.append((invocation_id, response.status_code, type(error).__name__ if error else None))

        async with httpx.AsyncClient(
            base_url="https://original.invalid", transport=httpx.MockTransport(handler),
        ) as http:
            client = SenseAudioClient(replace(settings(), max_retries=1), client=http,
                                      breaker=GatewayCircuitBreaker(failure_threshold=20, recovery_seconds=60),
                                      observer=Observer())
            result = await client.chat_json(messages=[ChatMessage(role="user", content="score")])
        self.assertEqual(result, {"score": 75})
        self.assertEqual(len(calls), 2)
        self.assertEqual(observed, [(1, 200, "SenseAudioError"), (2, 200, None)])

    async def test_persistently_malformed_http_envelope_stops_at_configured_limit(self) -> None:
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, content=b'{"choices":[')

        async with httpx.AsyncClient(
            base_url="https://original.invalid", transport=httpx.MockTransport(handler),
        ) as http:
            client = SenseAudioClient(replace(settings(), max_retries=1), client=http,
                                      breaker=GatewayCircuitBreaker(failure_threshold=20, recovery_seconds=60))
            with self.assertRaisesRegex(SenseAudioError, "envelope was not valid JSON"):
                await client.chat_json(messages=[ChatMessage(role="user", content="score")])
        self.assertEqual(len(calls), 2)
