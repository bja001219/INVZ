import json
from collections.abc import AsyncIterator, Callable
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from httpx import Response

from app.models import GenerationKind
from app.providers.contracts import (
    GenerationRequest,
    PermanentProviderError,
    RetryableProviderError,
    SubmissionUncertainError,
)
from app.providers.kie import KieGenerationProvider

CREATE_TASK_URL = "https://api.kie.ai/api/v1/jobs/createTask"
TASK_DETAIL_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"


def image_request(*, prompt: str = "forest") -> GenerationRequest:
    return GenerationRequest(
        job_id=uuid4(),
        kind=GenerationKind.IMAGE,
        prompt=prompt,
        source_image_url=None,
        duration_sec=5,
        mock_scenario=None,
        attempt_count=1,
    )


def video_request(
    prompt: str = "move slowly",
    source_image_url: str = "https://cdn.example/image.png",
) -> GenerationRequest:
    return GenerationRequest(
        job_id=uuid4(),
        kind=GenerationKind.VIDEO,
        prompt=prompt,
        source_image_url=source_image_url,
        duration_sec=5,
        mock_scenario=None,
        attempt_count=2,
    )


def success_payload(result_urls: list[str]) -> dict[str, object]:
    return {
        "code": 200,
        "msg": "success",
        "data": {
            "taskId": "task-1",
            "state": "success",
            "resultJson": json.dumps({"resultUrls": result_urls}),
        },
    }


@pytest_asyncio.fixture
async def kie() -> AsyncIterator[KieGenerationProvider]:
    provider = KieGenerationProvider(api_key="test-kie-key")
    yield provider
    await provider.aclose()


async def test_kie_image_submit_contract(kie: KieGenerationProvider, respx_mock) -> None:
    route = respx_mock.post(CREATE_TASK_URL).mock(
        return_value=Response(
            200,
            json={"code": 200, "msg": "success", "data": {"taskId": "img-1"}},
        )
    )

    result = await kie.submit(image_request(prompt="forest"))

    request = route.calls.last.request
    assert json.loads(request.content) == {
        "model": "google/nano-banana",
        "input": {"prompt": "forest", "aspect_ratio": "16:9", "output_format": "png"},
    }
    assert request.headers["Authorization"] == "Bearer test-kie-key"
    assert result.external_task_id == "img-1"


async def test_kie_video_submit_contract(kie: KieGenerationProvider, respx_mock) -> None:
    route = respx_mock.post(CREATE_TASK_URL).mock(
        return_value=Response(
            200,
            json={"code": 200, "msg": "success", "data": {"taskId": "vid-1"}},
        )
    )

    await kie.submit(video_request())

    assert json.loads(route.calls.last.request.content) == {
        "model": "kling-2.6/image-to-video",
        "input": {
            "prompt": "move slowly",
            "image_urls": ["https://cdn.example/image.png"],
            "sound": False,
            "duration": "5",
        },
    }


async def test_kie_poll_uses_documented_query_and_authorization(
    kie: KieGenerationProvider, respx_mock
) -> None:
    route = respx_mock.get(TASK_DETAIL_URL, params={"taskId": "vid-1"}).mock(
        return_value=Response(200, json=success_payload(["https://cdn.example/video.mp4"]))
    )

    await kie.poll("vid-1")

    request = route.calls.last.request
    assert request.url == httpx.URL(f"{TASK_DETAIL_URL}?taskId=vid-1")
    assert request.headers["Authorization"] == "Bearer test-kie-key"


@pytest.mark.parametrize("state", ["waiting", "queuing", "generating"])
async def test_kie_poll_maps_documented_active_states_to_pending(
    kie: KieGenerationProvider, respx_mock, state: str
) -> None:
    respx_mock.get(TASK_DETAIL_URL).mock(
        return_value=Response(200, json={"code": 200, "data": {"state": state}})
    )

    result = await kie.poll("task-1")

    assert result.state == "PENDING"
    assert result.result_url is None


async def test_kie_poll_success_requires_nonempty_http_url(
    kie: KieGenerationProvider, respx_mock
) -> None:
    respx_mock.get(TASK_DETAIL_URL).mock(
        return_value=Response(200, json=success_payload(["https://cdn.example/video.mp4"]))
    )

    result = await kie.poll("vid-1")

    assert result.state == "SUCCEEDED"
    assert result.result_url == "https://cdn.example/video.mp4"


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 200, "data": {"state": "success", "resultJson": "{}"}},
        {"code": 200, "data": {"state": "success", "resultJson": "not-json"}},
        {"code": 200, "data": {"state": "mystery"}},
        {"code": 500, "msg": "business failure"},
        {"code": 200, "data": []},
    ],
)
async def test_kie_malformed_or_unknown_poll_payload_is_contract_error(
    kie: KieGenerationProvider, respx_mock, payload: dict[str, object]
) -> None:
    respx_mock.get(TASK_DETAIL_URL).mock(return_value=Response(200, json=payload))

    with pytest.raises(PermanentProviderError, match="KIE_RESPONSE_INVALID"):
        await kie.poll("task-1")


async def test_kie_nonstring_state_is_contract_error(
    kie: KieGenerationProvider, respx_mock
) -> None:
    respx_mock.get(TASK_DETAIL_URL).mock(
        return_value=Response(200, json={"code": 200, "data": {"state": []}})
    )

    with pytest.raises(PermanentProviderError, match="KIE_RESPONSE_INVALID"):
        await kie.poll("task-1")


async def test_kie_malformed_bracketed_result_url_is_contract_error(
    kie: KieGenerationProvider, respx_mock
) -> None:
    respx_mock.get(TASK_DETAIL_URL).mock(
        return_value=Response(200, json=success_payload(["http://["]))
    )

    with pytest.raises(PermanentProviderError, match="KIE_RESPONSE_INVALID"):
        await kie.poll("task-1")


@pytest.mark.parametrize("result_url", ["", "   ", "ftp://cdn.example/video.mp4", "relative"])
async def test_kie_success_rejects_empty_or_non_http_result_url(
    kie: KieGenerationProvider, respx_mock, result_url: str
) -> None:
    respx_mock.get(TASK_DETAIL_URL).mock(
        return_value=Response(200, json=success_payload([result_url]))
    )

    with pytest.raises(PermanentProviderError, match="KIE_RESPONSE_INVALID"):
        await kie.poll("task-1")


@pytest.mark.parametrize(
    "response",
    [
        Response(200, content=b"not-json"),
        Response(200, json={"code": 500, "msg": "business failure"}),
        Response(200, json={"code": 200, "data": {}}),
        Response(200, json={"code": 200, "data": {"taskId": ""}}),
    ],
)
async def test_kie_submit_rejects_malformed_business_or_missing_task_id(
    kie: KieGenerationProvider, respx_mock, response: Response
) -> None:
    respx_mock.post(CREATE_TASK_URL).mock(return_value=response)

    with pytest.raises(PermanentProviderError, match="KIE_RESPONSE_INVALID"):
        await kie.submit(image_request())


async def test_kie_failed_task_returns_only_safe_error_fields(
    kie: KieGenerationProvider, respx_mock
) -> None:
    respx_mock.get(TASK_DETAIL_URL).mock(
        return_value=Response(
            200,
            json={
                "code": 200,
                "data": {
                    "state": "fail",
                    "failCode": "upstream-secret-code",
                    "failMsg": "provider body must not escape",
                },
            },
        )
    )

    result = await kie.poll("task-1")

    assert result.state == "FAILED"
    assert result.error_code == "KIE_TASK_FAILED"
    assert result.error_message == "Generation provider failed"
    assert result.retryable is False
    assert "provider body" not in repr(result)


async def test_kie_failed_task_honors_only_boolean_retryable_signal(
    kie: KieGenerationProvider, respx_mock
) -> None:
    respx_mock.get(TASK_DETAIL_URL).mock(
        return_value=Response(
            200,
            json={"code": 200, "data": {"state": "fail", "retryable": True}},
        )
    )

    result = await kie.poll("task-1")

    assert result.state == "FAILED"
    assert result.retryable is True


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_kie_submit_explicit_retryable_http_status(
    kie: KieGenerationProvider, respx_mock, status: int
) -> None:
    respx_mock.post(CREATE_TASK_URL).mock(
        return_value=Response(status, headers={"Retry-After": "7.5"})
    )

    with pytest.raises(RetryableProviderError) as caught:
        await kie.submit(image_request())

    assert caught.value.retry_after_seconds == 7.5


@pytest.mark.parametrize(
    "retry_after",
    ["", "soon", "Wed, 21 Oct 2015 07:28:00 GMT", "-1", "nan", "inf"],
)
async def test_kie_ignores_invalid_retry_after(
    kie: KieGenerationProvider, respx_mock, retry_after: str
) -> None:
    respx_mock.post(CREATE_TASK_URL).mock(
        return_value=Response(429, headers={"Retry-After": retry_after})
    )

    with pytest.raises(RetryableProviderError) as caught:
        await kie.submit(image_request())

    assert caught.value.retry_after_seconds is None


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ConnectTimeout])
async def test_kie_submit_connect_before_send_is_retryable(
    error_type: type[httpx.RequestError],
) -> None:
    async def raise_connect(request: httpx.Request) -> Response:
        raise error_type("sensitive connect detail", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(raise_connect)) as client:
        provider = KieGenerationProvider(api_key="secret-key", client=client)
        with pytest.raises(RetryableProviderError, match="KIE_SUBMIT_RETRYABLE") as caught:
            await provider.submit(image_request())

    assert "secret-key" not in repr(caught.value)
    assert "sensitive connect detail" not in str(caught.value)


async def test_kie_post_read_timeout_is_submission_uncertain() -> None:
    async def raise_timeout(request: httpx.Request) -> Response:
        raise httpx.ReadTimeout("sensitive timeout detail", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(raise_timeout)) as client:
        provider = KieGenerationProvider(api_key="secret-key", client=client)
        with pytest.raises(SubmissionUncertainError, match="SUBMISSION_UNCERTAIN") as caught:
            await provider.submit(image_request())

    assert "secret-key" not in repr(caught.value)
    assert "sensitive timeout detail" not in str(caught.value)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_kie_poll_explicit_retryable_http_status(
    kie: KieGenerationProvider, respx_mock, status: int
) -> None:
    respx_mock.get(TASK_DETAIL_URL).mock(return_value=Response(status))

    with pytest.raises(RetryableProviderError, match="KIE_POLL_RETRYABLE"):
        await kie.poll("task-1")


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda request: httpx.ConnectError("connect failed", request=request),
        lambda request: httpx.ConnectTimeout("connect timed out", request=request),
        lambda request: httpx.ReadTimeout("read timed out", request=request),
    ],
)
async def test_kie_poll_transport_failure_is_retryable(
    error_factory: Callable[[httpx.Request], httpx.RequestError],
) -> None:
    async def raise_transport(request: httpx.Request) -> Response:
        raise error_factory(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(raise_transport)) as client:
        provider = KieGenerationProvider(api_key="test-key", client=client)
        with pytest.raises(RetryableProviderError, match="KIE_POLL_RETRYABLE"):
            await provider.poll("task-1")


async def test_kie_nonretryable_http_status_is_permanent(
    kie: KieGenerationProvider, respx_mock
) -> None:
    respx_mock.post(CREATE_TASK_URL).mock(
        return_value=Response(400, json={"apiKey": "secret-key", "input": {"prompt": "private"}})
    )

    with pytest.raises(PermanentProviderError, match="KIE_REQUEST_FAILED") as caught:
        await kie.submit(image_request())

    rendered = f"{caught.value!s} {caught.value!r}"
    assert "secret-key" not in rendered
    assert "private" not in rendered


def test_kie_provider_repr_does_not_expose_api_key() -> None:
    provider = KieGenerationProvider(api_key="secret-key")

    assert "secret-key" not in repr(provider)


def test_contract_fixture_has_no_opaque_provider_body() -> None:
    request = image_request()

    assert not hasattr(request, "provider_body")
    assert request.attempt_count == 1
