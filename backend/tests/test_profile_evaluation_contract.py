from sales_backend.main import app
from sales_backend.repositories.directory import DirectoryRepository
from sales_backend.repositories.profile import ProfileRepository


def test_profile_evaluation_route_is_registered() -> None:
    operation = app.openapi()["paths"]["/api/v1/profile/evaluation"]["get"]
    assert operation["operationId"] == "evaluation_summary_api_v1_profile_evaluation_get"
    assert operation["tags"] == ["Profile"]


def test_evaluation_member_rows_are_role_scoped() -> None:
    import inspect

    source = inspect.getsource(ProfileRepository.evaluation_summary)
    assert "$2 = 'supervisor' AND tm.team_id = ANY($3::uuid[])" in source
    assert 'if actor.role.value == "manager"' in source
    assert 'if actor.role.value in {"supervisor", "manager"}' in source


def test_task_assignee_directory_is_role_scoped() -> None:
    import inspect

    source = inspect.getsource(DirectoryRepository.task_assignees)
    assert "$2 = 'supervisor' AND rb.role_code = 'sales'" in source
    assert "$2 = 'sales' AND rb.role_code = 'sales'" in source
    assert "manager" in source
