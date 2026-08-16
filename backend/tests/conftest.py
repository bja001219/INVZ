from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.db import build_engine
from app.core.logging import SecretRedactionFilter
from app.main import create_app
from app.models import Base


def build_scene_payload() -> dict[str, object]:
    """A SceneDraft payload in provider wire format, shared by scene and provider tests."""
    return {
        "title": "Moon Voyage",
        "scenario": "A small crew travels to the moon.",
        "characterProfiles": [
            {
                "name": "Mina",
                "role": "female lead high-school student",
                "ageRange": "17",
                "hairColor": "dark brown",
                "hairStyle": "long straight",
                "outfit": "navy sailor uniform with a red ribbon",
                "build": "slim",
                "faceImpression": "soft calm expression",
                "signatureProp": "a stack of library books",
            },
            {
                "name": "Jun",
                "role": "male lead high-school student",
                "ageRange": "17",
                "hairColor": "black",
                "hairStyle": "short messy",
                "outfit": "grey blazer uniform",
                "build": "tall lean",
                "faceImpression": "bright open smile",
                "signatureProp": "a worn canvas backpack",
            },
        ],
        "cuts": [
            {
                "order": order,
                "shotDescription": f"Moon shot {order}, wide framing of the two leads",
                "videoMotion": f"gentle camera drift {order}",
                "durationSec": 5,
            }
            for order in range(1, 7)
        ],
    }


@pytest.fixture
def valid_scene_payload() -> dict[str, object]:
    return build_scene_payload()


@pytest.fixture
def settings_factory(tmp_path: Path) -> Callable[..., Settings]:
    def factory(**overrides: object) -> Settings:
        values: dict[str, object] = {
            "generation_mode": "mock",
            "openai_api_key": SecretStr(""),
            "kie_api_key": SecretStr(""),
            "database_url": f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
            "frontend_origin": "http://localhost:5173",
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return factory


@pytest.fixture
def settings(settings_factory: Callable[..., Settings]) -> Settings:
    return settings_factory()


@pytest.fixture
def secret_filter() -> Callable[..., SecretRedactionFilter]:
    def factory(*, openai: str, kie: str) -> SecretRedactionFilter:
        return SecretRedactionFilter([openai, kie])

    return factory


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    application = create_app(settings)
    async with application.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    await application.state.engine.dispose()


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'models.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session

    await engine.dispose()
