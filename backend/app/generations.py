from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Cut, CutImage, CutVideo, GenerationJob, GenerationKind, JobStatus
from app.schemas import CreateGenerationRequest, MockScenario, normalize_mock_scenario


async def create_image_job(
    session: AsyncSession,
    cut_id: UUID,
    request: CreateGenerationRequest,
    *,
    max_attempts: int = 3,
    generation_mode: str = "MOCK",
    batch_id: UUID | None = None,
) -> GenerationJob:
    return await _create_job(
        session,
        cut_id,
        kind=GenerationKind.IMAGE,
        request=request,
        max_attempts=max_attempts,
        generation_mode=generation_mode,
        batch_id=batch_id,
    )


async def create_video_job(
    session: AsyncSession,
    cut_id: UUID,
    request: CreateGenerationRequest,
    *,
    max_attempts: int = 3,
    generation_mode: str = "MOCK",
    batch_id: UUID | None = None,
) -> GenerationJob:
    return await _create_job(
        session,
        cut_id,
        kind=GenerationKind.VIDEO,
        request=request,
        max_attempts=max_attempts,
        generation_mode=generation_mode,
        batch_id=batch_id,
    )


async def select_image(session: AsyncSession, cut_id: UUID, image_id: UUID) -> Cut:
    async with session.begin():
        cut = await _cut_or_error(session, cut_id)
        image = await session.scalar(
            select(CutImage)
            .join(GenerationJob, CutImage.generation_job_id == GenerationJob.id)
            .where(
                CutImage.id == image_id,
                CutImage.cut_id == cut.id,
                GenerationJob.cut_id == cut.id,
                GenerationJob.kind == GenerationKind.IMAGE,
                GenerationJob.status == JobStatus.SUCCEEDED,
            )
        )
        if image is None:
            raise AppError(status_code=404, code="IMAGE_NOT_FOUND", message="Image not found")
        cut.selected_image_id = image.id
        cut.selected_video_id = None
    return cut


async def select_video(session: AsyncSession, cut_id: UUID, video_id: UUID) -> Cut:
    async with session.begin():
        cut = await _cut_or_error(session, cut_id)
        video = await session.scalar(
            select(CutVideo)
            .join(GenerationJob, CutVideo.generation_job_id == GenerationJob.id)
            .where(
                CutVideo.id == video_id,
                CutVideo.cut_id == cut.id,
                GenerationJob.cut_id == cut.id,
                GenerationJob.kind == GenerationKind.VIDEO,
                GenerationJob.status == JobStatus.SUCCEEDED,
                GenerationJob.source_image_id == CutVideo.cut_image_id,
            )
        )
        if video is None:
            raise AppError(status_code=404, code="VIDEO_NOT_FOUND", message="Video not found")
        if video.cut_image_id != cut.selected_image_id:
            raise AppError(
                status_code=409,
                code="VIDEO_SOURCE_MISMATCH",
                message="Video must use the selected image",
            )
        cut.selected_video_id = video.id
    return cut


async def _create_job(
    session: AsyncSession,
    cut_id: UUID,
    *,
    kind: GenerationKind,
    request: CreateGenerationRequest,
    max_attempts: int,
    generation_mode: str = "MOCK",
    batch_id: UUID | None = None,
) -> GenerationJob:
    try:
        async with session.begin():
            cut = await _cut_or_error(session, cut_id)
            source_image_id: UUID | None = None
            prompt = cut.image_prompt
            if kind is GenerationKind.VIDEO:
                source_image, source_mode = await _selected_successful_image(session, cut)
                if source_mode != generation_mode:
                    raise AppError(
                        status_code=409,
                        code="ARTIFACT_MODE_MISMATCH",
                        message="The selected image was generated in a different mode",
                    )
                source_image_id = source_image.id
                prompt = cut.video_prompt
            version = await session.scalar(
                select(func.coalesce(func.max(GenerationJob.version), 0) + 1).where(
                    GenerationJob.cut_id == cut.id,
                    GenerationJob.kind == kind,
                )
            )
            job = GenerationJob(
                cut_id=cut.id,
                kind=kind,
                version=version,
                status=JobStatus.QUEUED,
                prompt=prompt,
                generation_mode=generation_mode,
                source_image_id=source_image_id,
                batch_id=batch_id,
                attempt_count=0,
                max_attempts=max_attempts,
                mock_scenario=_normalized_mock_scenario(request.mock_scenario),
            )
            session.add(job)
            await session.flush()
    except IntegrityError as error:
        if _is_active_generation_conflict(error):
            raise AppError(
                status_code=409,
                code="GENERATION_ALREADY_ACTIVE",
                message="A generation job is already active",
            ) from error
        raise
    return job


def _normalized_mock_scenario(raw_scenario: str | None) -> MockScenario | None:
    try:
        return normalize_mock_scenario(raw_scenario)
    except ValueError as error:
        raise AppError(
            status_code=422,
            code="REQUEST_VALIDATION_FAILED",
            message="Request validation failed",
        ) from error


def _is_active_generation_conflict(error: IntegrityError) -> bool:
    return "UNIQUE constraint failed: generation_jobs.cut_id, generation_jobs.kind" in str(
        error.orig
    )


async def _cut_or_error(session: AsyncSession, cut_id: UUID) -> Cut:
    cut = await session.get(Cut, cut_id)
    if cut is None:
        raise AppError(status_code=404, code="CUT_NOT_FOUND", message="Cut not found")
    return cut


async def _selected_successful_image(session: AsyncSession, cut: Cut) -> tuple[CutImage, str]:
    """The selected image and the mode that produced it.

    The mode travels with the image because an artifact is only usable by the provider that
    made it: a Mock image is a path this app serves, and a Live image is a URL on the
    provider's CDN. Handing one to the other fails, and the caller has to say why.
    """
    if cut.selected_image_id is None:
        raise AppError(
            status_code=409,
            code="SELECTED_IMAGE_REQUIRED",
            message="A successful selected image is required",
        )
    row = (
        await session.execute(
            select(CutImage, GenerationJob.generation_mode)
            .join(GenerationJob, CutImage.generation_job_id == GenerationJob.id)
            .where(
                CutImage.id == cut.selected_image_id,
                CutImage.cut_id == cut.id,
                GenerationJob.cut_id == cut.id,
                GenerationJob.kind == GenerationKind.IMAGE,
                GenerationJob.status == JobStatus.SUCCEEDED,
            )
        )
    ).first()
    if row is None:
        raise AppError(
            status_code=409,
            code="SELECTED_IMAGE_REQUIRED",
            message="A successful selected image is required",
        )
    image, generation_mode = row
    return image, generation_mode
