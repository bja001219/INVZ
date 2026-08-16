import json
from collections.abc import Callable

import httpx
import pytest
from httpx import Response
from openai import APIConnectionError, APITimeoutError

from app.prompting import SCENE_SYSTEM_INSTRUCTION
from app.providers.contracts import PermanentProviderError, RetryableProviderError
from app.providers.openai_scene import OpenAISceneProvider
from tests.conftest import build_scene_payload


def valid_scene_payload() -> dict[str, object]:
    return build_scene_payload()


def openai_scene_response(payload: dict[str, object]) -> dict[str, object]:
    return {
        "id": "resp_123",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "gpt-5.4-mini",
        "output": [
            {
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(payload),
                        "annotations": [],
                    }
                ],
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }


class RaisingResponses:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def parse(self, **kwargs: object) -> object:
        raise self._error


class RaisingClient:
    def __init__(self, error: Exception) -> None:
        self.responses = RaisingResponses(error)


def sdk_connection_error(cause: BaseException | None) -> APIConnectionError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    error = APIConnectionError(request=request)
    error.__cause__ = cause
    return error


def sdk_timeout_error(cause: BaseException | None) -> APITimeoutError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    error = APITimeoutError(request=request)
    error.__cause__ = cause
    return error


@pytest.fixture
def provider() -> OpenAISceneProvider:
    return OpenAISceneProvider(api_key="test-key")


async def test_openai_scene_request_uses_model_prompt_and_strict_schema(
    provider: OpenAISceneProvider, respx_mock
) -> None:
    route = respx_mock.post("https://api.openai.com/v1/responses").mock(
        return_value=Response(200, json=openai_scene_response(valid_scene_payload()))
    )

    draft = await provider.generate("moon voyage")

    request = json.loads(route.calls.last.request.content)
    assert request["model"] == "gpt-5.4-mini"
    assert "moon voyage" in json.dumps(request["input"])
    assert request["input"][0]["content"] == SCENE_SYSTEM_INSTRUCTION
    assert request["text"]["format"]["strict"] is True
    assert len(draft.cuts) == 6
    assert len(draft.character_profiles) == 2


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_openai_retryable_status_is_normalized(
    provider: OpenAISceneProvider, respx_mock, status: int
) -> None:
    respx_mock.post("https://api.openai.com/v1/responses").mock(return_value=Response(status))

    with pytest.raises(RetryableProviderError):
        await provider.generate("moon voyage")


async def test_openai_malformed_structured_output_is_permanent(
    provider: OpenAISceneProvider, respx_mock
) -> None:
    respx_mock.post("https://api.openai.com/v1/responses").mock(
        return_value=Response(200, json=openai_scene_response({"title": "bad"}))
    )

    with pytest.raises(PermanentProviderError, match="OPENAI_RESPONSE_INVALID"):
        await provider.generate("moon voyage")


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda request: httpx.ConnectError("connect failed", request=request),
        lambda request: httpx.ConnectTimeout("connect timed out", request=request),
        lambda request: sdk_connection_error(httpx.ConnectError("connect failed", request=request)),
        lambda request: sdk_timeout_error(
            httpx.ConnectTimeout("connect timed out", request=request)
        ),
    ],
)
async def test_openai_connect_failures_are_retryable(
    error_factory: Callable[[httpx.Request], Exception],
) -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    provider = OpenAISceneProvider(
        api_key="test-key",
        client=RaisingClient(error_factory(request)),  # type: ignore[arg-type]
    )

    with pytest.raises(RetryableProviderError):
        await provider.generate("moon voyage")


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda request: httpx.ReadTimeout("read timed out", request=request),
        lambda request: sdk_connection_error(httpx.ReadTimeout("read timed out", request=request)),
        lambda request: sdk_timeout_error(None),
        lambda request: sdk_connection_error(None),
    ],
)
async def test_openai_non_connect_failures_are_permanent(
    error_factory: Callable[[httpx.Request], Exception],
) -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    provider = OpenAISceneProvider(
        api_key="test-key",
        client=RaisingClient(error_factory(request)),  # type: ignore[arg-type]
    )

    with pytest.raises(PermanentProviderError):
        await provider.generate("moon voyage")
