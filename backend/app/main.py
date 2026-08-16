from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.batches import create_image_batch, create_video_batch
from app.core.clock import Clock
from app.core.config import GenerationMode, Settings, get_settings
from app.core.db import build_engine
from app.core.errors import AppError, register_error_handlers
from app.core.logging import configure_secret_redaction
from app.generations import create_image_job, create_video_job, select_image, select_video
from app.providers.contracts import GenerationProvider
from app.providers.kie import KieGenerationProvider
from app.providers.mock import MockGenerationProvider, MockSceneProvider
from app.providers.openai_scene import OpenAISceneProvider
from app.scenes import _job_response, create_scene, get_scene
from app.schemas import (
    BatchResponse,
    ConfigResponse,
    CreateGenerationRequest,
    GenerationJobResponse,
    SceneCreateRequest,
    SceneResponse,
    SelectImageRequest,
    SelectVideoRequest,
    normalize_mock_scenario,
)
from app.worker import GenerationWorker


def create_app(settings: Settings, *, generation_worker: GenerationWorker | None = None) -> FastAPI:
    engine = build_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    generation_provider: GenerationProvider | None = None
    if generation_worker is None:
        if settings.generation_mode is GenerationMode.MOCK:
            generation_provider = MockGenerationProvider()
        else:
            generation_provider = KieGenerationProvider(
                api_key=settings.kie_api_key.get_secret_value()
            )
        generation_worker = GenerationWorker(
            session_factory=session_factory,
            provider=generation_provider,
            clock=Clock(),
            retry_base_delay_sec=settings.retry_base_delay_sec,
            provider_poll_interval_sec=settings.provider_poll_interval_sec,
            generation_attempt_timeout_sec=settings.generation_attempt_timeout_sec,
            concurrency=settings.generation_concurrency,
        )

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        await generation_worker.start()
        try:
            yield
        finally:
            await generation_worker.stop()
            if isinstance(generation_provider, KieGenerationProvider):
                await generation_provider.aclose()
            await engine.dispose()

    application = FastAPI(title="InvzAssign Prompt-to-Animation MVP", lifespan=lifespan)
    register_error_handlers(application)
    application.state.engine = engine
    application.state.generation_worker = generation_worker

    async def get_app_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    scene_provider: MockSceneProvider | OpenAISceneProvider
    if settings.generation_mode is GenerationMode.MOCK:
        scene_provider = MockSceneProvider()
    else:
        scene_provider = OpenAISceneProvider(api_key=settings.openai_api_key.get_secret_value())

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    mock_media_dir = Path(__file__).resolve().parent / "static" / "mock"
    application.mount(
        "/media/mock",
        StaticFiles(directory=mock_media_dir, check_dir=False),
        name="mock-media",
    )

    configure_secret_redaction(
        [
            settings.openai_api_key.get_secret_value(),
            settings.kie_api_key.get_secret_value(),
        ]
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/config", response_model=ConfigResponse)
    async def config() -> ConfigResponse:
        return ConfigResponse(generation_mode=settings.generation_mode.name)

    @application.post(
        "/api/scenes",
        response_model=SceneResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def post_scene(
        payload: SceneCreateRequest,
        session: Annotated[AsyncSession, Depends(get_app_session)],
    ) -> SceneResponse:
        return await create_scene(
            session,
            scene_provider,
            payload.prompt,
            max_attempts=settings.generation_max_attempts,
            retry_base_delay_sec=settings.retry_base_delay_sec,
        )

    @application.get("/api/scenes/{scene_id}", response_model=SceneResponse)
    async def read_scene(
        scene_id: UUID,
        session: Annotated[AsyncSession, Depends(get_app_session)],
    ) -> SceneResponse:
        return await get_scene(session, scene_id)

    def validate_generation_request(payload: CreateGenerationRequest) -> None:
        if settings.generation_mode is GenerationMode.LIVE and payload.mock_scenario is not None:
            raise AppError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="GENERATION_REQUEST_INVALID",
                message="mockScenario is available only in Mock mode",
            )
        try:
            payload.mock_scenario = normalize_mock_scenario(payload.mock_scenario)
        except ValueError as error:
            raise AppError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="REQUEST_VALIDATION_FAILED",
                message="Request validation failed",
            ) from error

    @application.post(
        "/api/cuts/{cut_id}/images",
        response_model=GenerationJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def post_image_generation(
        cut_id: UUID,
        payload: CreateGenerationRequest,
        session: Annotated[AsyncSession, Depends(get_app_session)],
    ) -> GenerationJobResponse:
        validate_generation_request(payload)
        job = await create_image_job(
            session, cut_id, payload, max_attempts=settings.generation_max_attempts
        )
        return _job_response(job)

    @application.post(
        "/api/cuts/{cut_id}/videos",
        response_model=GenerationJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def post_video_generation(
        cut_id: UUID,
        payload: CreateGenerationRequest,
        session: Annotated[AsyncSession, Depends(get_app_session)],
    ) -> GenerationJobResponse:
        validate_generation_request(payload)
        job = await create_video_job(
            session, cut_id, payload, max_attempts=settings.generation_max_attempts
        )
        return _job_response(job)

    @application.post(
        "/api/scenes/{scene_id}/images",
        response_model=BatchResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def post_image_batch(
        scene_id: UUID,
        payload: CreateGenerationRequest,
        session: Annotated[AsyncSession, Depends(get_app_session)],
    ) -> BatchResponse:
        validate_generation_request(payload)
        return await create_image_batch(
            session,
            scene_id,
            payload,
            max_attempts=settings.generation_max_attempts,
            generation_mode=settings.generation_mode.name,
        )

    @application.post(
        "/api/scenes/{scene_id}/videos",
        response_model=BatchResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def post_video_batch(
        scene_id: UUID,
        payload: CreateGenerationRequest,
        session: Annotated[AsyncSession, Depends(get_app_session)],
    ) -> BatchResponse:
        validate_generation_request(payload)
        return await create_video_batch(
            session,
            scene_id,
            payload,
            max_attempts=settings.generation_max_attempts,
            generation_mode=settings.generation_mode.name,
        )

    @application.put("/api/cuts/{cut_id}/selected-image", response_model=SceneResponse)
    async def put_selected_image(
        cut_id: UUID,
        payload: SelectImageRequest,
        session: Annotated[AsyncSession, Depends(get_app_session)],
    ) -> SceneResponse:
        cut = await select_image(session, cut_id, payload.image_id)
        return await get_scene(session, cut.scene_id)

    @application.put("/api/cuts/{cut_id}/selected-video", response_model=SceneResponse)
    async def put_selected_video(
        cut_id: UUID,
        payload: SelectVideoRequest,
        session: Annotated[AsyncSession, Depends(get_app_session)],
    ) -> SceneResponse:
        cut = await select_video(session, cut_id, payload.video_id)
        return await get_scene(session, cut.scene_id)

    return application


app = create_app(get_settings())
