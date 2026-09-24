from sales_backend.main import app
from sales_backend.repositories.directory import DirectoryRepository
from sales_backend.repositories.profile import ProfileRepository


def test_profile_evaluation_route_is_registered() -> None:
    operation = app.openapi()["paths"]["/api/v1/profile/evaluation"]["get"]
    assert operation["operationId"] == "evaluation_summary_api_v1_profile_evaluation_get"
    assert operation["tags"] == ["Profile"]


def test_evaluation_member_rows_use_action_scopes() -> None:
    import inspect

    source = inspect.getsource(ProfileRepository.evaluation_summary)
    assert "profile.sales_read" in source and "authorization_subject" in source


def test_task_assignee_directory_uses_business_recipient_projection() -> None:
    import inspect

    source = inspect.getsource(DirectoryRepository.task_assignees)
    assert "TaskTargetRepository().recipients" in source and "business_only=True" in source
