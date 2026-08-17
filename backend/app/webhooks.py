"""Provider webhook intake.

The webhook does not own a second state machine. It normalizes the callback with the same
function polling uses, then hands the result to the same worker transition. Duplicate and
late deliveries are absorbed by the worker's `status is PROCESSING` guard, so the two paths
can race without producing two artifacts.
"""

import secrets
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import GenerationJob, JobStatus
from app.providers.contracts import ProviderError
from app.providers.kie import task_result_from_data
from app.schemas import WebhookAck
from app.worker import GenerationWorker

_IGNORED = WebhookAck(status="ignored")


def verify_webhook_secret(configured: str | None, *provided: str | None) -> None:
    """Accept the secret from any of the channels a provider can actually use.

    Kie is handed one callback URL and nothing else, so a header-only check makes the Live
    webhook unsatisfiable: every delivery 401s and the provider retries forever. The secret
    therefore also travels as a query token in the callback URL we register. Both candidates
    are compared in constant time and the failure never says which one was tried.
    """
    if not configured:
        raise AppError(
            status_code=503,
            code="WEBHOOK_DISABLED",
            message="Webhook delivery is not configured",
        )
    matched = False
    for candidate in provided:
        if candidate is not None and secrets.compare_digest(configured, candidate):
            matched = True
    if not matched:
        raise AppError(
            status_code=401,
            code="WEBHOOK_UNAUTHORIZED",
            message="Webhook credentials are invalid",
        )


def webhook_task_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept both the bare task record and the `{"data": {...}}` envelope Kie sends."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        raise AppError(
            status_code=422,
            code="WEBHOOK_PAYLOAD_INVALID",
            message="Webhook payload is invalid",
        )
    return data


async def apply_webhook(
    session: AsyncSession,
    worker: GenerationWorker,
    payload: dict[str, Any],
) -> WebhookAck:
    data = webhook_task_data(payload)
    task_id = data.get("taskId")
    if not isinstance(task_id, str) or not task_id.strip():
        raise AppError(
            status_code=422,
            code="WEBHOOK_PAYLOAD_INVALID",
            message="Webhook payload is invalid",
        )

    job_id = await _processing_job_id(session, task_id.strip())
    if job_id is None:
        # Unknown or already-terminal task. Answering 2xx stops the provider from retrying
        # a delivery that can never change anything.
        return _IGNORED

    try:
        result = task_result_from_data(data)
    except ProviderError as error:
        raise AppError(
            status_code=422,
            code="WEBHOOK_PAYLOAD_INVALID",
            message="Webhook payload is invalid",
        ) from error

    if result.state == "PENDING":
        return _IGNORED
    try:
        await worker.apply_external_result(job_id, result)
    except IntegrityError:
        # A poll of the same task committed first. Both transactions read PROCESSING before
        # either took the write lock, and the unique artifact index rejected the loser. The
        # job is already complete, so this delivery has nothing left to do.
        return _IGNORED
    return WebhookAck(status="applied")


async def _processing_job_id(session: AsyncSession, task_id: str) -> UUID | None:
    job_id: UUID | None = await session.scalar(
        select(GenerationJob.id).where(
            GenerationJob.external_task_id == task_id,
            GenerationJob.status == JobStatus.PROCESSING,
        )
    )
    return job_id
