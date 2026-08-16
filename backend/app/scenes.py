from collections.abc import Sequence
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import AppError
from app.models import Cut, CutImage, CutVideo, GenerationJob, GenerationKind, Scene
from app.prompting import compose_image_prompt, compose_video_prompt
from app.providers.contracts import PermanentProviderError, RetryableProviderError, SceneProvider
from app.schemas import (
    CharacterProfile,
    CutImageResponse,
    CutResponse,
    CutVideoResponse,
    GenerationJobResponse,
    SceneDraft,
    SceneResponse,
)


async def create_scene(
    session: AsyncSession,
    provider: SceneProvider,
    prompt: str,
    *,
    max_attempts: int = 3,
    retry_base_delay_sec: float = 1,
    clock: Clock | None = None,
) -> SceneResponse:
    draft = await _generate_draft(
        provider,
        prompt,
        max_attempts=max_attempts,
        retry_base_delay_sec=retry_base_delay_sec,
        clock=clock or Clock(),
    )

    profiles = draft.character_profiles
    async with session.begin():
        scene = Scene(
            user_prompt=prompt,
            title=draft.title,
            scenario=draft.scenario,
            character_profiles=[profile.model_dump(by_alias=True) for profile in profiles],
        )
        session.add(scene)
        await session.flush()
        cuts = [
            Cut(
                scene_id=scene.id,
                order=cut.order,
                shot_description=cut.shot_description,
                video_motion=cut.video_motion,
                image_prompt=compose_image_prompt(profiles, cut.shot_description),
                video_prompt=compose_video_prompt(
                    profiles, cut.shot_description, cut.video_motion
                ),
                duration_sec=cut.duration_sec,
            )
            for cut in draft.cuts
        ]
        session.add_all(cuts)
        await session.flush()

    return _scene_response(scene, cuts)


async def get_scene(session: AsyncSession, scene_id: UUID) -> SceneResponse:
    scene = await session.get(Scene, scene_id)
    if scene is None:
        raise AppError(status_code=404, code="SCENE_NOT_FOUND", message="Scene not found")

    result = await session.scalars(select(Cut).where(Cut.scene_id == scene.id).order_by(Cut.order))
    cuts = list(result)
    cut_ids = [cut.id for cut in cuts]
    jobs = list(
        await session.scalars(
            select(GenerationJob)
            .where(GenerationJob.cut_id.in_(cut_ids))
            .order_by(GenerationJob.kind, GenerationJob.version.desc())
        )
    )
    images = list(
        await session.scalars(
            select(CutImage)
            .join(GenerationJob, CutImage.generation_job_id == GenerationJob.id)
            .where(CutImage.cut_id.in_(cut_ids))
            .order_by(CutImage.cut_id, GenerationJob.version.desc())
        )
    )
    videos = list(
        await session.scalars(
            select(CutVideo)
            .join(GenerationJob, CutVideo.generation_job_id == GenerationJob.id)
            .where(CutVideo.cut_id.in_(cut_ids))
            .order_by(CutVideo.cut_id, GenerationJob.version.desc())
        )
    )
    return _scene_response(scene, cuts, jobs=jobs, images=images, videos=videos)


async def _generate_draft(
    provider: SceneProvider,
    prompt: str,
    *,
    max_attempts: int,
    retry_base_delay_sec: float,
    clock: Clock,
) -> SceneDraft:
    for retry_index in range(max_attempts):
        try:
            return SceneDraft.model_validate(await provider.generate(prompt))
        except RetryableProviderError as error:
            if retry_index == max_attempts - 1:
                raise AppError(
                    status_code=502,
                    code="SCENE_PROVIDER_UNAVAILABLE",
                    message="Scene provider unavailable",
                ) from error
            await clock.sleep(retry_base_delay_sec * 2**retry_index)
        except PermanentProviderError as error:
            raise AppError(
                status_code=502,
                code="SCENE_PROVIDER_FAILED",
                message="Scene provider failed",
            ) from error
        except ValidationError as error:
            raise AppError(
                status_code=502,
                code="SCENE_SCHEMA_INVALID",
                message="Scene output was invalid",
            ) from error

    raise AssertionError("unreachable")


def _scene_response(
    scene: Scene,
    cuts: Sequence[Cut],
    *,
    jobs: Sequence[GenerationJob] = (),
    images: Sequence[CutImage] = (),
    videos: Sequence[CutVideo] = (),
) -> SceneResponse:
    jobs_by_cut: dict[UUID, list[GenerationJob]] = {}
    images_by_cut: dict[UUID, list[CutImage]] = {}
    videos_by_cut: dict[UUID, list[CutVideo]] = {}
    for job in jobs:
        jobs_by_cut.setdefault(job.cut_id, []).append(job)
    for image in images:
        images_by_cut.setdefault(image.cut_id, []).append(image)
    for video in videos:
        videos_by_cut.setdefault(video.cut_id, []).append(video)
    return SceneResponse(
        id=scene.id,
        user_prompt=scene.user_prompt,
        title=scene.title,
        scenario=scene.scenario,
        character_profiles=[
            CharacterProfile.model_validate(profile) for profile in scene.character_profiles or []
        ],
        cuts=[
            _cut_response(
                cut,
                jobs=jobs_by_cut.get(cut.id, []),
                images=images_by_cut.get(cut.id, []),
                videos=videos_by_cut.get(cut.id, []),
            )
            for cut in cuts
        ],
    )


def _cut_response(
    cut: Cut,
    *,
    jobs: Sequence[GenerationJob],
    images: Sequence[CutImage],
    videos: Sequence[CutVideo],
) -> CutResponse:
    image_jobs = [_job_response(job) for job in jobs if job.kind is GenerationKind.IMAGE]
    video_jobs = [_job_response(job) for job in jobs if job.kind is GenerationKind.VIDEO]
    return CutResponse(
        id=cut.id,
        order=cut.order,
        shot_description=cut.shot_description,
        video_motion=cut.video_motion,
        image_prompt=cut.image_prompt,
        video_prompt=cut.video_prompt,
        duration_sec=cut.duration_sec,
        selected_image_id=cut.selected_image_id,
        selected_video_id=cut.selected_video_id,
        image_jobs=image_jobs,
        video_jobs=video_jobs,
        images=[
            CutImageResponse(
                id=image.id,
                generation_job_id=image.generation_job_id,
                url=image.url,
                input_prompt=image.input_prompt,
                created_at=image.created_at,
            )
            for image in images
        ],
        videos=[
            CutVideoResponse(
                id=video.id,
                cut_image_id=video.cut_image_id,
                generation_job_id=video.generation_job_id,
                url=video.url,
                input_prompt=video.input_prompt,
                created_at=video.created_at,
            )
            for video in videos
        ],
    )


def _job_response(job: GenerationJob) -> GenerationJobResponse:
    return GenerationJobResponse(
        id=job.id,
        kind=job.kind,
        version=job.version,
        status=job.status,
        prompt=job.prompt,
        generation_mode=job.generation_mode,
        source_image_id=job.source_image_id,
        reference_image_id=job.reference_image_id,
        batch_id=job.batch_id,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        next_run_at=job.next_run_at,
        last_error_code=job.last_error_code,
        last_error_message=job.last_error_message,
        mock_scenario=job.mock_scenario,
    )
