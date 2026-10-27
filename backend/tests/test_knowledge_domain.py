from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from sales_backend.contracts.knowledge import (
    KnowledgeCatalog,
    KnowledgeContent,
    KnowledgeContributionInput,
    KnowledgeCount,
    KnowledgeCRMReference,
    KnowledgeMaterial,
    KnowledgeStoredFile,
)
from sales_backend.domain.knowledge import (
    CATEGORY_LABELS,
    SCOPE_LABELS,
    KnowledgeRuleError,
    activate_version,
    create_version,
    require_distinct_name,
    require_output_format,
    resolve_contribution,
    transition_version,
)

COMPANY, OTHER, USER, MATERIAL, VERSION, FILE, CUSTOMER, OPPORTUNITY = [UUID(int=i) for i in range(1, 9)]
NOW = datetime(2026, 10, 17, tzinfo=UTC)


def catalog(**updates):
    data = {
        "company_id": COMPANY, "revision": 2, "general_product_code": "general",
        "products": [{"code": "general", "label": "通用"}, {"code": "test_product", "label": "测试产品"}],
        "industries": [{"code": "test_industry", "label": "测试行业"}],
        "reporting_positions": [{"code": "test_position", "label": "测试岗位"}],
        "reporting_levels": [{"code": "test_level", "label": "测试职级"}],
        "decision_roles": [{"code": "test_role", "label": "测试决策角色"}],
    }
    return KnowledgeCatalog.model_validate(data | updates)


def selection(**updates):
    return KnowledgeContributionInput.model_validate({
        "expected_catalog_revision": 2, "content_category": "product_intro", "content_scope": "internal",
        "product_code": "general", "content": {"links": ["https://example.com/source"]},
    } | updates)


def customer_selection(**updates):
    return selection(**{
        "content_category": "customer_solution", "industry_code": "test_industry", "customer_id": CUSTOMER,
        "reporting_position_code": "test_position", "reporting_level_code": "test_level",
        "decision_role_code": "test_role",
    } | updates)


def reference(id_=CUSTOMER, kind="customer", **updates):
    return KnowledgeCRMReference.model_validate({
        "company_id": COMPANY, "id": id_, "kind": kind, "name": "标准客户名称",
    } | updates)


def resolve(data=None, **updates):
    return resolve_contribution(
        data or selection(), catalog(), **({"company_id": COMPANY, "attachable_files": {}} | updates),
    )


def file(**updates):
    return KnowledgeStoredFile.model_validate({
        "company_id": COMPANY, "file_id": FILE, "storage_provider": "postgres", "storage_key": "test-key",
        "filename": "测试.docx", "media_type": "application/octet-stream", "size_bytes": 42, "sha256": "a" * 64,
    } | updates)


def material(**updates):
    return KnowledgeMaterial.model_validate({"company_id": COMPANY, "material_id": MATERIAL} | updates)


def version(**updates):
    return create_version(material(), resolve(), **{
        "version_id": VERSION, "version_no": 1, "contributor_user_id": USER, "uploaded_at": NOW,
    } | updates)


def test_complete_customer_contribution_uses_resolved_labels_and_preserves_all_editable_values():
    data = customer_selection(
        product_code="test_product", content_scope="customer", opportunity_id=OPPORTUNITY,
        supplementary_text="  第一期  ", remarks="更新说明", content={"file_ids": [FILE]},
    )
    result = resolve(data, customer=reference(), opportunity=reference(OPPORTUNITY, "opportunity", name="标准商机"),
                     attachable_files={FILE: file()})
    assert result.content_name == "测试产品｜客户方案｜对客｜测试行业｜标准客户名称｜第一期"
    assert result.company_id == COMPANY
    assert result.customer_name == "标准客户名称" and result.opportunity_name == "标准商机"
    assert result.selection == data
    saved = create_version(material(), result, version_id=VERSION, version_no=1,
                           contributor_user_id=USER, uploaded_at=NOW)
    assert saved.uploaded_at == NOW and saved.contributor_user_id == USER
    assert saved.material_id == MATERIAL and saved.version_id == VERSION


@pytest.mark.parametrize("category,label", CATEGORY_LABELS.items())
def test_all_seven_categories_follow_naming_and_conditional_rules(category, label):
    if category == "customer_solution":
        result = resolve(customer_selection(), customer=reference())
    elif category == "industry_solution":
        result = resolve(selection(content_category=category, industry_code="test_industry"))
    else:
        result = resolve(selection(content_category=category))
    assert result.content_name.startswith(f"{label}｜仅内部")
    assert "通用" not in result.content_name


@pytest.mark.parametrize("scope,label", SCOPE_LABELS.items())
def test_business_audience_only_changes_name(scope, label):
    result = resolve(selection(content_scope=scope))
    assert result.content_name == f"产品介绍｜{label}"
    assert "acl" not in result.model_dump() and "grants" not in result.model_dump()


@pytest.mark.parametrize("field", [
    "industry_code", "customer_id", "reporting_position_code", "reporting_level_code", "decision_role_code",
])
def test_customer_solution_requires_every_conditional_field(field):
    with pytest.raises(ValidationError):
        customer_selection(**{field: None})


def test_industry_solution_requires_industry():
    with pytest.raises(ValidationError):
        selection(content_category="industry_solution")


@pytest.mark.parametrize("field,value", [
    ("industry_code", "test_industry"), ("customer_id", CUSTOMER), ("reporting_position_code", "test_position"),
    ("reporting_level_code", "test_level"), ("decision_role_code", "test_role"),
])
def test_category_change_must_clear_hidden_fields(field, value):
    with pytest.raises(ValidationError):
        selection(**{field: value})


@pytest.mark.parametrize("field,value", [
    ("content_name", "手填名称"), ("customer_name", "自由客户"), ("uploaded_at", NOW),
    ("contributor_user_id", USER), ("acl", {}), ("unknown", "x"),
])
def test_generated_and_unknown_input_fields_are_rejected(field, value):
    with pytest.raises(ValidationError):
        selection(**{field: value})


@pytest.mark.parametrize("field,value", [
    ("content_category", "自由输入"), ("content_scope", "所有用户"), ("product_code", "测试产品"),
    ("customer_id", "客户名称"), ("opportunity_id", "商机名称"), ("opportunity_id", UUID(int=0)),
    ("expected_catalog_revision", True), ("supplementary_text", "bad\x00name"),
])
def test_enum_codes_ids_revision_and_text_have_strict_input_shapes(field, value):
    with pytest.raises(ValidationError):
        selection(**{field: value})


@pytest.mark.parametrize("updates", [
    {"product_code": "nonexistent"}, {"industry_code": "nonexistent"},
    {"reporting_position_code": "nonexistent"}, {"reporting_level_code": "nonexistent"},
    {"decision_role_code": "nonexistent"},
])
def test_well_formed_but_unconfigured_codes_are_not_free_text_options(updates):
    with pytest.raises(KnowledgeRuleError) as error:
        resolve(customer_selection(**updates), customer=reference())
    assert error.value.code == "KNOWLEDGE_ENUM_INVALID"


def test_stale_catalog_and_foreign_catalog_are_rejected():
    with pytest.raises(KnowledgeRuleError, match="枚举已更新"):
        resolve(selection(expected_catalog_revision=1))
    with pytest.raises(KnowledgeRuleError, match="当前企业"):
        resolve_contribution(selection(), catalog(company_id=OTHER), company_id=COMPANY, attachable_files={})


def test_inactive_enum_and_duplicate_configuration_codes_are_rejected():
    with pytest.raises(ValidationError):
        catalog(products=[{"code": "general", "label": "通用", "active": False}])
    with pytest.raises(ValidationError):
        catalog(products=[{"code": "general", "label": "通用"}] * 2)
    configured = catalog(industries=[{"code": "test_industry", "label": "旧行业", "active": False}])
    with pytest.raises(KnowledgeRuleError, match="配置枚举"):
        resolve_contribution(customer_selection(), configured, company_id=COMPANY,
                             attachable_files={}, customer=reference())


@pytest.mark.parametrize("bad", [
    None, {"company_id": OTHER}, {"id": OTHER}, {"kind": "opportunity"}, {"active": False},
])
def test_crm_id_requires_matching_current_company_reference(bad):
    with pytest.raises(KnowledgeRuleError) as error:
        resolve(customer_selection(), customer=None if bad is None else reference(**bad))
    assert error.value.code == "KNOWLEDGE_REFERENCE_INVALID"


def test_optional_opportunity_is_checked_without_requiring_a_customer_solution():
    data = selection(opportunity_id=OPPORTUNITY)
    with pytest.raises(KnowledgeRuleError):
        resolve(data)
    good = resolve(data, opportunity=reference(OPPORTUNITY, "opportunity", name="已核商机"))
    assert good.content_name == "产品介绍｜仅内部"
    assert good.opportunity_name == "已核商机"
    with pytest.raises(KnowledgeRuleError):
        resolve(customer=reference())


@pytest.mark.parametrize("content", [
    {}, {"links": ["javascript:alert(1)"]}, {"links": ["file:///tmp/example.docx"]},
    {"links": ["https://user:password@example.com"]}, {"links": ["https://example.com"] * 2},
    {"file_ids": [str(FILE)] * 2}, {"file_ids": ["filename.docx"]},
])
def test_content_requires_real_unique_attachment_ids_or_http_links(content):
    with pytest.raises(ValidationError):
        KnowledgeContent.model_validate(content)


def test_link_only_attachment_only_and_both_are_supported_without_fetching():
    assert resolve().selection.content.links
    for content in ({"file_ids": [FILE]}, {"file_ids": [FILE], "links": ["https://example.com"]}):
        resolved = resolve(selection(content=content), attachable_files={FILE: file()})
        assert resolved.selection.content.file_ids == (FILE,)


@pytest.mark.parametrize("files", [{}, {FILE: file(company_id=OTHER)}, {FILE: file(file_id=OTHER)}])
def test_attachment_must_be_currently_attachable_and_from_same_company(files):
    with pytest.raises(KnowledgeRuleError) as error:
        resolve(selection(content={"file_ids": [FILE]}), attachable_files=files)
    assert error.value.code == "KNOWLEDGE_FILE_DENIED"


def test_duplicate_name_requires_explicit_update_or_supplement():
    name = resolve().content_name
    with pytest.raises(KnowledgeRuleError) as error:
        require_distinct_name(name, {MATERIAL: name})
    assert error.value.code == "KNOWLEDGE_NAME_CONFLICT"
    require_distinct_name(name, {MATERIAL: name}, updating_material_id=MATERIAL)
    with pytest.raises(KnowledgeRuleError):
        require_distinct_name(name, {MATERIAL: name, OTHER: name}, updating_material_id=MATERIAL)
    require_distinct_name(resolve(selection(supplementary_text="不同用途")).content_name, {MATERIAL: name})


def test_new_version_must_be_validated_before_activation_and_keeps_stable_ids():
    original = material(active_version_id=OTHER)
    new = version()
    assert new.state == "saved"
    for state in ("saved", "syncing", "parsing"):
        assert new.state == state
        with pytest.raises(KnowledgeRuleError, match="可检索"):
            activate_version(original, new)
        new = transition_version(new, {"saved": "syncing", "syncing": "parsing", "parsing": "retrievable"}[state])
    active = activate_version(original, new)
    assert active.active_version_id == VERSION and original.active_version_id == OTHER
    assert new.material_id == MATERIAL and new.version_id == VERSION
    assert active.material_id == original.material_id
    assert transition_version(new, "deleted").state == "deleted"


def test_failure_retry_does_not_activate_or_change_version_identity():
    new = transition_version(transition_version(version(), "syncing"), "failed")
    retried = transition_version(new, "syncing")
    assert retried.version_id == new.version_id
    with pytest.raises(KnowledgeRuleError):
        activate_version(material(), retried)
    with pytest.raises(KnowledgeRuleError):
        transition_version(version(), "retrievable")
    with pytest.raises(KnowledgeRuleError):
        transition_version(transition_version(version(), "deleted"), "syncing")


def test_versions_cannot_be_created_for_foreign_or_deleted_material_or_reuse_active_id():
    for target in (material(company_id=OTHER), material(deleted=True), material(active_version_id=VERSION)):
        with pytest.raises(KnowledgeRuleError):
            create_version(target, resolve(), version_id=VERSION, version_no=1,
                           contributor_user_id=USER, uploaded_at=NOW)
    with pytest.raises(ValidationError):
        version(uploaded_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        version(version_no=True)
    ready = transition_version(transition_version(transition_version(version(), "syncing"), "parsing"), "retrievable")
    for target in (material(company_id=OTHER), material(material_id=OTHER), material(deleted=True)):
        with pytest.raises(KnowledgeRuleError):
            activate_version(target, ready)


@pytest.mark.parametrize("status", ["unconfigured", "unavailable", "stale"])
def test_unknown_counts_never_turn_into_zero(status):
    assert KnowledgeCount(status=status, count=None).count is None
    with pytest.raises(ValidationError):
        KnowledgeCount(status=status, count=0)


def test_available_count_requires_provenance_and_accepts_real_zero():
    assert KnowledgeCount(status="available", count=0, source="jingsocial", observed_at=NOW).count == 0
    for data in ({"count": None}, {"count": 0}, {"count": True, "source": "jingsocial", "observed_at": NOW}):
        with pytest.raises(ValidationError):
            KnowledgeCount(status="available", **data)


def test_output_capabilities_are_explicit_and_docx_never_enables_pptx():
    require_output_format("docx", {"docx"})
    for output, verified in (("docx", set()), ("pptx", {"docx"}), ("pdf", {"pdf"})):
        with pytest.raises(KnowledgeRuleError):
            require_output_format(output, verified)


def test_file_location_is_not_business_identity():
    before = file()
    after = file(storage_provider="object_storage", storage_key="bucket/test")
    assert before.file_id == after.file_id and before.sha256 == after.sha256
    for bad in ({"storage_key": " "}, {"sha256": "unknown"}, {"size_bytes": 0}):
        with pytest.raises(ValidationError):
            file(**bad)


def test_loaded_version_cannot_claim_a_different_company_from_its_contribution():
    data = version().model_dump()
    with pytest.raises(ValidationError):
        type(version()).model_validate(data | {"company_id": OTHER})
