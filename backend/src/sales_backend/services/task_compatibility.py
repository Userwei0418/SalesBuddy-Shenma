"""Mode is immutable after creation; check it before legacy mutation and replay."""

from sales_backend.domain.tasks import TaskConflict, TaskNotFound


async def require_legacy_mutation(connection, task_id, event_type):
    mode = await connection.fetchval(
        "SELECT execution_mode FROM workflow.task WHERE id=$1::uuid AND deleted_at IS NULL", task_id
    )
    if mode is None:
        raise TaskNotFound("TASK_NOT_FOUND")
    if mode in {"execution_v2", "execution_v3"} or (mode != "legacy" and event_type != "cancel"):
        raise TaskConflict("TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED")


async def require_execution_creation_enabled(connection, *, protocol_version=1):
    v3_available = await connection.fetchval("SELECT to_regprocedure('workflow.task_execution_initialize_v3(uuid,jsonb)') IS NOT NULL")
    if v3_available is True:
        if protocol_version != 3:
            raise TaskConflict("TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED")
        enabled = await connection.fetchval("""SELECT
            security.workspace_module_enabled(common.current_workspace_id(),'task')
            AND COALESCE(has_function_privilege(current_user,
              to_regprocedure('workflow.task_execution_initialize_v3(uuid,jsonb)'), 'EXECUTE'),false)""")
        if enabled is not True:
            raise TaskConflict("TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED")
        return
    if protocol_version == 3:
        raise TaskConflict("TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED")
    if protocol_version is None:
        return
    if protocol_version == 2:
        enabled = await connection.fetchval("""SELECT
            security.workspace_module_enabled(common.current_workspace_id(),'task')
            AND COALESCE(has_function_privilege(current_user,
              to_regprocedure('workflow.task_execution_initialize_v2(uuid,jsonb,boolean)'), 'EXECUTE'),false)""")
        if enabled is not True:
            raise TaskConflict("TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED")
        return
    enabled = await connection.fetchval(
        "SELECT security.workspace_module_enabled(common.current_workspace_id(),'task') "
        "AND security.workspace_module_enabled(common.current_workspace_id(),'task_execution_v1')"
    )
    if enabled is not True:
        raise TaskConflict("TASK_EXECUTION_ROLLOUT_DISABLED")
