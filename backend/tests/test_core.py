import logging
import socket
import warnings
from io import StringIO
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from pytest_socket import SocketConnectBlockedError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SAWarning

from app.core.config import Settings
from app.core.logging import SecretRedactionFilter
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
    assert response.json() == {"generationMode": "MOCK"}
    assert "api" not in response.text.lower()


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

    assert len(sorted_tables) == 5
