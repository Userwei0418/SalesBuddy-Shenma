"""Typed frozen overview facts; no credentials or private dependency rows."""
import hashlib
import json
from typing import Literal

from pydantic import model_validator

from sales_backend.contracts.work_report_base import (
    PeriodSelection, ReportModel, ResolvedScope, ScopeSelection,
)
from sales_backend.contracts.work_report_meeting import MeetingFacts

GenerationProfile = Literal['general', 'sales_week_six']


class OverviewScopeSelection(ScopeSelection):
    kind: Literal['self', 'member', 'team'] = 'self'


class OverviewResolvedScope(ResolvedScope):
    kind: Literal['self', 'member', 'team'] = 'self'


class OverviewSelection(ReportModel):
    period: PeriodSelection
    scope: OverviewScopeSelection
    generation_profile: GenerationProfile = 'general'

    @model_validator(mode='after')
    def profile_period(self):
        if self.generation_profile == 'sales_week_six' and self.period.kind != 'week':
            raise ValueError('销售六节仅适用于周报')
        return self


class OverviewFacts(MeetingFacts):
    contract_version: Literal['work_report.overview_facts.v1'] = 'work_report.overview_facts.v1'
    scope: OverviewResolvedScope


def overview_reference(source):
    raw = json.dumps(source.model_dump(mode='json'), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return 'overview:' + hashlib.sha256(raw.encode()).hexdigest()


def overview_missing_reasons(value):
    if value is None:
        return []
    found = set()
    def visit(item):
        if isinstance(item, dict):
            found.update(item.get('missing_reasons', []))
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value.facts.model_dump(mode='json'))
    return sorted(found)


class OverviewGenerationFacts(ReportModel):
    facts: OverviewFacts
    references: dict[str, str]

    @model_validator(mode='after')
    def exact_frozen_references(self):
        sources = self.facts.sources
        expected = {s.source_id: overview_reference(s) for s in sources}
        if len(expected) != len(sources) or self.references != expected or len(set(expected.values())) != len(sources):
            raise ValueError('概览引用必须精确对应冻结来源')
        # Meeting display refs are private to that deterministic projection until
        # explicitly mapped into the generation reference space.
        def visit(value):
            if isinstance(value, dict):
                if 'source_refs' in value and not set(value['source_refs']) <= expected.keys():
                    raise ValueError('概览明细含未知来源')
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(self.facts.model_dump(mode='json'))
        return self
