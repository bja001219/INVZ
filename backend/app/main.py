from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import GenerationMode, Settings, get_settings
from app.core.db import build_engine
from app.core.errors import register_error_handlers
from app.core.logging import configure_secret_redaction
from app.providers.mock import MockSceneProvider
from app.providers.openai_scene import OpenAISceneProvider
from app.scenes import create_scene, get_scene
from app.schemas import ConfigResponse, SceneCreateRequest, SceneResponse


def create_app(settings: Settings) -> FastAPI:
    application = FastAPI(title="InvzAssign Prompt-to-Animation MVP")
    register_error_handlers(application)
    engine = build_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    application.state.engine = engine

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

    return application


app = create_app(get_settings())
