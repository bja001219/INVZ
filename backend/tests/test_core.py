import logging
import socket
import warnings
from collections.abc import AsyncIterator, Callable
from io import StringIO
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr, ValidationError
from pytest_socket import SocketConnectBlockedError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SAWarning
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.logging import (
    SecretRedactionFilter,
    configure_secret_redaction,
    reset_secret_redaction,
)
from app.main import create_app
from app.models import Base, Cut, GenerationJob, GenerationKind, JobStatus, Scene


def test_external_network_is_blocked() -> None:
    with socket.socket() as client_socket:
        with (
            pytest.warns(UserWarning, match="A test tried to use socket.socket.connect"),
            pytest.raises(SocketConnectBlockedError),
        ):
            client_socket.connect(("192.0.2.1", 80))


async def test_config_exposes_mode_without_secrets(client) -> None:
    response = await client.get("/api/config")

    assert response.status_code == 200
    assert response.json() == {"generationMode": "MOCK", "liveAvailable": False}
    assert "api" not in response.text.lower()


async def test_switching_to_live_without_keys_is_rejected(client) -> None:
    response = await client.put("/api/config", json={"generationMode": "LIVE"})

    assert response.status_code == 409
    assert response.json() == {
        "code": "LIVE_MODE_UNAVAILABLE",
        "message": "Live mode is not configured",
    }
    assert (await client.get("/api/config")).json()["generationMode"] == "MOCK"


async def test_config_rejects_an_unknown_mode(client) -> None:
    response = await client.put("/api/config", json={"generationMode": "TURBO"})

    assert response.status_code == 422
    assert response.json() == {
        "code": "REQUEST_VALIDATION_FAILED",
        "message": "Request validation failed",
    }


@pytest_asyncio.fixture
async def live_capable_client(
    settings_factory: Callable[..., Settings],
) -> AsyncIterator[httpx.AsyncClient]:
    """Mock mode at startup, but with keys present so LIVE can be selected at runtime."""
    settings = settings_factory(
        generation_mode="mock",
        openai_api_key=SecretStr(_OPENAI_SECRET),
        kie_api_key=SecretStr(_KIE_SECRET),
    )
    application = create_app(settings)
    async with application.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    await application.state.engine.dispose()


async def test_live_becomes_selectable_once_both_keys_exist(
    live_capable_client: httpx.AsyncClient,
) -> None:
    assert (await live_capable_client.get("/api/config")).json() == {
        "generationMode": "MOCK",
        "liveAvailable": True,
    }

    switched = await live_capable_client.put("/api/config", json={"generationMode": "LIVE"})

    assert switched.status_code == 200
    assert switched.json() == {"generationMode": "LIVE", "liveAvailable": True}


async def test_mode_switch_applies_to_new_jobs_and_leaves_existing_ones_alone(
    live_capable_client: httpx.AsyncClient,
) -> None:
    scene = (await live_capable_client.post("/api/scenes", json={"prompt": "moon"})).json()
    first_cut, second_cut = scene["cuts"][0], scene["cuts"][1]
    before = await live_capable_client.post(f"/api/cuts/{first_cut['id']}/images", json={})

    await live_capable_client.put("/api/config", json={"generationMode": "LIVE"})
    after = await live_capable_client.post(f"/api/cuts/{second_cut['id']}/images", json={})

    assert before.json()["generationMode"] == "MOCK"
    assert after.json()["generationMode"] == "LIVE"
    detail = (await live_capable_client.get(f"/api/scenes/{scene['id']}")).json()
    assert detail["cuts"][0]["imageJobs"][0]["generationMode"] == "MOCK"


async def test_live_mode_rejects_a_mock_scenario_after_switching(
    live_capable_client: httpx.AsyncClient,
) -> None:
    scene = (await live_capable_client.post("/api/scenes", json={"prompt": "moon"})).json()
    await live_capable_client.put("/api/config", json={"generationMode": "LIVE"})

    response = await live_capable_client.post(
        f"/api/cuts/{scene['cuts'][0]['id']}/images", json={"mockScenario": "SUCCESS"}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "GENERATION_REQUEST_INVALID"


def test_live_mode_requires_both_keys(settings_factory) -> None:
    with pytest.raises(ValidationError, match="live mode requires"):
        settings_factory(
            generation_mode="live",
            openai_api_key=SecretStr(""),
            kie_api_key=SecretStr(""),
        )


def test_live_mode_rejects_whitespace_only_keys(settings_factory) -> None:
    with pytest.raises(ValidationError, match="live mode requires"):
        settings_factory(
            generation_mode="live",
            openai_api_key=SecretStr("   "),
            kie_api_key=SecretStr("\t"),
        )


def test_settings_repr_hides_secrets(settings_factory) -> None:
    settings: Settings = settings_factory(
        openai_api_key=SecretStr("openai-secret"),
        kie_api_key=SecretStr("kie-secret"),
    )

    assert "openai-secret" not in repr(settings)
    assert "kie-secret" not in repr(settings)


def test_log_filter_masks_secrets_and_authorization(caplog) -> None:
    logger = logging.getLogger("app.secret-test")
    redaction_filter = SecretRedactionFilter(["openai-secret", "kie-secret"])
    logger.addFilter(redaction_filter)
    caplog.set_level(logging.ERROR, logger="app.secret-test")

    try:
        logger.error("Authorization: Bearer openai-secret kie-secret")
    finally:
        logger.removeFilter(redaction_filter)

    assert "openai-secret" not in caplog.text
    assert "kie-secret" not in caplog.text
    assert "Bearer " not in caplog.text


def test_log_filter_masks_exception_tracebacks() -> None:
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(SecretRedactionFilter(["openai-secret", "kie-secret"]))
    logger = logging.getLogger("app.exception-secret-test")
    logger.addHandler(handler)
    logger.propagate = False

    try:
        raise RuntimeError("openai-secret Authorization: Bearer kie-secret")
    except RuntimeError:
        logger.exception("provider failed")
    finally:
        logger.removeHandler(handler)

    rendered = output.getvalue()
    assert "openai-secret" not in rendered
    assert "kie-secret" not in rendered
    assert "Bearer " not in rendered


def uvicorn_shaped_logger(name: str) -> tuple[logging.Logger, StringIO, logging.Handler]:
    """A logger configured the way Uvicorn configures its own: private handler, no propagation.

    Records logged here never reach the root logger, so a filter installed on root alone --
    which is what the app used to do -- redacts nothing that Uvicorn writes.
    """
    output = StringIO()
    handler = logging.StreamHandler(output)
    logger = logging.getLogger(name)
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, output, handler


def test_secret_redaction_reaches_loggers_that_bypass_the_root_handlers() -> None:
    logger, output, handler = uvicorn_shaped_logger("test.uvicorn.access")
    try:
        configure_secret_redaction(["openai-secret", "kie-secret"])
        logger.info('GET /x?token=%s "Authorization: Bearer kie-secret"', "openai-secret")
    finally:
        reset_secret_redaction()
        logger.removeHandler(handler)

    rendered = output.getvalue()
    assert "openai-secret" not in rendered
    assert "kie-secret" not in rendered
    assert "[REDACTED]" in rendered


def test_secret_redaction_reaches_handlers_installed_after_configuration() -> None:
    """Uvicorn's --reload worker and any late logging setup add handlers after startup."""
    try:
        configure_secret_redaction(["openai-secret"])
        logger, output, handler = uvicorn_shaped_logger("test.late.handler")
        logger.info("leaked openai-secret")
    finally:
        reset_secret_redaction()
        logger.removeHandler(handler)

    assert "openai-secret" not in output.getvalue()


def test_resetting_redaction_restores_plain_logging() -> None:
    configure_secret_redaction(["openai-secret"])
    reset_secret_redaction()
    logger, output, handler = uvicorn_shaped_logger("test.reset")
    try:
        logger.info("openai-secret")
    finally:
        logger.removeHandler(handler)

    assert "openai-secret" in output.getvalue()


async def test_cors_allows_only_frontend_origin(client) -> None:
    allowed = await client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    denied = await client.options(
        "/health",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in denied.headers


async def test_mock_media_is_served(client) -> None:
    image = await client.get("/media/mock/cut-image.png")
    video = await client.get("/media/mock/cut-video.mp4")

    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert video.status_code == 200
    assert video.headers["content-type"] == "video/mp4"
    assert b"ftyp" in video.content[:32]


async def test_missing_resource_uses_stable_error(client) -> None:
    response = await client.get("/missing-route")

    assert response.status_code == 404
    assert response.json() == {"code": "ROUTE_NOT_FOUND", "message": "Route not found"}


async def test_cut_constraints_reject_duplicate_order(session) -> None:
    scene = Scene(user_prompt="prompt", title="title", scenario="scenario")
    session.add(scene)
    await session.flush()
    session.add_all(
        [
            Cut(
                scene_id=scene.id,
                order=1,
                image_prompt="image one",
                video_prompt="video one",
                duration_sec=5,
            ),
            Cut(
                scene_id=scene.id,
                order=1,
                image_prompt="image duplicate",
                video_prompt="video duplicate",
                duration_sec=5,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_active_generation_is_unique_per_cut_and_kind(session) -> None:
    scene = Scene(user_prompt="prompt", title="title", scenario="scenario")
    session.add(scene)
    await session.flush()
    cut = Cut(
        scene_id=scene.id,
        order=1,
        image_prompt="image",
        video_prompt="video",
        duration_sec=5,
    )
    session.add(cut)
    await session.flush()
    session.add_all(
        [
            GenerationJob(
                cut_id=cut.id,
                kind=GenerationKind.IMAGE,
                version=1,
                status=JobStatus.QUEUED,
                prompt="image",
            ),
            GenerationJob(
                cut_id=cut.id,
                kind=GenerationKind.IMAGE,
                version=2,
                status=JobStatus.RETRY_WAIT,
                prompt="image",
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_sqlite_foreign_keys_are_enabled_in_tests(session) -> None:
    enabled = await session.scalar(text("PRAGMA foreign_keys"))

    assert enabled == 1


@pytest.mark.parametrize(
    ("kind", "status"),
    [("AUDIO", "QUEUED"), ("IMAGE", "UNKNOWN")],
)
async def test_generation_kind_and_status_reject_unknown_values(
    session, kind: str, status: str
) -> None:
    scene = Scene(user_prompt="prompt", title="title", scenario="scenario")
    session.add(scene)
    await session.flush()
    cut = Cut(
        scene_id=scene.id,
        order=1,
        image_prompt="image",
        video_prompt="video",
        duration_sec=5,
    )
    session.add(cut)
    await session.flush()
    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                """
                INSERT INTO generation_jobs (
                    id, cut_id, kind, version, status, prompt,
                    attempt_count, max_attempts
                ) VALUES (
                    :id, :cut_id, :kind, 1, :status, 'prompt', 0, 3
                )
                """
            ),
            {
                "id": uuid4().hex,
                "cut_id": cut.id.hex,
                "kind": kind,
                "status": status,
            },
        )


def test_metadata_foreign_keys_have_a_resolvable_creation_order() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        sorted_tables = list(Base.metadata.sorted_tables)

    assert len(sorted_tables) == 6


# Deliberately not shaped like a real `sk-…` key, so the repository secret scan documented
# in the README stays clean while still exercising a long, distinctive secret value.
_OPENAI_SECRET = "openai-live-secret-0123456789abcdef"
_KIE_SECRET = "kie-live-secret-0123456789abcdef"


def _credential_echoing_error(secret: str) -> httpx.Response:
    """A provider failure whose body and headers echo the caller's credentials back."""
    return httpx.Response(
        401,
        json={
            "code": 401,
            "msg": f"Invalid credentials: Bearer {secret}",
            "error": {"message": f"Incorrect API key provided: {secret}"},
        },
        headers={"x-echoed-authorization": f"Bearer {secret}"},
    )


@pytest_asyncio.fixture
async def live_application(
    settings_factory: Callable[..., Settings],
) -> AsyncIterator[tuple[FastAPI, Settings]]:
    settings = settings_factory(
        generation_mode="live",
        openai_api_key=SecretStr(_OPENAI_SECRET),
        kie_api_key=SecretStr(_KIE_SECRET),
    )
    application = create_app(settings)
    async with application.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield application, settings
    finally:
        await application.state.engine.dispose()


async def _trigger_stubbed_provider_failure(application: FastAPI, respx_mock) -> str:
    """Fail one Scene call and one generation job, then read every user-facing surface."""
    respx_mock.post("https://api.openai.com/v1/responses").mock(
        return_value=_credential_echoing_error(_OPENAI_SECRET)
    )
    respx_mock.post("https://api.kie.ai/api/v1/jobs/createTask").mock(
        return_value=_credential_echoing_error(_KIE_SECRET)
    )

    factory = async_sessionmaker(application.state.engine, expire_on_commit=False)
    async with factory.begin() as session:
        scene = Scene(user_prompt="moon voyage", title="Moon Voyage", scenario="Scenario")
        session.add(scene)
        await session.flush()
        cut = Cut(
            scene_id=scene.id,
            order=1,
            image_prompt="image prompt",
            video_prompt="video prompt",
            duration_sec=5,
        )
        session.add(cut)
        await session.flush()
        scene_id, cut_id = scene.id, cut.id

    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        scene_failure = await client.post("/api/scenes", json={"prompt": "moon voyage"})
        accepted = await client.post(f"/api/cuts/{cut_id}/images", json={})
        await application.state.generation_worker.run_once()
        detail = await client.get(f"/api/scenes/{scene_id}")

    assert scene_failure.status_code == 502
    assert scene_failure.json() == {
        "code": "SCENE_PROVIDER_FAILED",
        "message": "Scene provider failed",
    }
    assert accepted.status_code == 202
    failed_job = detail.json()["cuts"][0]["imageJobs"][0]
    assert failed_job["status"] == "FAILED"
    # startswith, not equality: a leaked provider body must reach the secret assertions
    # below instead of failing this precondition first.
    assert failed_job["lastErrorCode"].startswith("KIE_REQUEST_FAILED")
    return "\n".join([scene_failure.text, accepted.text, detail.text])


@pytest.mark.parametrize("secret_name", ["openai_api_key", "kie_api_key"])
async def test_provider_error_never_serializes_configured_secrets(
    live_application: tuple[FastAPI, Settings], caplog, respx_mock, secret_name: str
) -> None:
    application, settings = live_application
    secret_field: SecretStr = getattr(settings, secret_name)
    secret = secret_field.get_secret_value()

    responses = await _trigger_stubbed_provider_failure(application, respx_mock)

    serialized = responses + "\n" + caplog.text + "\n" + repr(settings)
    assert secret
    assert secret not in serialized
    assert "Authorization" not in serialized
    assert "Bearer " not in serialized
