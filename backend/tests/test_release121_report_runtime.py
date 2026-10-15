"""1.2.1 production merge contract: notes stay out of model inputs and schemas."""
from copy import deepcopy
import socket
from uuid import UUID
from unittest.mock import AsyncMock

import pytest

from sales_backend.contracts.work_report import GenerationInput, ReportContent, ReportItem
from sales_backend.domain.work_report import canonical_hash
from sales_backend.services import work_report_providers as adapter
from sales_backend.services.agent_platform.routing import InvalidAgentResult
from sales_backend.services.runtime_config import RuntimeConfiguration
from sales_backend.services.work_report_generation import service
from sales_backend.services.work_report_generation.content import model_output_schema, validate_model_content
from tests.test_work_report_contract import content_data, request_data
from tests.test_work_report_generation import answer
from tests.test_work_report_attempt_routing import four_batches, virtual_clock
from tests.test_work_report_providers import ACTOR, generation as generation


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("No network in production-merge contract tests")
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "create_connection", reject)


def envelope(request):
    return service._envelope(request, material=service._material(request), summaries=[],
                             phase="summarize", index=0, total=1, text_limit=64000)


def test_report_model_schema_stays_legacy_while_public_contract_can_retain_notes():
    # Fixed pre-notes public schema, also retained as the stricter model schema.
    assert canonical_hash(model_output_schema()) == "513b487a671180a9c592622325ed520f80d65719388335968d1f4d9756c768dd"
    assert "source_notes" in ReportContent.model_json_schema()["$defs"]["ReportItem"]["properties"]
    sent = envelope(GenerationInput(**request_data()))
    assert "source_notes" not in sent["output_contract"]["$defs"]["ReportItem"]["properties"]
    assert "ReportSourceNote" not in sent["output_contract"]["$defs"]
    assert "manual" not in sent["output_contract"]["$defs"]["ReportItem"]["properties"]["kind"]["enum"]


def test_rewrite_projection_keeps_frozen_revision_and_strips_notes_only_from_model_input():
    data, previous = request_data(), content_data()
    item = previous["sections"][0]["items"][0]
    note = dict(title="Frozen source", notice_text="Does not certify adjacent prose",
                text="Preserve exact frozen facts", source_refs=item["source_refs"])
    item["source_notes"] = [note]
    frozen = deepcopy(previous)
    data["rewrite"] = dict(document_id=UUID(int=70), base_revision_id=UUID(int=71),
                           base_document_version=1, section_ids=[previous["sections"][0]["id"]],
                           previous_content=previous)
    request = GenerationInput(**data)
    before = request.model_dump(mode="json")
    outgoing = envelope(request)
    sent = outgoing["input"]["rewrite"]["previous_content"]["sections"]
    assert len(sent) == 1 and "source_notes" not in sent[0]["items"][0]
    assert sent[0]["items"][0]["text"] == frozen["sections"][0]["items"][0]["text"]
    sent[0]["items"][0]["text"] = "A model-side mutation"
    assert request.model_dump(mode="json") == before
    assert previous == frozen
    assert request.rewrite.previous_content.sections[0].items[0].source_notes[0].model_dump(mode="json") == note


@pytest.mark.parametrize("value", [None, [], [{"title": "forged"}]])
def test_model_cannot_supply_source_note_key_even_when_empty(value):
    request = GenerationInput(**request_data())
    payload = answer(envelope(request))
    payload["sections"][0]["items"][0]["source_notes"] = value
    with pytest.raises(InvalidAgentResult):
        validate_model_content(payload, request, allowed_refs={s.source_id for s in request.sources})


@pytest.mark.parametrize("extension", [{}, {"source_notes": None}, {"source_notes": []}])
def test_old_report_item_wire_format_does_not_gain_null_notes(extension):
    old = dict(kind="summary", text="Existing report", source_refs=[], metric_refs=[])
    assert ReportItem(**old, **extension).model_dump(mode="json") == old


@pytest.mark.asyncio
async def test_production_entry_retains_progress_renewal_beyond_new_600_second_default(generation, monkeypatch):
    request, clock = four_batches(monkeypatch), virtual_clock(monkeypatch)
    settings, _, loader, client, audit = generation
    assert settings.work_report_inference_total_seconds == 600
    assert settings.work_report_inference_platform_seconds == 300
    loader.return_value = RuntimeConfiguration(settings, {})
    original = client.chat_json

    async def slow(**kwargs):
        clock[0] += 170
        return await original(**kwargs)

    client.chat_json = slow
    result = await adapter.generate_report(request, database=object(), actor=ACTOR,
                                           settings=settings, assert_active=AsyncMock())
    assert result.status == "generated" and result.trace.elapsed_ms == 850000
    assert result.trace.chunks_completed == result.trace.chunks_total == 5
    assert len(client.envelopes) == 5
    assert audit.finish.await_args.args == ("accepted",)
    assert (settings.agent_inference_total_seconds, settings.agent_inference_platform_seconds) == (45, 12)
