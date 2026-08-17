import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.main import create_app
from app.models import (
    Cut,
    CutImage,
    CutVideo,
    GenerationJob,
    GenerationKind,
    JobStatus,
    Scene,
)
from app.scenes import get_scene


async def make_cut(session: AsyncSession) -> Cut:
    async with session.begin():
        scene = Scene(user_prompt="prompt", title="title", scenario="scenario")
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
    return cut


async def make_succeeded_image(session: AsyncSession, cut: Cut, *, version: int) -> CutImage:
    async with session.begin():
        job = GenerationJob(
            cut_id=cut.id,
            kind=GenerationKind.IMAGE,
            version=version,
            status=JobStatus.SUCCEEDED,
            prompt=cut.image_prompt,
        )
        session.add(job)
        await session.flush()
        image = CutImage(
            cut_id=cut.id,
            generation_job_id=job.id,
            url=f"/image-{version}.png",
            input_prompt=cut.image_prompt,
        )
        session.add(image)
        await session.flush()
    return image


async def make_succeeded_video(
    session: AsyncSession, cut: Cut, image: CutImage, *, version: int
) -> CutVideo:
    async with session.begin():
        job = GenerationJob(
            cut_id=cut.id,
            kind=GenerationKind.VIDEO,
            version=version,
            status=JobStatus.SUCCEEDED,
            prompt=cut.video_prompt,
            source_image_id=image.id,
        )
        session.add(job)
        await session.flush()
        video = CutVideo(
            cut_id=cut.id,
            cut_image_id=image.id,
            generation_job_id=job.id,
            url=f"/video-{version}.mp4",
            input_prompt=cut.video_prompt,
        )
        session.add(video)
        await session.flush()
    return video


def mock_request():
    from app.schemas import CreateGenerationRequest

    return CreateGenerationRequest(mockScenario="SUCCESS")


async def test_active_job_blocks_regeneration(session: AsyncSession) -> None:
    import app.schemas as schemas

    assert hasattr(schemas, "CreateGenerationRequest")
    from app.generations import create_image_job

    cut = await make_cut(session)
    cut_id = cut.id

    first = await create_image_job(session, cut_id, mock_request(), max_attempts=4)
    first_version = first.version

    with pytest.raises(AppError, match="GENERATION_ALREADY_ACTIVE"):
        await create_image_job(session, cut_id, mock_request(), max_attempts=4)

    assert first_version == 1
    stored = await session.scalar(select(GenerationJob).where(GenerationJob.cut_id == cut_id))
    assert stored is not None
    assert stored.status is JobStatus.QUEUED
    assert stored.max_attempts == 4


async def test_terminal_job_allows_next_image_version(session: AsyncSession) -> None:
    from app.generations import create_image_job

    cut = await make_cut(session)
    first = await create_image_job(session, cut.id, mock_request())
    async with session.begin():
        first.status = JobStatus.FAILED

    second = await create_image_job(session, cut.id, mock_request())

    assert second.version == 2
    assert second.id != first.id


async def test_video_captures_selected_successful_source_image(session: AsyncSession) -> None:
    from app.generations import create_video_job

    cut = await make_cut(session)
    image = await make_succeeded_image(session, cut, version=1)
    async with session.begin():
        cut.selected_image_id = image.id

    job = await create_video_job(session, cut.id, mock_request(), max_attempts=5)

    assert job.source_image_id == image.id
    assert job.prompt == "video prompt"
    assert job.max_attempts == 5


async def test_video_requires_selected_successful_image(session: AsyncSession) -> None:
    from app.generations import create_video_job

    cut = await make_cut(session)

    with pytest.raises(AppError, match="SELECTED_IMAGE_REQUIRED"):
        await create_video_job(session, cut.id, mock_request())


async def test_changing_image_clears_incompatible_video(session: AsyncSession) -> None:
    from app.generations import select_image

    cut = await make_cut(session)
    image_one = await make_succeeded_image(session, cut, version=1)
    image_two = await make_succeeded_image(session, cut, version=2)
    video = await make_succeeded_video(session, cut, image_one, version=1)
    async with session.begin():
        cut.selected_image_id = image_one.id
        cut.selected_video_id = video.id

    await select_image(session, cut.id, image_two.id)
    await session.refresh(cut)

    assert cut.selected_image_id == image_two.id
    assert cut.selected_video_id is None


async def test_rejects_video_from_nonselected_image(session: AsyncSession) -> None:
    from app.generations import select_video

    cut = await make_cut(session)
    old_image = await make_succeeded_image(session, cut, version=1)
    selected_image = await make_succeeded_image(session, cut, version=2)
    old_video = await make_succeeded_video(session, cut, old_image, version=1)
    async with session.begin():
        cut.selected_image_id = selected_image.id

    with pytest.raises(AppError, match="VIDEO_SOURCE_MISMATCH"):
        await select_video(session, cut.id, old_video.id)


async def test_selection_rejects_artifact_from_another_cut(session: AsyncSession) -> None:
    from app.generations import select_image

    first_cut = await make_cut(session)
    second_cut = await make_cut(session)
    image = await make_succeeded_image(session, second_cut, version=1)

    with pytest.raises(AppError, match="IMAGE_NOT_FOUND"):
        await select_image(session, first_cut.id, image.id)


async def test_image_selection_rejects_cross_wired_producing_jobs(session: AsyncSession) -> None:
    from app.generations import select_image

    cut = await make_cut(session)
    other_cut = await make_cut(session)
    cut_id = cut.id
    async with session.begin():
        other_cut_job = GenerationJob(
            cut_id=other_cut.id,
            kind=GenerationKind.IMAGE,
            version=1,
            status=JobStatus.SUCCEEDED,
            prompt="other image prompt",
        )
        wrong_kind_job = GenerationJob(
            cut_id=cut.id,
            kind=GenerationKind.VIDEO,
            version=1,
            status=JobStatus.SUCCEEDED,
            prompt=cut.video_prompt,
        )
        session.add_all([other_cut_job, wrong_kind_job])
        await session.flush()
        session.add_all(
            [
                CutImage(
                    cut_id=cut.id,
                    generation_job_id=other_cut_job.id,
                    url="/cross-cut.png",
                    input_prompt=cut.image_prompt,
                ),
                CutImage(
                    cut_id=cut.id,
                    generation_job_id=wrong_kind_job.id,
                    url="/wrong-kind.png",
                    input_prompt=cut.image_prompt,
                ),
            ]
        )
        await session.flush()
        images = list(
            await session.scalars(
                select(CutImage).where(CutImage.cut_id == cut_id).order_by(CutImage.url)
            )
        )
        image_ids = [image.id for image in images]

    for image_id in image_ids:
        with pytest.raises(AppError, match="IMAGE_NOT_FOUND"):
            await select_image(session, cut_id, image_id)


async def test_video_creation_rejects_cross_wired_selected_images(session: AsyncSession) -> None:
    from app.generations import create_video_job

    cut = await make_cut(session)
    other_cut = await make_cut(session)
    cut_id = cut.id
    async with session.begin():
        other_cut_job = GenerationJob(
            cut_id=other_cut.id,
            kind=GenerationKind.IMAGE,
            version=1,
            status=JobStatus.SUCCEEDED,
            prompt="other image prompt",
        )
        wrong_kind_job = GenerationJob(
            cut_id=cut.id,
            kind=GenerationKind.VIDEO,
            version=1,
            status=JobStatus.SUCCEEDED,
            prompt=cut.video_prompt,
        )
        session.add_all([other_cut_job, wrong_kind_job])
        await session.flush()
        cross_cut_image = CutImage(
            cut_id=cut.id,
            generation_job_id=other_cut_job.id,
            url="/cross-cut.png",
            input_prompt=cut.image_prompt,
        )
        wrong_kind_image = CutImage(
            cut_id=cut.id,
            generation_job_id=wrong_kind_job.id,
            url="/wrong-kind.png",
            input_prompt=cut.image_prompt,
        )
        session.add_all([cross_cut_image, wrong_kind_image])
        await session.flush()
        image_ids = [cross_cut_image.id, wrong_kind_image.id]

    for image_id in image_ids:
        async with session.begin():
            cut.selected_image_id = image_id
        with pytest.raises(AppError, match="SELECTED_IMAGE_REQUIRED"):
            await create_video_job(session, cut_id, mock_request())


async def test_video_selection_rejects_cross_wired_producing_jobs(session: AsyncSession) -> None:
    from app.generations import select_video

    cut = await make_cut(session)
    other_cut = await make_cut(session)
    cut_id = cut.id
    selected_image = await make_succeeded_image(session, cut, version=1)
    other_image = await make_succeeded_image(session, cut, version=2)
    async with session.begin():
        cut.selected_image_id = selected_image.id
        cross_cut_job = GenerationJob(
            cut_id=other_cut.id,
            kind=GenerationKind.VIDEO,
            version=1,
            status=JobStatus.SUCCEEDED,
            prompt="other video prompt",
            source_image_id=selected_image.id,
        )
        wrong_kind_job = GenerationJob(
            cut_id=cut.id,
            kind=GenerationKind.IMAGE,
            version=3,
            status=JobStatus.SUCCEEDED,
            prompt=cut.image_prompt,
            source_image_id=selected_image.id,
        )
        wrong_source_job = GenerationJob(
            cut_id=cut.id,
            kind=GenerationKind.VIDEO,
            version=1,
            status=JobStatus.SUCCEEDED,
            prompt=cut.video_prompt,
            source_image_id=other_image.id,
        )
        session.add_all([cross_cut_job, wrong_kind_job, wrong_source_job])
        await session.flush()
        videos = [
            CutVideo(
                cut_id=cut.id,
                cut_image_id=selected_image.id,
                generation_job_id=job.id,
                url=f"/{job.id}.mp4",
                input_prompt=cut.video_prompt,
            )
            for job in [cross_cut_job, wrong_kind_job, wrong_source_job]
        ]
        session.add_all(videos)
        await session.flush()
        video_ids = [video.id for video in videos]

    assert all(video_id is not None for video_id in video_ids)
    for video_id in video_ids:
        with pytest.raises(AppError, match="VIDEO_NOT_FOUND"):
            await select_video(session, cut_id, video_id)


async def test_generation_does_not_mislabel_check_constraint_as_active_conflict(
    session: AsyncSession,
) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.generations import create_image_job

    cut = await make_cut(session)

    with pytest.raises(IntegrityError):
        await create_image_job(session, cut.id, mock_request(), max_attempts=0)


async def test_scene_detail_returns_newest_history_and_lineage(session: AsyncSession) -> None:
    cut = await make_cut(session)
    older_image = await make_succeeded_image(session, cut, version=1)
    newer_image = await make_succeeded_image(session, cut, version=2)
    video = await make_succeeded_video(session, cut, newer_image, version=1)
    async with session.begin():
        cut.selected_image_id = newer_image.id
        cut.selected_video_id = video.id

    scene = await get_scene(session, cut.scene_id)
    detail = scene.cuts[0]

    assert detail.id == cut.id
    assert detail.selected_image_id == newer_image.id
    assert detail.selected_video_id == video.id
    assert [job.version for job in detail.image_jobs] == [2, 1]
    assert [image.id for image in detail.images] == [newer_image.id, older_image.id]
    assert detail.video_jobs[0].source_image_id == newer_image.id
    assert detail.videos[0].cut_image_id == newer_image.id


async def make_scene_cuts(session: AsyncSession, *, orders: tuple[int, ...]) -> list[Cut]:
    async with session.begin():
        scene = Scene(user_prompt="prompt", title="title", scenario="scenario")
        session.add(scene)
        await session.flush()
        cuts = [
            Cut(
                scene_id=scene.id,
                order=order,
                image_prompt=f"image prompt {order}",
                video_prompt=f"video prompt {order}",
                duration_sec=5,
            )
            for order in orders
        ]
        session.add_all(cuts)
        await session.flush()
    return cuts


async def make_queued_image_job(session: AsyncSession, cut: Cut) -> GenerationJob:
    async with session.begin():
        job = GenerationJob(
            cut_id=cut.id,
            kind=GenerationKind.IMAGE,
            version=1,
            status=JobStatus.QUEUED,
            prompt=cut.image_prompt,
        )
        session.add(job)
        await session.flush()
    return job


async def test_scene_detail_marks_image_jobs_waiting_for_the_scene_anchor(
    session: AsyncSession,
) -> None:
    """A gated job looks idle. The response has to say why it is not moving."""
    first, second = await make_scene_cuts(session, orders=(1, 2))
    await make_queued_image_job(session, second)

    scene = await get_scene(session, first.scene_id)

    assert scene.cuts[1].image_jobs[0].waiting_for_anchor is True


async def test_scene_detail_clears_the_anchor_wait_once_cut_one_has_an_image(
    session: AsyncSession,
) -> None:
    first, second = await make_scene_cuts(session, orders=(1, 2))
    anchor_image = await make_succeeded_image(session, first, version=1)
    async with session.begin():
        first.selected_image_id = anchor_image.id
    await make_queued_image_job(session, second)

    scene = await get_scene(session, first.scene_id)

    assert scene.cuts[1].image_jobs[0].waiting_for_anchor is False


async def test_scene_detail_never_marks_the_anchor_cut_as_waiting(
    session: AsyncSession,
) -> None:
    first, _ = await make_scene_cuts(session, orders=(1, 2))
    await make_queued_image_job(session, first)

    scene = await get_scene(session, first.scene_id)

    assert scene.cuts[0].image_jobs[0].waiting_for_anchor is False


async def _insert_cut_for_client(client: httpx.AsyncClient) -> Cut:
    factory = async_sessionmaker(client._transport.app.state.engine, expire_on_commit=False)  # type: ignore[attr-defined]
    async with factory() as session:
        return await make_cut(session)


@pytest_asyncio.fixture
async def generation_cut(client: httpx.AsyncClient) -> AsyncIterator[Cut]:
    yield await _insert_cut_for_client(client)


async def test_concurrent_image_requests_create_one_active_job(
    client: httpx.AsyncClient, generation_cut: Cut
) -> None:
    factory = async_sessionmaker(client._transport.app.state.engine, expire_on_commit=False)  # type: ignore[attr-defined]
    async with factory.begin() as session:
        session.add(
            GenerationJob(
                cut_id=generation_cut.id,
                kind=GenerationKind.IMAGE,
                version=4,
                status=JobStatus.FAILED,
                prompt="image prompt",
            )
        )
    responses = await asyncio.gather(
        client.post(f"/api/cuts/{generation_cut.id}/images", json={"mockScenario": "SUCCESS"}),
        client.post(f"/api/cuts/{generation_cut.id}/images", json={"mockScenario": "SUCCESS"}),
    )
    async with factory() as session:
        active_count = await session.scalar(
            select(func.count(GenerationJob.id)).where(
                GenerationJob.cut_id == generation_cut.id,
                GenerationJob.kind == GenerationKind.IMAGE,
                GenerationJob.status == JobStatus.QUEUED,
            )
        )
        jobs = list(
            await session.scalars(
                select(GenerationJob)
                .where(
                    GenerationJob.cut_id == generation_cut.id,
                    GenerationJob.kind == GenerationKind.IMAGE,
                )
                .order_by(GenerationJob.version)
            )
        )

    assert sorted(response.status_code for response in responses) == [202, 409]
    assert active_count == 1
    assert [(job.version, job.status) for job in jobs] == [
        (4, JobStatus.FAILED),
        (5, JobStatus.QUEUED),
    ]


async def test_generation_route_returns_queued_job_and_uses_mock_scenario(
    client: httpx.AsyncClient, generation_cut: Cut
) -> None:
    response = await client.post(
        f"/api/cuts/{generation_cut.id}/images", json={"mockScenario": "ALWAYS_FAIL"}
    )

    assert response.status_code == 202
    assert response.json() == {
        "id": response.json()["id"],
        "kind": "IMAGE",
        "version": 1,
        "status": "QUEUED",
        "prompt": "image prompt",
        "generationMode": "MOCK",
        "sourceImageId": None,
        "referenceImageId": None,
        "batchId": None,
        "waitingForAnchor": False,
        "attemptCount": 0,
        "maxAttempts": 3,
        "nextRunAt": None,
        "lastErrorCode": None,
        "lastErrorMessage": None,
        "mockScenario": "ALWAYS_FAIL",
    }


async def test_video_generation_endpoint_uses_selected_successful_image(
    client: httpx.AsyncClient, generation_cut: Cut
) -> None:
    factory = async_sessionmaker(client._transport.app.state.engine, expire_on_commit=False)  # type: ignore[attr-defined]
    async with factory() as session:
        image = await make_succeeded_image(session, generation_cut, version=1)
        async with session.begin():
            cut = await session.get(Cut, generation_cut.id)
            assert cut is not None
            cut.selected_image_id = image.id

    response = await client.post(f"/api/cuts/{generation_cut.id}/videos", json={})

    assert response.status_code == 202
    assert response.json()["kind"] == "VIDEO"
    assert response.json()["sourceImageId"] == str(image.id)
    assert response.json()["prompt"] == "video prompt"


async def test_image_selection_endpoint_returns_selected_image(
    client: httpx.AsyncClient, generation_cut: Cut
) -> None:
    factory = async_sessionmaker(client._transport.app.state.engine, expire_on_commit=False)  # type: ignore[attr-defined]
    async with factory() as session:
        image = await make_succeeded_image(session, generation_cut, version=1)

    response = await client.put(
        f"/api/cuts/{generation_cut.id}/selected-image", json={"imageId": str(image.id)}
    )

    assert response.status_code == 200
    assert response.json()["cuts"][0]["selectedImageId"] == str(image.id)
    assert response.json()["cuts"][0]["selectedVideoId"] is None


async def test_video_selection_endpoint_returns_selected_video(
    client: httpx.AsyncClient, generation_cut: Cut
) -> None:
    factory = async_sessionmaker(client._transport.app.state.engine, expire_on_commit=False)  # type: ignore[attr-defined]
    async with factory() as session:
        image = await make_succeeded_image(session, generation_cut, version=1)
        video = await make_succeeded_video(session, generation_cut, image, version=1)
        async with session.begin():
            cut = await session.get(Cut, generation_cut.id)
            assert cut is not None
            cut.selected_image_id = image.id

    response = await client.put(
        f"/api/cuts/{generation_cut.id}/selected-video", json={"videoId": str(video.id)}
    )

    assert response.status_code == 200
    assert response.json()["cuts"][0]["selectedVideoId"] == str(video.id)


async def test_image_selection_endpoint_rejects_unsuccessful_artifact(
    client: httpx.AsyncClient, generation_cut: Cut
) -> None:
    factory = async_sessionmaker(client._transport.app.state.engine, expire_on_commit=False)  # type: ignore[attr-defined]
    async with factory.begin() as session:
        job = GenerationJob(
            cut_id=generation_cut.id,
            kind=GenerationKind.IMAGE,
            version=1,
            status=JobStatus.QUEUED,
            prompt="image prompt",
        )
        session.add(job)
        await session.flush()
        image = CutImage(
            cut_id=generation_cut.id,
            generation_job_id=job.id,
            url="/unfinished.png",
            input_prompt="image prompt",
        )
        session.add(image)
        await session.flush()
        image_id = image.id

    response = await client.put(
        f"/api/cuts/{generation_cut.id}/selected-image", json={"imageId": str(image_id)}
    )

    assert response.status_code == 404
    assert response.json() == {"code": "IMAGE_NOT_FOUND", "message": "Image not found"}


async def test_job_records_the_requested_generation_mode(session: AsyncSession) -> None:
    from app.generations import create_image_job
    from app.schemas import CreateGenerationRequest

    cut = await make_cut(session)

    job = await create_image_job(
        session, cut.id, CreateGenerationRequest(), generation_mode="LIVE"
    )

    assert job.generation_mode == "LIVE"


async def test_scene_detail_exposes_mode_and_lineage_fields(
    client: httpx.AsyncClient, generation_cut: Cut
) -> None:
    await client.post(f"/api/cuts/{generation_cut.id}/images", json={})

    body = (await client.get(f"/api/scenes/{generation_cut.scene_id}")).json()

    job = body["cuts"][0]["imageJobs"][0]
    assert job["generationMode"] == "MOCK"
    assert job["referenceImageId"] is None
    assert job["batchId"] is None


async def test_active_generation_endpoint_returns_exact_conflict_envelope(
    client: httpx.AsyncClient, generation_cut: Cut
) -> None:
    first = await client.post(f"/api/cuts/{generation_cut.id}/images", json={})
    second = await client.post(f"/api/cuts/{generation_cut.id}/images", json={})

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json() == {
        "code": "GENERATION_ALREADY_ACTIVE",
        "message": "A generation job is already active",
    }


async def test_live_generation_rejects_mock_scenario(settings_factory, tmp_path) -> None:
    app = create_app(
        settings_factory(
            generation_mode="live",
            openai_api_key="test-openai-key",
            kie_api_key="test-kie-key",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'live.db'}",
        )
    )
    from app.models import Base

    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as live_client:
            cut = await _insert_cut_for_client(live_client)
            response = await live_client.post(
                f"/api/cuts/{cut.id}/images", json={"mockScenario": "SUCCESS"}
            )
    finally:
        await app.state.engine.dispose()

    assert response.status_code == 422
    assert response.json() == {
        "code": "GENERATION_REQUEST_INVALID",
        "message": "mockScenario is available only in Mock mode",
    }


async def test_live_generation_rejects_unknown_mock_scenario(settings_factory, tmp_path) -> None:
    app = create_app(
        settings_factory(
            generation_mode="live",
            openai_api_key="test-openai-key",
            kie_api_key="test-kie-key",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'live-unknown.db'}",
        )
    )
    from app.models import Base

    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as live_client:
            cut = await _insert_cut_for_client(live_client)
            response = await live_client.post(
                f"/api/cuts/{cut.id}/images", json={"mockScenario": "UNKNOWN"}
            )
    finally:
        await app.state.engine.dispose()

    assert response.status_code == 422
    assert response.json() == {
        "code": "GENERATION_REQUEST_INVALID",
        "message": "mockScenario is available only in Mock mode",
    }


async def test_mock_generation_rejects_unknown_mock_scenario_with_stable_validation_error(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/cuts/00000000-0000-0000-0000-000000000000/images",
        json={"mockScenario": "UNKNOWN"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "REQUEST_VALIDATION_FAILED",
        "message": "Request validation failed",
    }
