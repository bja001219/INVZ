import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.errors import AppError
from app.main import create_app
from app.models import Base, Scene
from app.providers.contracts import RetryableProviderError, SceneProvider
from app.scenes import create_scene
from app.schemas import SceneCreateRequest, SceneDraft


@pytest.fixture
def valid_scene_payload() -> dict[str, object]:
    return {
        "title": "Moon Voyage",
        "scenario": "A small crew travels to the moon.",
        "cuts": [
            {
                "order": order,
                "imagePrompt": f"Moon image {order}",
                "videoPrompt": f"Moon video {order}",
                "durationSec": 5,
            }
            for order in range(1, 7)
        ],
    }


def test_scene_draft_requires_ordered_six_five_second_cuts(
    valid_scene_payload: dict[str, object],
) -> None:
    cuts = valid_scene_payload["cuts"]
    assert isinstance(cuts, list)
    cuts[5]["durationSec"] = 6

    with pytest.raises(ValidationError):
        SceneDraft.model_validate(valid_scene_payload)


class InvalidProvider(SceneProvider):
    async def generate(self, prompt: str) -> SceneDraft:
        return {"title": "invalid"}  # type: ignore[return-value]


async def test_scene_creation_rolls_back_invalid_provider_output(session) -> None:
    with pytest.raises(AppError, match="SCENE_SCHEMA_INVALID"):
        await create_scene(session, InvalidProvider(), "moon voyage")

    assert await session.scalar(select(func.count(Scene.id))) == 0


async def test_scene_api_trims_prompt_and_returns_six_cuts(client) -> None:
    response = await client.post("/api/scenes", json={"prompt": "  moon voyage  "})

    assert response.status_code == 201
    body = response.json()
    assert body["userPrompt"] == "moon voyage"
    assert len(body["cuts"]) == 6
    assert all(cut["id"] is not None for cut in body["cuts"])
    assert {cut["durationSec"] for cut in body["cuts"]} == {5}


def test_scene_prompt_is_trimmed_before_the_two_thousand_character_limit() -> None:
    accepted = SceneCreateRequest.model_validate({"prompt": f"  {'x' * 2000}  "})

    assert accepted.prompt == "x" * 2000
    with pytest.raises(ValidationError):
        SceneCreateRequest.model_validate({"prompt": "x" * 2001})


async def test_scene_api_rejects_prompts_over_two_thousand_characters(client) -> None:
    response = await client.post("/api/scenes", json={"prompt": "x" * 2001})

    assert response.status_code == 422
    assert response.json() == {
        "code": "REQUEST_VALIDATION_FAILED",
        "message": "Request validation failed",
    }


async def test_missing_scene_uses_stable_error(client) -> None:
    response = await client.get("/api/scenes/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json() == {"code": "SCENE_NOT_FOUND", "message": "Scene not found"}


class RetryThenValidProvider(SceneProvider):
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.calls = 0

    async def generate(self, prompt: str) -> SceneDraft:
        self.calls += 1
        if self.calls < 3:
            raise RetryableProviderError("temporary")
        return SceneDraft.model_validate(self._payload)


class RecordingClock:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


async def test_scene_creation_retries_typed_transient_provider_failures(
    session, valid_scene_payload: dict[str, object]
) -> None:
    provider = RetryThenValidProvider(valid_scene_payload)
    clock = RecordingClock()

    scene = await create_scene(
        session,
        provider,
        "moon voyage",
        max_attempts=3,
        retry_base_delay_sec=0.25,
        clock=clock,  # type: ignore[arg-type]
    )

    assert scene.title == "Moon Voyage"
    assert provider.calls == 3
    assert clock.sleeps == [0.25, 0.5]


async def test_live_scene_api_uses_openai_provider(
    settings_factory, monkeypatch, valid_scene_payload: dict[str, object]
) -> None:
    async def fake_generate(self, prompt: str) -> SceneDraft:
        return SceneDraft.model_validate(valid_scene_payload)

    monkeypatch.setattr("app.main.OpenAISceneProvider.generate", fake_generate)
    app = create_app(
        settings_factory(
            generation_mode="live",
            openai_api_key="test-openai-key",
            kie_api_key="test-kie-key",
        )
    )
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/scenes", json={"prompt": "moon voyage"})
    finally:
        await app.state.engine.dispose()

    assert response.status_code == 201
