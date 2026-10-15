"""A meeting produces a reviewable draft; only a human confirmation creates tasks."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime

from sales_backend.contracts.task_imports import TaskImportDraftItem, TaskImportPreview
from sales_backend.domain.agent import ChatMessage
from sales_backend.domain.concurrency import require_version
from sales_backend.domain.task_imports import (
    PROMPT,
    ExtractedItems,
    ImportLimitError,
    candidate_lines,
    confirmation_hash,
    materialize_items,
    similar,
    source_hash,
    suggested_due,
)
from sales_backend.domain.tasks import TaskConflict, TaskNotFound, task_association_kind
from sales_backend.repositories.task_imports import TaskImportRepository
from sales_backend.repositories.task_targets import TaskTargetRepository
from sales_backend.services.agent_business_rules import prepare_business_rules, supplementary_prompt
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService
from sales_backend.services.agent_platform.pilot import task_import_pilot_policy
from sales_backend.services.capabilities import require_capability
from sales_backend.services.runtime_config import load_runtime_configuration
from sales_backend.services.tasks import TaskService


def counters(tasks):
    now = datetime.now(UTC)
    completed = sum(t["status"] == "completed" for t in tasks)
    cancelled = sum(t["status"] == "cancelled" for t in tasks)
    denominator = len(tasks) - cancelled

    def overdue(task):
        due = task.get("due_at")
        if isinstance(due, str):
            due = datetime.fromisoformat(due.replace("Z", "+00:00"))
        return bool(due and due < now and task["status"] not in {"completed", "cancelled"})

    return {
        "visible_task_count": len(tasks),
        "task_count": len(tasks),
        "completed": completed,
        "pending_review": sum(t["status"] == "pending_review" for t in tasks),
        "cancelled": cancelled,
        "active": denominator - completed,
        "overdue": sum(overdue(t) for t in tasks),
        "completion_rate": round(completed / denominator, 4) if denominator else None,
    }


def editable(items):
    keys = TaskImportDraftItem.model_fields.keys()
    return [
        {key: value for key, value in item.items() if key in keys} | {"selected": True, "duplicate_acknowledged": False}
        for item in items
    ]


async def annotate_duplicates(connection, items):
    existing = await TaskImportRepository().existing_tasks(connection)
    result = []
    for item in items:
        matches = []
        account = (item.get("assignee_account_code") or "").strip().casefold()
        for other in items:
            if (
                other["item_id"] != item["item_id"]
                and (other.get("assignee_account_code") or "").strip().casefold() == account
                and similar(item["description"], other["description"])
            ):
                matches.append({"item_id": other["item_id"], "description": other["description"]})
        for task in existing:
            if (
                account
                and (task["account_code"] or "").casefold() == account
                and similar(item["description"], task["description"])
            ):
                matches.append(
                    {"task_id": task["id"], "description": task["description"], "assignee_name": task["display_name"]}
                )
        result.append({**item, "duplicates": matches})
    return result


def source_date_warnings(item, source, meeting_date):
    if not source or not meeting_date:
        return []
    if isinstance(meeting_date, str):
        meeting_date = date.fromisoformat(meeting_date)
    suggested, warning = suggested_due(source.get("source_text", ""), meeting_date)
    current = item.get("due_at")
    if not current:
        if suggested:
            # Older drafts may lack the newly recognized suggestion. Explain it
            # without silently replacing a saved draft field.
            day = datetime.fromisoformat(suggested).date().isoformat()
            return [f"原文日期可识别为北京时间{day}，请核对并补充截止时间"]
        return [warning] if warning else []
    if not suggested or not warning:
        return []
    current = datetime.fromisoformat(current.replace("Z", "+00:00")) if isinstance(current, str) else current
    # Keep the default-time reminder only while that suggestion is still in use.
    # Human edits resolve missing/ambiguous source dates and do not retain stale errors.
    return [warning] if current == datetime.fromisoformat(suggested) else []


async def editable_presentation(connection, items, *, source_items=(), meeting_date=None):
    items = await annotate_duplicates(connection, items)
    sources = {item["item_id"]: item for item in source_items}
    ids = [item["opportunity_id"] for item in items if item.get("opportunity_id")]
    links = (
        {
            str(row["id"]): dict(row)
            for row in await connection.fetch(
                "SELECT o.id::text,o.name,o.customer_id::text,"
                "security.customer_reference(o.customer_id)->>'name' AS customer_name "
                "FROM crm.opportunity o WHERE o.id=ANY($1::uuid[]) AND o.deleted_at IS NULL "
                "AND security.authorization_opportunity('task.create_customer',o.id)",
                ids,
            )
        }
        if ids
        else {}
    )
    for item in items:
        warnings = []
        if not item.get("assignee_account_code"):
            warnings.append("请选择负责人")
        if not item.get("due_at"):
            warnings.append("请补充截止日期与时间")
        if not 5 <= len(item["description"]) <= 500:
            warnings.append("任务内容需调整为5–500字，未截断原文")
        link = links.get(item.get("opportunity_id"))
        if link and link["customer_id"] == item.get("customer_id"):
            item.update(customer_name=link["customer_name"], opportunity_name=link["name"])
        else:
            item.update(customer_name=None, opportunity_name=None)
            if item.get("association_kind") == "customer":
                warnings.append("请选择当前有权关联的客户及其商机")
        item["warnings"] = warnings
        item["source_warnings"] = source_date_warnings(item, sources.get(item["item_id"]), meeting_date)
    return items


class TaskImportService:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings
        self.repo = TaskImportRepository()

    async def assert_current(self, actor):
        # The inference boundary and persistence boundary both recheck current
        # identity and either create grant, including after an external response.
        await InferenceService(self.database, self.settings).assert_actor_current(actor, "task.import")

    async def preview(self, actor, request):
        fingerprint = source_hash(request.source_text)
        await self.assert_current(actor)
        async with self.database.transaction(actor, readonly=True) as connection:
            prior = await self.repo.matching(connection, request.meeting_date, fingerprint)
            if prior:
                return await self.detail(connection, actor, prior["id"])
            people = await TaskTargetRepository().recipients(connection, actor)
        items, method, warnings = await self._extract(actor, request, people)
        await self.assert_current(actor)
        async with self.database.transaction(actor) as connection:
            await require_capability(connection, actor, "task.create")
            items = await annotate_duplicates(connection, items)
            row = await self.repo.insert(
                connection, actor, request, fingerprint, method, warnings, items, editable(items)
            )
            return await self.detail(connection, actor, row["id"])

    async def _extract(self, actor, request, people, *, require_ai=False):
        candidates = candidate_lines(request.source_text, people)
        extracted = [{"source_line": row["source_line"], "description": row["description"]} for row in candidates]
        # Send each complete action once. Source text and owner hints stay local
        # for the persisted original, date suggestions and assignee mapping.
        model_facts = {"meeting_date": request.meeting_date.isoformat(), "candidates": extracted}
        method, warnings = "rules", []
        try:
            runtime = await load_runtime_configuration(self.database, actor, self.settings, capability="task_import")
            pilot = task_import_pilot_policy(runtime.settings, actor)
            platform = filtered_facts_runtime(
                self.database, runtime.settings, capability="task_import",
                block_requests=pilot.block_platform_requests,
            ) if pilot else None
            settings = runtime.settings if pilot else replace(runtime.settings, agent_platform_bindings_json="{}")

            def validate(value):
                parsed = ExtractedItems.model_validate(value).model_dump()
                if {v["source_line"] for v in parsed["items"]} != {v["source_line"] for v in candidates}:
                    raise ValueError("AI未完整对应原文，请使用逐行草稿")
                return parsed

            facts, messages = prepare_business_rules(
                runtime,
                model_facts,
                [
                    ChatMessage(
                        role="system", content=supplementary_prompt(PROMPT, runtime.prompt_overrides.get("task_import"))
                    ),
                    ChatMessage(
                        role="user",
                        content=json.dumps(model_facts, ensure_ascii=False),
                    ),
                ],
            )
            result = await InferenceService(self.database, settings, platform=platform).evaluate(
                actor=actor,
                mode="task_import",
                facts=facts,
                messages=messages,
                user_text="请逐条整理给定候选事项，保留原文的全部事实与行动。",
                validate=validate,
            )
            extracted, method = result.payload["items"], "ai"
        except PermissionError:
            raise
        except ImportLimitError:
            raise
        except Exception:
            if require_ai:
                raise TaskConflict("本次AI重新整理未成功，原草稿保持不变，请稍后重试") from None
            # No silent AI claim. This path never dispatches tasks or guesses an
            # owner/date; parsing failure does not erase the original source.
            warnings.append("AI未接入或本次整理未成功，已按原文逐行预拆；请逐项核对后派发")
        items = materialize_items(extracted, candidates, people, request.meeting_date)
        return items, method, warnings

    @staticmethod
    def _require_reparseable(row, version_no):
        if not row:
            raise TaskNotFound("批次不存在或无权编辑")
        if row["status"] != "draft":
            raise TaskConflict("批次已派发，不能重新整理")
        require_version(row["version_no"], version_no)
        # Normalize optional fields and date serialization before comparison.
        # Any saved human choice, including deselection, blocks replacement.
        def canonical(items):
            values = [TaskImportDraftItem.model_validate(item) for item in items]
            return [
                {
                    **value.model_dump(mode="json"),
                    "due_at": (
                        (value.due_at.astimezone(UTC) if value.due_at.tzinfo else value.due_at).isoformat()
                        if value.due_at else None
                    ),
                }
                for value in values
            ]
        if canonical(row["editable_items"]) != canonical(editable(row["items"])):
            raise TaskConflict("草稿已有人工编辑，请保留当前内容；不能自动覆盖后重新整理")

    async def reparse(self, actor, batch_id, body):
        await self.assert_current(actor)
        async with self.database.transaction(actor, readonly=True) as connection:
            row = await self.repo.own(connection, batch_id)
            self._require_reparseable(row, body.version_no)
            people = await TaskTargetRepository().recipients(connection, actor)
            request = TaskImportPreview(
                source_text=row["source_text"], meeting_date=row["meeting_date"], batch_name=row["batch_name"]
            )
        # External inference never holds the draft row lock. Failure leaves its
        # source, initial parse and human-editable draft completely unchanged.
        items, method, warnings = await self._extract(actor, request, people, require_ai=True)
        await self.assert_current(actor)
        async with self.database.transaction(actor) as connection:
            await require_capability(connection, actor, "task.create")
            current = await self.repo.own(connection, batch_id, lock=True)
            self._require_reparseable(current, body.version_no)
            items = await annotate_duplicates(connection, items)
            await self.repo.replace_parse(
                connection, batch_id, body.version_no, method, warnings, items, editable(items)
            )
            return await self.detail(connection, actor, batch_id)

    async def detail(self, connection, actor, batch_id):
        own = await self.repo.own(connection, batch_id)
        summaries = await self.repo.summaries(connection, batch_id)
        header = summaries[0] if summaries else None
        if not own and not header:
            raise TaskNotFound("批次不存在或无权查看")
        batch = own or header
        task_items = header.get("task_items", []) if header else []
        links = {str(link["task_id"]): link for link in task_items}
        tasks = [
            {
                **task,
                "import_item_id": links[task["id"]]["item_id"],
                "original_assignee": links[task["id"]].get("original_assignee"),
            }
            for task in await self.repo.task_rows(connection, list(links))
        ]
        groups = {}
        for task in tasks:
            owner = next((a for a in task.get("assignees", []) if a["responsibility"] == "owner"), None) or {}
            key = owner.get("user_id")
            groups.setdefault(key, {"user_id": key, "name": owner.get("name") or "待交接", "tasks": []})[
                "tasks"
            ].append(task)
        creator_name = (
            header.get("creator_name")
            if header
            else await connection.fetchval(
                "SELECT display_name FROM platform.user_ref WHERE id=$1::uuid", batch["creator_user_ref_id"]
            )
        )
        edits = (
            await editable_presentation(
                connection,
                batch.get("editable_items", []),
                source_items=batch.get("items", []),
                meeting_date=batch.get("meeting_date"),
            )
            if own and batch["status"] == "draft"
            else (batch.get("editable_items", []) if own else [])
        )
        return {
            key: batch.get(key)
            for key in (
                "id",
                "batch_name",
                "meeting_date",
                "status",
                "version_no",
                "parse_method",
                "created_at",
                "updated_at",
                "confirmed_at",
                "creator_user_ref_id",
            )
        } | {
            "creator_name": creator_name,
            "is_creator": bool(own),
            "source_text": batch.get("source_text") if own else None,
            "warnings": batch.get("parse_warnings", []) if own else [],
            "items": batch.get("items", []) if own else [],
            "editable_items": edits,
            "tasks": tasks,
            "summary": counters(tasks),
            "summary_scope": "当前账号可见任务；已取消不计完成率分母，待验收不计完成",
            "assignees": [
                {"user_id": value["user_id"], "name": value["name"], **counters(value["tasks"])}
                for value in groups.values()
            ],
        }

    async def list(self, connection, actor, *, status=None, limit=20, offset=0):
        # The security projection paginates before returning headers. Raw source,
        # draft items and all historical task IDs never travel through this path.
        rows = await self.repo.page(connection, status=status, limit=limit + 1, offset=offset)
        page = rows[:limit]
        statistics = {
            row["batch_id"]: row
            for row in await self.repo.task_statistics(
                connection, [str(row["id"]) for row in page if row["status"] == "confirmed"]
            )
        }
        items = []
        for row in page:
            summary = statistics.get(str(row["id"]), counters([]))
            denominator = summary["visible_task_count"] - summary["cancelled"]
            summary = {
                **summary,
                "task_count": summary["visible_task_count"],
                "completion_rate": round(summary["completed"] / denominator, 4) if denominator else None,
            }
            summary.pop("batch_id", None)
            items.append(
                {
                    key: row.get(key)
                    for key in (
                        "id",
                        "batch_name",
                        "meeting_date",
                        "status",
                        "version_no",
                        "parse_method",
                        "created_at",
                        "confirmed_at",
                        "creator_user_ref_id",
                        "creator_name",
                    )
                }
                | {
                    "summary": summary,
                    "is_creator": str(row["creator_user_ref_id"]) == actor.user_id,
                    "summary_scope": "当前账号可见任务；已取消不计完成率分母，待验收不计完成",
                }
            )
        more = len(rows) > limit
        return {"items": items, "has_more": more, "next_offset": offset + limit if more else None}

    async def save_draft(self, connection, actor, batch_id, body):
        await require_capability(connection, actor, "task.create")
        row = await self.repo.own(connection, batch_id, lock=True)
        if not row:
            raise TaskNotFound("批次不存在或无权编辑")
        if row["status"] != "draft":
            raise TaskConflict("批次已派发，不能修改原始草稿")
        require_version(row["version_no"], body.version_no)
        allowed = {v["item_id"] for v in row["items"]}
        if any(str(item.item_id) not in allowed for item in body.items):
            raise TaskConflict("草稿条目不属于此批次")
        old = {v["item_id"]: v for v in row["editable_items"]}
        values = []
        for item in body.items:
            value = item.model_dump(mode="json")
            previous = old.get(value["item_id"], {})
            if any(
                value.get(key) != previous.get(key)
                for key in (
                    "description",
                    "assignee_account_code",
                    "due_at",
                    "priority_code",
                    "association_kind",
                    "customer_id",
                    "opportunity_id",
                )
            ):
                value["duplicate_acknowledged"] = False
            values.append(value)
        await connection.execute(
            "UPDATE workflow.task_import_batch SET editable_items=$2::jsonb,version_no=version_no+1 WHERE id=$1::uuid",
            batch_id,
            values,
        )
        return {"batch_id": batch_id}

    async def confirm(self, connection, actor, batch_id, body):
        await require_capability(connection, actor, "task.create")
        row = await self.repo.own(connection, batch_id, lock=True)
        if not row:
            raise TaskNotFound("批次不存在或无权派发")
        fingerprint = confirmation_hash(body.items)
        if row["status"] == "confirmed":
            if row["confirmed_payload_hash"] != fingerprint:
                raise TaskConflict("此批次已按其他内容派发，请查看派发记录")
            return {"batch_id": batch_id}
        require_version(row["version_no"], body.version_no)
        sources = {value["item_id"]: value for value in row["items"]}
        if any(str(item.item_id) not in sources for item in body.items):
            raise TaskConflict("确认条目不属于此批次")
        annotated = await annotate_duplicates(connection, [item.model_dump(mode="json") for item in body.items])
        if any(item["duplicates"] and not item["duplicate_acknowledged"] for item in annotated):
            raise TaskConflict("发现相似任务，请逐条核对并明确确认重复派发")
        old_feature = await connection.fetchval("SELECT current_setting('app.authorized_feature',true)")
        for item in body.items:
            value = item.model_dump(exclude={"item_id", "duplicate_acknowledged"})
            if value["customer_id"]:
                value["customer_id"] = str(value["customer_id"])
            if value["opportunity_id"]:
                value["opportunity_id"] = str(value["opportunity_id"])
            permission = "task.create_" + task_association_kind(
                value["customer_id"], value["opportunity_id"], value["association_kind"]
            )
            await connection.execute("SELECT set_config('app.authorized_feature',$1,true)", permission)
            task = await TaskService().create(connection, actor=actor, **value, source_code="meeting_import")
            owner = next(a for a in task["assignees"] if a["responsibility"] == "owner")
            await connection.execute(
                "INSERT INTO workflow.task_import_item(workspace_id,batch_id,item_id,task_id,source_line,"
                "original_assignee_user_ref_id,confirmed_item) "
                "VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,$6::uuid,$7::jsonb)",
                actor.workspace_id,
                batch_id,
                str(item.item_id),
                task["id"],
                sources[str(item.item_id)]["source_line"],
                owner["user_id"],
                item.model_dump(mode="json"),
            )
        # On failure the owning transaction rolls back this setting and all
        # writes. Do not issue cleanup SQL in a failed PostgreSQL transaction:
        # that would replace the original cause with InFailedSQLTransaction.
        await connection.execute("SELECT set_config('app.authorized_feature',$1,true)", old_feature or "")
        await connection.execute(
            "UPDATE workflow.task_import_batch SET status='confirmed',confirmed_at=clock_timestamp(),"
            "confirmed_payload_hash=$2,version_no=version_no+1 WHERE id=$1::uuid",
            batch_id,
            fingerprint,
        )
        return {"batch_id": batch_id}
