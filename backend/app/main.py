import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, status
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
from app.models import GenerationKind
from app.providers.kie import KieGenerationProvider
from app.providers.mock import (
    MockGenerationProvider,
    MockSceneProvider,
    mock_result_url,
)
from app.providers.openai_scene import OpenAISceneProvider
from app.runtime import ProviderRegistry, RuntimeMode
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
    UpdateConfigRequest,
    WebhookAck,
    normalize_mock_scenario,
)
from app.webhooks import apply_webhook, verify_webhook_secret
from app.worker import GenerationWorker

logger = logging.getLogger(__name__)


def create_app(settings: Settings, *, generation_worker: GenerationWorker | None = None) -> FastAPI:
    engine = build_engine(settings.database_url)
    background_tasks: set[asyncio.Task[None]] = set()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Both provider pairs are constructed up front so the mode can flip without a restart.
    # Live is only constructible when both keys are present, which is exactly what
    # `liveAvailable` reports.
    live_scene: OpenAISceneProvider | None = None
    live_generation: KieGenerationProvider | None = None
    if settings.openai_api_key.get_secret_value().strip() and (
        settings.kie_api_key.get_secret_value().strip()
    ):
        live_scene = OpenAISceneProvider(api_key=settings.openai_api_key.get_secret_value())
        live_generation = KieGenerationProvider(
            api_key=settings.kie_api_key.get_secret_value(),
            # Built once, with the callback already wired: rebuilding it later would strand
            # the first provider's HTTP client with nothing to close it.
            callback_url=settings.webhook_public_url or None,
        )

    webhook_secret = settings.webhook_secret.get_secret_value()

    async def deliver_mock_webhook(task_id: str, kind: GenerationKind) -> None:
        """Post a provider-shaped callback to our own endpoint after a short delay."""
        if not webhook_secret:
            return

        base_url = settings.self_base_url.rstrip("/")

        async def send() -> None:
            await asyncio.sleep(settings.mock_webhook_delay_sec)
            # Absolute, because a real provider callback carries an absolute URL and the
            # shared normalizer rejects anything that is not http(s).
            result_url = f"{base_url}{mock_result_url(kind)}"
            payload = {
                "data": {
                    "taskId": task_id,
                    "state": "success",
                    "resultJson": json.dumps({"resultUrls": [result_url]}),
                }
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as callback_client:
                    await callback_client.post(
                        f"{base_url}/api/webhooks/kie",
                        headers={"X-Webhook-Secret": webhook_secret},
                        json=payload,
                    )
            except httpx.HTTPError:
                # Polling remains the backstop, so a lost callback only costs latency.
                logger.warning("Mock webhook delivery failed")

        delivery = asyncio.create_task(send())
        background_tasks.add(delivery)
        delivery.add_done_callback(background_tasks.discard)

    registry = ProviderRegistry(
        mock_scene=MockSceneProvider(),
        mock_generation=MockGenerationProvider(webhook_sender=deliver_mock_webhook),
        live_scene=live_scene,
        live_generation=live_generation,
    )
    runtime_mode = RuntimeMode(settings.generation_mode, live_available=registry.live_available)

    if generation_worker is None:
        generation_worker = GenerationWorker(
            session_factory=session_factory,
            provider=registry.generation_provider(GenerationMode.MOCK),
            clock=Clock(),
            retry_base_delay_sec=settings.retry_base_delay_sec,
            provider_poll_interval_sec=settings.provider_poll_interval_sec,
            generation_attempt_timeout_sec=settings.generation_attempt_timeout_sec,
            concurrency=settings.generation_concurrency,
            provider_registry=registry,
        )

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        await generation_worker.start()
        try:
            yield
        finally:
            await generation_worker.stop()
            for pending in list(background_tasks):
                pending.cancel()
            if live_generation is not None:
                await live_generation.aclose()
            await engine.dispose()

    application = FastAPI(title="InvzAssign Prompt-to-Animation MVP", lifespan=lifespan)
    register_error_handlers(application)
    application.state.engine = engine
    application.state.generation_worker = generation_worker
    application.state.runtime_mode = runtime_mode

    async def get_app_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

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
            # The callback URL carries this as a query token, so it lands in access logs.
            webhook_secret,
        ]
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    def _config_response() -> ConfigResponse:
        return ConfigResponse(
            generation_mode=runtime_mode.name,
            live_available=runtime_mode.live_available,
        )

    @application.get("/api/config", response_model=ConfigResponse)
    async def config() -> ConfigResponse:
        return _config_response()

    @application.put("/api/config", response_model=ConfigResponse)
    async def update_config(payload: UpdateConfigRequest) -> ConfigResponse:
        runtime_mode.set(GenerationMode[payload.generation_mode])
        return _config_response()

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
            registry.scene_provider(runtime_mode.current),
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
        if runtime_mode.current is GenerationMode.LIVE and payload.mock_scenario is not None:
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
            session,
            cut_id,
            payload,
            max_attempts=settings.generation_max_attempts,
            generation_mode=runtime_mode.name,
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
            session,
            cut_id,
            payload,
            max_attempts=settings.generation_max_attempts,
            generation_mode=runtime_mode.name,
        )
        return _job_response(job)

    @application.post("/api/webhooks/kie", response_model=WebhookAck)
    async def receive_kie_webhook(
        payload: dict[str, Any],
        session: Annotated[AsyncSession, Depends(get_app_session)],
        x_webhook_secret: Annotated[str | None, Header()] = None,
        # Live providers cannot be told to send a custom header; they only get a URL back.
        token: str | None = None,
    ) -> WebhookAck:
        verify_webhook_secret(webhook_secret, x_webhook_secret, token)
        return await apply_webhook(session, generation_worker, payload)

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
            generation_mode=runtime_mode.name,
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
            generation_mode=runtime_mode.name,
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
