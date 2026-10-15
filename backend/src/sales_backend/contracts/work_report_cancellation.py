"""Owner cancellation of expired preparation that has never started."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime

from sales_backend.contracts.work_report_base import Digest, ReportModel


class SnapshotCancellationState(ReportModel):
    id: UUID
    status: Literal["preparing", "ready", "failed"]
    revision: Digest
    cancellable: bool
    eligible_at: AwareDatetime | None
    reason: Literal["expired_unstarted", "not_expired", "already_started_or_finished", "snapshot_terminal",
                    "job_unavailable", "job_reference_mismatch"]


class SnapshotCancellationRequest(ReportModel):
    expected_revision: Digest
