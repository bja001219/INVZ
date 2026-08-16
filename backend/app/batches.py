"""Scene-wide generation batches.

A batch is a bookkeeping record, not a scheduler: it enqueues one job per Cut in a single
request and stamps them with a shared id. Concurrency lives in the worker, so a batch never
needs its own runner. Batch progress is derived from the jobs rather than stored, which keeps
the state machine the single source of truth.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.generations import create_image_job, create_video_job
from app.models import Cut, GenerationBatch, GenerationKind, Scene
from app.schemas import BatchResponse, BatchSkip, CreateGenerationRequest


async def create_image_batch(
    session: AsyncSession,
    scene_id: UUID,
    request: CreateGenerationRequest,
    *,
    max_attempts: int,
    generation_mode: str,
) -> BatchResponse:
    return await _create_batch(
        session,
        scene_id,
        request,
        kind=GenerationKind.IMAGE,
        max_attempts=max_attempts,
        generation_mode=generation_mode,
    )


async def create_video_batch(
    session: AsyncSession,
    scene_id: UUID,
    request: CreateGenerationRequest,
    *,
    max_attempts: int,
    generation_mode: str,
) -> BatchResponse:
    return await _create_batch(
        session,
        scene_id,
        request,
        kind=GenerationKind.VIDEO,
        max_attempts=max_attempts,
        generation_mode=generation_mode,
    )


async def _create_batch(
    session: AsyncSession,
    scene_id: UUID,
    request: CreateGenerationRequest,
    *,
    kind: GenerationKind,
    max_attempts: int,
    generation_mode: str,
) -> BatchResponse:
    # Reading the Cuts and inserting the batch share one transaction: the session must be idle
    # again before create_*_job opens its own short transaction per Cut.
    async with session.begin():
        if await session.get(Scene, scene_id) is None:
            raise AppError(status_code=404, code="SCENE_NOT_FOUND", message="Scene not found")
        cut_ids = list(
            await session.scalars(
                select(Cut.id).where(Cut.scene_id == scene_id).order_by(Cut.order)
            )
        )
        batch = GenerationBatch(scene_id=scene_id, kind=kind, requested_count=len(cut_ids))
        session.add(batch)
        await session.flush()
        batch_id = batch.id

    create = create_image_job if kind is GenerationKind.IMAGE else create_video_job
    created: list[UUID] = []
    skipped: list[BatchSkip] = []
    for cut_id in cut_ids:
        try:
            job = await create(
                session,
                cut_id,
                request,
                max_attempts=max_attempts,
                generation_mode=generation_mode,
                batch_id=batch_id,
            )
        except AppError as error:
            # One unusable Cut must not cancel the other five. Report it and keep going.
            skipped.append(BatchSkip(cut_id=cut_id, reason=error.code))
        else:
            created.append(job.id)

    return BatchResponse(
        id=batch_id,
        kind=kind,
        requested_count=len(cut_ids),
        created_job_ids=created,
        skipped=skipped,
    )
