from collections.abc import Sequence
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import AppError
from app.models import Cut, Scene
from app.providers.contracts import PermanentProviderError, RetryableProviderError, SceneProvider
from app.schemas import CutDraft, CutResponse, SceneDraft, SceneResponse


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

    async with session.begin():
        scene = Scene(user_prompt=prompt, title=draft.title, scenario=draft.scenario)
        session.add(scene)
        await session.flush()
        session.add_all(
            [
                Cut(
                    scene_id=scene.id,
                    order=cut.order,
                    image_prompt=cut.image_prompt,
                    video_prompt=cut.video_prompt,
                    duration_sec=cut.duration_sec,
                )
                for cut in draft.cuts
            ]
        )

    return _scene_response(scene, draft.cuts)


async def get_scene(session: AsyncSession, scene_id: UUID) -> SceneResponse:
    scene = await session.get(Scene, scene_id)
    if scene is None:
        raise AppError(status_code=404, code="SCENE_NOT_FOUND", message="Scene not found")

    result = await session.scalars(select(Cut).where(Cut.scene_id == scene.id).order_by(Cut.order))
    cuts = list(result)
    return _scene_response(scene, cuts)


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


def _scene_response(scene: Scene, cuts: Sequence[Cut | CutDraft]) -> SceneResponse:
    return SceneResponse(
        id=scene.id,
        user_prompt=scene.user_prompt,
        title=scene.title,
        scenario=scene.scenario,
        cuts=[
            CutResponse(
                order=cut.order,
                image_prompt=cut.image_prompt,
                video_prompt=cut.video_prompt,
                duration_sec=cut.duration_sec,
            )
            for cut in cuts
        ],
    )
