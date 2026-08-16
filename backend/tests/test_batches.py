from typing import Any

import httpx
import pytest_asyncio


@pytest_asyncio.fixture
async def scene(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post("/api/scenes", json={"prompt": "moon voyage"})
    assert response.status_code == 201
    return response.json()


async def test_image_batch_creates_one_job_per_cut(
    client: httpx.AsyncClient, scene: dict[str, Any]
) -> None:
    response = await client.post(f"/api/scenes/{scene['id']}/images", json={})

    assert response.status_code == 202
    body = response.json()
    assert len(body["createdJobIds"]) == 6
    assert body["skipped"] == []
    assert body["kind"] == "IMAGE"


async def test_batch_jobs_all_carry_the_same_batch_id(
    client: httpx.AsyncClient, scene: dict[str, Any]
) -> None:
    batch_id = (await client.post(f"/api/scenes/{scene['id']}/images", json={})).json()["id"]

    detail = (await client.get(f"/api/scenes/{scene['id']}")).json()

    batch_ids = {cut["imageJobs"][0]["batchId"] for cut in detail["cuts"]}
    assert batch_ids == {batch_id}


async def test_image_batch_skips_cuts_that_already_have_an_active_job(
    client: httpx.AsyncClient, scene: dict[str, Any]
) -> None:
    busy_cut = scene["cuts"][0]
    await client.post(f"/api/cuts/{busy_cut['id']}/images", json={})

    body = (await client.post(f"/api/scenes/{scene['id']}/images", json={})).json()

    assert len(body["createdJobIds"]) == 5
    assert body["skipped"] == [
        {"cutId": busy_cut["id"], "reason": "GENERATION_ALREADY_ACTIVE"}
    ]


async def test_video_batch_skips_cuts_without_a_selected_image(
    client: httpx.AsyncClient, scene: dict[str, Any]
) -> None:
    body = (await client.post(f"/api/scenes/{scene['id']}/videos", json={})).json()

    assert body["createdJobIds"] == []
    assert [entry["reason"] for entry in body["skipped"]] == ["SELECTED_IMAGE_REQUIRED"] * 6


async def test_batch_forwards_the_mock_scenario_to_every_job(
    client: httpx.AsyncClient, scene: dict[str, Any]
) -> None:
    await client.post(f"/api/scenes/{scene['id']}/images", json={"mockScenario": "ALWAYS_FAIL"})

    detail = (await client.get(f"/api/scenes/{scene['id']}")).json()

    assert {cut["imageJobs"][0]["mockScenario"] for cut in detail["cuts"]} == {"ALWAYS_FAIL"}


async def test_batch_on_a_missing_scene_uses_the_stable_error(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/scenes/00000000-0000-0000-0000-000000000000/images", json={}
    )

    assert response.status_code == 404
    assert response.json() == {"code": "SCENE_NOT_FOUND", "message": "Scene not found"}


async def test_batch_records_the_requested_count_even_when_cuts_are_skipped(
    client: httpx.AsyncClient, scene: dict[str, Any]
) -> None:
    await client.post(f"/api/cuts/{scene['cuts'][0]['id']}/images", json={})

    body = (await client.post(f"/api/scenes/{scene['id']}/images", json={})).json()

    assert body["requestedCount"] == 6
    assert len(body["createdJobIds"]) + len(body["skipped"]) == 6
