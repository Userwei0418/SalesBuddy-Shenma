from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from sales_backend.contracts.knowledge import (
    KnowledgeChunk,
    KnowledgeCitation,
    KnowledgeContribution,
    KnowledgeContributionInput,
    KnowledgeDocumentBinding,
    KnowledgeMaterial,
    KnowledgeMaterialVersion,
    KnowledgeRetrievalRequest,
    KnowledgeStoredFile,
    KnowledgeVersionRef,
)
from sales_backend.domain.knowledge import KnowledgeRuleError, build_generation_request
from sales_backend.domain.knowledge_access import (
    KnowledgeAccessEntry,
    KnowledgeAccessError,
    KnowledgeACL,
    KnowledgeACLGrant,
    KnowledgeActor,
    allows,
    freeze_read_scope,
    require_access,
    require_file_access,
    revalidate_scope,
    validate_chunks,
    validate_citations,
)

COMPANY, OTHER_COMPANY, OWNER, USER, TEAM, MATERIAL, VERSION, OTHER_VERSION = [UUID(int=i) for i in range(1, 9)]
NOW = datetime(2026, 10, 17, tzinfo=UTC)


def actor(**updates):
    return KnowledgeActor.model_validate({
        "company_id": COMPANY, "user_id": USER, "team_ids": [TEAM], "authorization_revision": 1,
        "active": True, "module_enabled": True,
        "permitted_actions": ["read", "edit", "manage"],
    } | updates)


def grant(kind="user", subject_id=USER, actions=("read",)):
    return KnowledgeACLGrant(subject_kind=kind, subject_id=subject_id, actions=actions)


def acl(**updates):
    return KnowledgeACL.model_validate({
        "company_id": COMPANY, "material_id": MATERIAL, "owner_user_id": OWNER, "revision": 1,
        "grants": [grant()],
    } | updates)


def entry(*, acl_updates=None, material_updates=None, version_updates=None, content_scope="internal"):
    selection = KnowledgeContributionInput(
        expected_catalog_revision=1, content_category="product_intro", content_scope=content_scope,
        product_code="general", content={"links": ["https://example.com"]},
    )
    contribution = KnowledgeContribution(company_id=COMPANY, selection=selection, content_name="test")
    material = KnowledgeMaterial.model_validate({
        "company_id": COMPANY, "material_id": MATERIAL, "active_version_id": VERSION,
    } | (material_updates or {}))
    version = KnowledgeMaterialVersion.model_validate({
        "company_id": COMPANY, "material_id": MATERIAL, "version_id": VERSION, "version_no": 1,
        "state": "retrievable", "contribution": contribution, "uploaded_at": NOW, "contributor_user_id": OWNER,
    } | (version_updates or {}))
    return KnowledgeAccessEntry(material=material, version=version, acl=acl(**(acl_updates or {})))


def scope(entries=None, who=None):
    return freeze_read_scope(who or actor(), [entry()] if entries is None else entries, observed_at=NOW)


def binding(source=None, **updates):
    return KnowledgeDocumentBinding.model_validate({
        "company_id": COMPANY, "source": source or KnowledgeVersionRef(material_id=MATERIAL, version_id=VERSION),
        "provider_document_id": "doc-a",
    } | updates)


def request(**updates):
    return KnowledgeRetrievalRequest.model_validate({"scope": scope(), "documents": [binding()], "query": "测试问题"}
                                                   | updates)


def chunk(**updates):
    return KnowledgeChunk.model_validate({
        "source": {"material_id": MATERIAL, "version_id": VERSION}, "provider_document_id": "doc-a",
        "chunk_id": "chunk-a", "text": "授权原文", "page": 1,
    } | updates)


@pytest.mark.parametrize("kind,subject", [("user", USER), ("team", TEAM), ("company", None)])
def test_individual_team_and_company_grants_are_action_specific(kind, subject):
    permissions = acl(grants=[grant(kind, subject)])
    assert allows(actor(), permissions, "read")
    assert not allows(actor(), permissions, "edit")
    assert not allows(actor(), permissions, "manage")


def test_grants_do_not_multiply_actions_across_subjects():
    permissions = acl(grants=[grant(), grant("user", OWNER, ("manage", "edit"))])
    assert allows(actor(), permissions, "read")
    assert not allows(actor(), permissions, "manage")
    assert not allows(actor(team_ids=[]), acl(grants=[grant("team", TEAM)]), "read")


@pytest.mark.parametrize("overrides", [
    {"company_id": OTHER_COMPANY}, {"active": False}, {"module_enabled": False},
    {"permitted_actions": []}, {"denied_actions": ["read"]},
])
def test_account_and_function_guards_override_owner_and_grants(overrides):
    for user in (USER, OWNER):
        who = actor(**({"user_id": user} | overrides))
        assert not allows(who, acl(), "read")
        with pytest.raises(KnowledgeAccessError):
            require_access(who, entry(), "read")


def test_owner_capabilities_still_respect_explicit_denials():
    assert allows(actor(user_id=OWNER), acl(), "manage")
    assert not allows(actor(user_id=OWNER), acl(denied_user_ids=[OWNER]), "manage")
    assert not allows(actor(), acl(denied_user_ids=[USER]), "read")
    assert not allows(actor(), acl(grants=[]), "manage")


def test_missing_or_unverified_account_and_module_status_cannot_default_to_enabled():
    for field in ("active", "module_enabled"):
        data = actor().model_dump()
        del data[field]
        with pytest.raises(ValidationError):
            KnowledgeActor.model_validate(data)
        with pytest.raises(ValidationError):
            KnowledgeActor.model_validate(data | {field: "true"})


@pytest.mark.parametrize("scope_name", ["internal", "customer", "partner", "public"])
def test_business_audience_never_grants_system_access(scope_name):
    with pytest.raises(KnowledgeAccessError):
        freeze_read_scope(actor(), [entry(acl_updates={"grants": []}, content_scope=scope_name)], observed_at=NOW)


@pytest.mark.parametrize("bad", [
    {"subject_kind": "user", "actions": ["read"]},
    {"subject_kind": "company", "subject_id": USER, "actions": ["read"]},
    {"subject_kind": "user", "subject_id": USER, "actions": ["admin"]},
    {"subject_kind": "user", "subject_id": USER, "actions": []},
])
def test_invalid_acl_shapes_fail_closed(bad):
    with pytest.raises(ValidationError):
        KnowledgeACLGrant.model_validate(bad)


def test_cross_material_or_company_snapshot_cannot_be_assembled():
    for updates in ({"company_id": OTHER_COMPANY}, {"material_id": OTHER_VERSION}):
        with pytest.raises(ValidationError):
            entry(acl_updates=updates)


@pytest.mark.parametrize("changes", [
    {"material_updates": {"deleted": True}}, {"version_updates": {"state": "deleted"}},
    {"version_updates": {"state": "parsing"}}, {"material_updates": {"active_version_id": OTHER_VERSION}},
])
def test_retrieval_requires_live_current_retrievable_version(changes):
    with pytest.raises(KnowledgeAccessError):
        scope([entry(**changes)])


def test_empty_allowlist_cannot_form_provider_request_even_with_document_fallback():
    empty = scope([])
    assert empty.versions == ()
    for documents in ([], [binding()]):
        with pytest.raises(ValidationError, match="空允许集合"):
            request(scope=empty, documents=documents)


def test_all_selected_materials_must_be_authorized_instead_of_silently_dropped():
    with pytest.raises(KnowledgeAccessError):
        scope([entry(), entry(acl_updates={"grants": []})])
    with pytest.raises(ValidationError):
        scope([entry(), entry()])


@pytest.mark.parametrize("documents", [
    [], [binding(), binding()], [binding(company_id=OTHER_COMPANY)],
    [binding(KnowledgeVersionRef(material_id=MATERIAL, version_id=OTHER_VERSION))],
])
def test_provider_bindings_must_match_exact_allowed_versions(documents):
    with pytest.raises(ValidationError):
        request(documents=documents)


@pytest.mark.parametrize("overrides", [
    {"provider_document_id": "doc-unknown"},
    {"source": {"material_id": MATERIAL, "version_id": OTHER_VERSION}},
    {"source": {"material_id": OTHER_VERSION, "version_id": VERSION}},
])
def test_provider_result_outside_allowlist_is_rejected_before_model_input(overrides):
    with pytest.raises(KnowledgeAccessError) as error:
        validate_chunks(request(), [chunk(), chunk(**overrides)])
    assert error.value.code == "KNOWLEDGE_REFERENCE_OUTSIDE_SCOPE"


def test_valid_chunks_pass_and_duplicate_chunk_identity_does_not():
    good = chunk()
    assert validate_chunks(request(), [good]) == (good,)
    assert validate_chunks(request(), []) == ()
    with pytest.raises(KnowledgeAccessError):
        validate_chunks(request(), [good, chunk(text="不同内容但冒用相同ID")])


def test_answer_citations_must_point_to_this_runs_actual_validated_chunks():
    good = KnowledgeCitation(source=chunk().source, provider_document_id="doc-a", chunk_id="chunk-a")
    assert validate_citations(request(), [chunk()], [good]) == (good,)
    for bad in (good.model_copy(update={"chunk_id": "invented"}),
                good.model_copy(update={"provider_document_id": "other"})):
        with pytest.raises(KnowledgeAccessError):
            validate_citations(request(), [chunk()], [bad])
    with pytest.raises(KnowledgeAccessError):
        validate_citations(request(), [], [good])


def test_frozen_scope_cannot_authorize_revoked_access():
    frozen = scope()
    assert revalidate_scope(frozen, actor(), [entry()], observed_at=NOW).purpose == "delivery"
    for current in (entry(acl_updates={"grants": [], "revision": 2}), entry(material_updates={"deleted": True})):
        with pytest.raises(KnowledgeAccessError):
            revalidate_scope(frozen, actor(), [current], observed_at=NOW)
    with pytest.raises(KnowledgeAccessError):
        revalidate_scope(frozen, actor(active=False), [entry()], observed_at=NOW)


@pytest.mark.parametrize("who,entries", [
    (actor(user_id=OWNER), [entry()]),
    (actor(), []),
    (actor(), [entry(material_updates={"active_version_id": OTHER_VERSION},
                     version_updates={"version_id": OTHER_VERSION})]),
])
def test_frozen_scope_does_not_authorize_changed_identity_or_substituted_sources(who, entries):
    with pytest.raises(KnowledgeAccessError) as error:
        revalidate_scope(scope(), who, entries, observed_at=NOW)
    assert error.value.code == "KNOWLEDGE_SCOPE_CHANGED"


def test_old_work_remains_downloadable_after_new_source_version_is_activated():
    frozen = scope()
    historical = entry(material_updates={"active_version_id": OTHER_VERSION})
    checked = revalidate_scope(frozen, actor(), [historical], observed_at=NOW + timedelta(days=1))
    assert checked.versions == frozen.versions and checked.purpose == "delivery"
    assert frozen.purpose == "retrieval"  # Original audit is not overwritten.
    with pytest.raises(KnowledgeAccessError):
        freeze_read_scope(actor(), [historical], observed_at=NOW)
    with pytest.raises(ValidationError, match="不能用于新检索"):
        request(scope=checked)
    with pytest.raises(ValidationError, match="不能用于新生成"):
        type(build_generation_request(request(), (chunk(),), output_format="docx", verified_formats={"docx"},
                                      instructions="生成"))(
            scope=checked, output_format="docx", instructions="重新生成", sources=(chunk(),),
        )


def test_unrelated_grant_and_authorization_revision_changes_require_new_judgment_not_permanent_denial():
    frozen = scope()
    current = entry(acl_updates={"revision": 2, "grants": [grant(), grant("user", OWNER, ("read", "edit"))]})
    checked = revalidate_scope(frozen, actor(authorization_revision=3), [current], observed_at=NOW)
    assert checked.authorization_revision == 3 and checked.versions[0].acl_revision == 2
    assert frozen.authorization_revision == 1 and frozen.versions[0].acl_revision == 1


@pytest.mark.parametrize("changes", [
    {"acl_updates": {"revision": 2, "grants": []}},
    {"acl_updates": {"revision": 2, "denied_user_ids": [USER]}},
    {"version_updates": {"state": "deleted"}},
    {"material_updates": {"deleted": True}},
])
def test_historical_delivery_still_rejects_actual_revocation_or_deleted_source(changes):
    with pytest.raises(KnowledgeAccessError):
        revalidate_scope(scope(), actor(), [entry(**changes)], observed_at=NOW)


def test_historical_delivery_rejects_cross_tenant_and_disabled_module():
    for who in (actor(company_id=OTHER_COMPANY), actor(module_enabled=False)):
        with pytest.raises(KnowledgeAccessError):
            revalidate_scope(scope(), who, [entry()], observed_at=NOW)


def test_generation_builder_enforces_capabilities_and_verified_retrieval_evidence():
    built = build_generation_request(request(), (chunk(),), output_format="docx", verified_formats={"docx"},
                                     instructions="按原文生成")
    assert built.scope == scope() and built.sources == (chunk(),)
    with pytest.raises(KnowledgeRuleError):
        build_generation_request(request(), (chunk(),), output_format="pptx", verified_formats={"docx"},
                                 instructions="生成")
    with pytest.raises(KnowledgeAccessError):
        build_generation_request(request(), (chunk(provider_document_id="other"),), output_format="docx",
                                 verified_formats={"docx"}, instructions="生成")
    with pytest.raises(ValidationError):
        build_generation_request(request(), (), output_format="docx", verified_formats={"docx"}, instructions="生成")


def test_scope_revalidation_rejects_older_observation():
    with pytest.raises(KnowledgeAccessError, match="权限已变化"):
        revalidate_scope(scope(), actor(), [entry()], observed_at=NOW - timedelta(seconds=1))


def test_download_checks_current_permissions_file_company_and_exact_version_membership():
    file_id = UUID(int=100)
    current = entry()
    data = current.version.model_dump()
    data["contribution"]["selection"]["content"] = {"file_ids": [file_id]}
    current = KnowledgeAccessEntry(
        material=current.material, version=KnowledgeMaterialVersion.model_validate(data), acl=current.acl,
    )
    stored = KnowledgeStoredFile(
        company_id=COMPANY, file_id=file_id, storage_provider="postgres", storage_key="test",
        filename="测试.docx", media_type="application/octet-stream", size_bytes=1, sha256="a" * 64,
    )
    require_file_access(actor(), current, stored)
    for bad in (stored.model_copy(update={"company_id": OTHER_COMPANY}),
                stored.model_copy(update={"file_id": OTHER_VERSION})):
        with pytest.raises(KnowledgeAccessError):
            require_file_access(actor(), current, bad)
    with pytest.raises(KnowledgeAccessError):
        require_file_access(actor(active=False), current, stored)
    with pytest.raises(KnowledgeAccessError):
        require_file_access(actor(), entry(), stored)
