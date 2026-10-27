"""Fixed, idempotent database maintenance operations used by the worker scheduler."""


class MaintenanceRepository:
    QUERIES = {
        "ai_usage": "SELECT ops.evaluate_ai_usage_alerts()",
        "review_history": "SELECT ops.refresh_review_history()",
        "log_retention": "SELECT ops.prune_system_events()",
        "competency_schedule": "SELECT ops.enqueue_daily_sales_competency_reviews()",
        "priority_reminders": "SELECT ops.run_priority_reminder_monitor()",
        "task_coordination_checks": "SELECT workflow.task_execution_enqueue_coordination_checks(100)",
        "task_pause_rechecks": "SELECT workflow.task_execution_enqueue_pause_rechecks(100)",
    }

    async def run(self, connection, name: str) -> None:
        await connection.fetchval(self.QUERIES[name])
