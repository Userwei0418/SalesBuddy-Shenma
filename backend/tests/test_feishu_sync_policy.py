from dataclasses import replace
from uuid import UUID

import pytest
from pydantic import ValidationError

from sales_backend.domain.feishu_sync.config import ObjectKind, SyncConfig
from sales_backend.domain.feishu_sync.planning import SourceEvent, notification_plans

W, D1, D2 = UUID(int=1), UUID(int=11), UUID(int=12)


def payload():
    return {
        "workspace_id": str(W), "connection_id": str(UUID(int=2)), "revision": 1,
        "app_id": "cli_test", "credential_ref": str(UUID(int=3)), "base_token": "testBase", "base_url": "https://example.feishu.cn/base/testBase",
        "enabled": True,
        "mappings": {"customer": {"enabled": True, "table_id": "tblCustomers",
                                  "id_field_id": "fldSystemId",
                                  "fields": {"name": "fldName", "record_status": "fldStatus"}}},
        "notification": {"enabled": True, "default_chat_id": "oc_default"},
    }


def event(**changes):
    return replace(SourceEvent(UUID(int=4), W, ObjectKind.CUSTOMER, UUID(int=5), True), **changes)


def test_single_group_ignores_future_routes():
    data = payload()
    data["notification"]["routes"] = [{"department_id": str(D1), "chat_ids": ["oc_region"]}]
    assert [p.chat_id for p in notification_plans(SyncConfig.model_validate(data), event(department_ids=(D1,)))] == [
        "oc_default"]


def test_department_dedup_and_fallback():
    data = payload()
    data["notification"].update(routing_mode="department_routes", routes=[
        {"department_id": str(D1), "chat_ids": ["oc_shared", "oc_shared"]},
        {"department_id": str(D2), "chat_ids": ["oc_shared", "oc_other"]},
    ])
    config = SyncConfig.model_validate(data)
    plans = notification_plans(config, event(department_ids=(D1, D2, UUID(int=99))))
    assert [p.chat_id for p in plans] == ["oc_default", "oc_other", "oc_shared"]
    assert len({p.dedupe_key for p in plans}) == 3
    assert notification_plans(config, event())[0].chat_id == "oc_default"


@pytest.mark.parametrize("changes", [{"historical": True}, {"first_formal_create": False}, {"production": False}])
def test_non_new_business_never_notifies(changes):
    assert not notification_plans(SyncConfig.model_validate(payload()), event(**changes))


def test_cross_company_rejected_even_when_disabled():
    data = payload()
    data["enabled"] = False
    with pytest.raises(PermissionError):
        notification_plans(SyncConfig.model_validate(data), event(workspace_id=UUID(int=90)))


def test_disable_notifications():
    data = payload()
    data["notification"]["enabled"] = False
    assert not notification_plans(SyncConfig.model_validate(data), event())


def test_identity_stable_across_config_revision():
    data = payload()
    first = notification_plans(SyncConfig.model_validate(data), event())[0]
    data["revision"] = 2
    assert notification_plans(SyncConfig.model_validate(data), event())[0].dedupe_key == first.dedupe_key
    data["connection_id"] = str(UUID(int=20))
    assert notification_plans(SyncConfig.model_validate(data), event())[0].dedupe_key != first.dedupe_key


@pytest.mark.parametrize("field", ["password", "phone", "select * from platform.user_ref", "raw_database_query"])
def test_reject_unsafe_or_unscoped_source(field):
    data = payload()
    data["mappings"]["customer"]["fields"][field] = "fldUnsafe"
    with pytest.raises(ValidationError):
        SyncConfig.model_validate(data)


@pytest.mark.parametrize("field,value", [("app_secret", "never-store"), ("base_url", "https://evil.invalid"),
                                         ("direction", "base_to_system")])
def test_no_inline_secret_endpoint_or_unknown_sync_direction(field, value):
    data = payload()
    data[field] = value
    with pytest.raises(ValidationError):
        SyncConfig.model_validate(data)


def test_direction_defaults_to_one_way_and_preserves_reserved_draft():
    data = payload()
    assert SyncConfig.model_validate(data).direction == "system_to_base"
    data.update(direction="bidirectional", enabled=False)
    config = SyncConfig.model_validate(data)
    assert config.direction == "bidirectional"
    assert config.enabled is False


def test_unique_targets_and_required_lifecycle():
    data = payload()
    data["mappings"]["customer"]["fields"]["name"] = "fldSystemId"
    with pytest.raises(ValidationError):
        SyncConfig.model_validate(data)
    data = payload()
    del data["mappings"]["customer"]["fields"]["record_status"]
    with pytest.raises(ValidationError):
        SyncConfig.model_validate(data)


def test_only_approved_notifications_and_default_group_required():
    data = payload()
    data["notification"]["on_create"] = ["task"]
    with pytest.raises(ValidationError):
        SyncConfig.model_validate(data)
    data = payload()
    del data["notification"]["default_chat_id"]
    with pytest.raises(ValidationError):
        SyncConfig.model_validate(data)


def test_repeated_formal_event_for_same_object_has_one_notification_identity():
    config = SyncConfig.model_validate(payload())
    first = notification_plans(config, event())[0]
    repeated = notification_plans(config, event(event_id=UUID(int=999)))[0]
    assert first.dedupe_key == repeated.dedupe_key
    other = notification_plans(config, event(object_id=UUID(int=1000)))[0]
    assert first.dedupe_key != other.dedupe_key
