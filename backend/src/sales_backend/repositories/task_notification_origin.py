"""Transaction-local notification context; caller determines the trusted origin."""

from contextlib import asynccontextmanager


@asynccontextmanager
async def task_group_notification_scope(connection, origin: str):
    previous = await connection.fetchval("SELECT current_setting('app.task_group_notification_origin',true)")
    await connection.execute("SELECT set_config('app.task_group_notification_origin',$1,true)", origin)
    try:
        yield
    except BaseException:
        # The outer transaction rolls the LOCAL setting back. Restoring it in an
        # aborted transaction would mask the original task-confirmation failure.
        raise
    else:
        await connection.execute("SELECT set_config('app.task_group_notification_origin',$1,true)", previous or "")
