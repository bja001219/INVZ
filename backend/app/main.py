from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_secret_redaction
from app.schemas import ConfigResponse


def create_app(settings: Settings) -> FastAPI:
    application = FastAPI(title="InvzAssign Prompt-to-Animation MVP")
    register_error_handlers(application)

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

    return application


app = create_app(get_settings())
