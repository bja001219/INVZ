import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError
from app.main import create_app
from app.models import Base, Cut, CutImage, GenerationJob, GenerationKind, JobStatus, Scene
from app.webhooks import verify_webhook_secret

WEBHOOK_SECRET = "webhook-shared-secret"


def success_payload(
    task_id: str, url: str = "https://cdn.example/generated.png"
) -> dict[str, Any]:
    return {
        "data": {
            "taskId": task_id,
            "state": "success",
            "resultJson": json.dumps({"resultUrls": [url]}),
        }
    }


@pytest_asyncio.fixture
async def webhook_app(
    settings_factory: Callable[..., Settings],
) -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker[Any], Any]]:
    settings = settings_factory(webhook_secret=SecretStr(WEBHOOK_SECRET))
    application = create_app(settings)
    async with application.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(application.state.engine, expire_on_commit=False)
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory, application
    await application.state.engine.dispose()


async def seed_processing_job(
    factory: async_sessionmaker[Any], *, task_id: str
) -> GenerationJob:
    async with factory.begin() as session:
        scene = Scene(user_prompt="p", title="t", scenario="s")
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
        job = GenerationJob(
            cut_id=cut.id,
            kind=GenerationKind.IMAGE,
            version=1,
            status=JobStatus.PROCESSING,
            prompt=cut.image_prompt,
            attempt_count=1,
            max_attempts=3,
            external_task_id=task_id,
        )
        session.add(job)
        await session.flush()
        return job


async def image_count(factory: async_sessionmaker[Any], cut_id: Any) -> int:
    async with factory() as session:
        return (
            await session.scalar(
                select(func.count(CutImage.id)).where(CutImage.cut_id == cut_id)
            )
            or 0
        )


async def reload_job(factory: async_sessionmaker[Any], job_id: Any) -> GenerationJob:
    async with factory() as session:
        stored = await session.get(GenerationJob, job_id)
        assert stored is not None
        return stored


async def test_webhook_rejects_a_missing_or_wrong_secret(webhook_app) -> None:
    client, factory, _ = webhook_app
    job = await seed_processing_job(factory, task_id="task-1")

    missing = await client.post("/api/webhooks/kie", json=success_payload("task-1"))
    wrong = await client.post(
        "/api/webhooks/kie",
        json=success_payload("task-1"),
        headers={"X-Webhook-Secret": "nope"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert wrong.json() == {
        "code": "WEBHOOK_UNAUTHORIZED",
        "message": "Webhook credentials are invalid",
    }
    assert (await reload_job(factory, job.id)).status == JobStatus.PROCESSING


async def test_webhook_accepts_the_secret_as_a_query_token(webhook_app) -> None:
    """A real provider cannot be told to send a custom header.

    Kie is handed one callback URL and nothing else, so header-only authentication makes the
    Live webhook impossible to satisfy: every delivery would 401 and the provider would retry
    forever. The secret has to be able to travel in the URL we give it.
    """
    client, factory, _ = webhook_app
    job = await seed_processing_job(factory, task_id="task-token")

    response = await client.post(
        f"/api/webhooks/kie?token={WEBHOOK_SECRET}",
        json=success_payload("task-token"),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "applied"}
    assert (await reload_job(factory, job.id)).status == JobStatus.SUCCEEDED


async def test_webhook_rejects_a_wrong_query_token(webhook_app) -> None:
    client, factory, _ = webhook_app
    job = await seed_processing_job(factory, task_id="task-token")

    response = await client.post(
        "/api/webhooks/kie?token=nope",
        json=success_payload("task-token"),
    )

    assert response.status_code == 401
    assert response.json() == {
        "code": "WEBHOOK_UNAUTHORIZED",
        "message": "Webhook credentials are invalid",
    }
    assert (await reload_job(factory, job.id)).status == JobStatus.PROCESSING


@pytest.mark.parametrize("bad_token", ["토큰", "🔑"])
async def test_webhook_rejects_a_non_ascii_query_token(webhook_app, bad_token: str) -> None:
    """An unauthenticated caller must not be able to pick bytes that crash the route.

    `secrets.compare_digest` raises TypeError on non-ASCII `str`, and no handler translates
    that into the shared envelope, so the one channel a Live provider can actually use would
    answer a bare 500 to anything with a non-ASCII token in the URL.
    """
    client, factory, _ = webhook_app
    job = await seed_processing_job(factory, task_id="task-token")

    response = await client.post(
        "/api/webhooks/kie",
        params={"token": bad_token},
        json=success_payload("task-token"),
    )

    assert response.status_code == 401
    assert response.json() == {
        "code": "WEBHOOK_UNAUTHORIZED",
        "message": "Webhook credentials are invalid",
    }
    assert (await reload_job(factory, job.id)).status == JobStatus.PROCESSING


async def test_webhook_rejects_a_non_ascii_header_secret(webhook_app) -> None:
    """The header channel is reachable with the same bytes.

    Starlette latin-1-decodes raw header bytes instead of rejecting them, so high bytes on the
    wire arrive at the endpoint as a non-ASCII `str` exactly like the query token does.
    """
    client, factory, _ = webhook_app
    job = await seed_processing_job(factory, task_id="task-1")

    response = await client.post(
        "/api/webhooks/kie",
        json=success_payload("task-1"),
        headers=[(b"x-webhook-secret", "토큰".encode())],
    )

    assert response.status_code == 401
    assert response.json() == {
        "code": "WEBHOOK_UNAUTHORIZED",
        "message": "Webhook credentials are invalid",
    }
    assert (await reload_job(factory, job.id)).status == JobStatus.PROCESSING


def test_verify_webhook_secret_compares_a_non_ascii_secret_byte_for_byte() -> None:
    """Surviving non-ASCII means comparing it, not refusing everything that contains it."""
    verify_webhook_secret("비밀-🔑", None, "비밀-🔑")

    with pytest.raises(AppError) as rejected:
        verify_webhook_secret("비밀-🔑", None, "비밀-🔓")

    assert rejected.value.code == "WEBHOOK_UNAUTHORIZED"


async def test_webhook_is_disabled_when_no_secret_is_configured(client) -> None:
    response = await client.post(
        "/api/webhooks/kie",
        json=success_payload("task-1"),
        headers={"X-Webhook-Secret": "anything"},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "WEBHOOK_DISABLED"


async def test_webhook_completes_a_processing_job(webhook_app) -> None:
    client, factory, _ = webhook_app
    job = await seed_processing_job(factory, task_id="task-1")

    response = await client.post(
        "/api/webhooks/kie",
        json=success_payload("task-1"),
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "applied"}
    stored = await reload_job(factory, job.id)
    assert stored.status == JobStatus.SUCCEEDED
    assert await image_count(factory, job.cut_id) == 1


async def test_duplicate_webhook_delivery_creates_no_second_artifact(webhook_app) -> None:
    client, factory, _ = webhook_app
    job = await seed_processing_job(factory, task_id="task-1")
    headers = {"X-Webhook-Secret": WEBHOOK_SECRET}

    first = await client.post("/api/webhooks/kie", json=success_payload("task-1"), headers=headers)
    second = await client.post(
        "/api/webhooks/kie", json=success_payload("task-1"), headers=headers
    )

    assert first.json() == {"status": "applied"}
    assert second.status_code == 200
    assert second.json() == {"status": "ignored"}
    assert await image_count(factory, job.cut_id) == 1


async def test_polling_after_a_webhook_does_not_double_apply(webhook_app) -> None:
    client, factory, application = webhook_app
    job = await seed_processing_job(factory, task_id="mock:IMAGE:abc:1")

    await client.post(
        "/api/webhooks/kie",
        json=success_payload("mock:IMAGE:abc:1"),
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
    )
    await application.state.generation_worker.run_once()

    assert (await reload_job(factory, job.id)).status == JobStatus.SUCCEEDED
    assert await image_count(factory, job.cut_id) == 1


async def test_concurrent_webhook_and_poll_never_500_or_double_apply(webhook_app) -> None:
    """Both transports can read PROCESSING before either takes the write lock.

    The unique artifact index rejects the loser; the webhook must absorb that rather than
    surface a 500 to the provider, which would trigger an endless redelivery loop.
    """
    client, factory, application = webhook_app
    job = await seed_processing_job(factory, task_id="mock:IMAGE:race:1")

    webhook_call = client.post(
        "/api/webhooks/kie",
        json=success_payload("mock:IMAGE:race:1"),
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
    )
    response, _ = await asyncio.gather(
        webhook_call, application.state.generation_worker.run_once()
    )

    assert response.status_code == 200
    assert response.json()["status"] in {"applied", "ignored"}
    assert (await reload_job(factory, job.id)).status == JobStatus.SUCCEEDED
    assert await image_count(factory, job.cut_id) == 1


async def test_unknown_task_is_ignored_rather_than_errored(webhook_app) -> None:
    client, _, _ = webhook_app

    response = await client.post(
        "/api/webhooks/kie",
        json=success_payload("no-such-task"),
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


async def test_pending_webhook_leaves_the_job_processing(webhook_app) -> None:
    client, factory, _ = webhook_app
    job = await seed_processing_job(factory, task_id="task-1")

    response = await client.post(
        "/api/webhooks/kie",
        json={"data": {"taskId": "task-1", "state": "generating"}},
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
    )

    assert response.json() == {"status": "ignored"}
    assert (await reload_job(factory, job.id)).status == JobStatus.PROCESSING


async def test_failed_webhook_drives_the_shared_retry_path(webhook_app) -> None:
    client, factory, _ = webhook_app
    job = await seed_processing_job(factory, task_id="task-1")

    response = await client.post(
        "/api/webhooks/kie",
        json={"data": {"taskId": "task-1", "state": "fail", "retryable": True}},
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
    )

    assert response.json() == {"status": "applied"}
    stored = await reload_job(factory, job.id)
    assert stored.status == JobStatus.RETRY_WAIT
    assert stored.last_error_code == "KIE_TASK_FAILED"


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"state": "success"}},
        {"data": {"taskId": "task-1", "state": "mystery"}},
        {"data": {"taskId": "task-1", "state": "success", "resultJson": "{}"}},
    ],
)
async def test_malformed_webhook_payload_is_rejected(webhook_app, payload: dict[str, Any]) -> None:
    client, factory, _ = webhook_app
    job = await seed_processing_job(factory, task_id="task-1")

    response = await client.post(
        "/api/webhooks/kie", json=payload, headers={"X-Webhook-Secret": WEBHOOK_SECRET}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "WEBHOOK_PAYLOAD_INVALID"
    assert (await reload_job(factory, job.id)).status == JobStatus.PROCESSING


async def test_webhook_accepts_the_bare_task_record_too(webhook_app) -> None:
    client, factory, _ = webhook_app
    job = await seed_processing_job(factory, task_id="task-1")

    response = await client.post(
        "/api/webhooks/kie",
        json=success_payload("task-1")["data"],
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
    )

    assert response.json() == {"status": "applied"}
    assert (await reload_job(factory, job.id)).status == JobStatus.SUCCEEDED
