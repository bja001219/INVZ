import json
import math
from typing import Any
from urllib.parse import urlparse

import httpx

from app.models import GenerationKind
from app.providers.contracts import (
    GenerationRequest,
    PermanentProviderError,
    RetryableProviderError,
    Submission,
    SubmissionUncertainError,
    TaskResult,
)

_CREATE_TASK_URL = "https://api.kie.ai/api/v1/jobs/createTask"
_TASK_DETAIL_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"


class KieGenerationProvider:
    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        callback_url: str | None = None,
    ) -> None:
        self._authorization = f"Bearer {api_key}"
        self._callback_url = callback_url
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0, read=30.0, write=30.0)
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def submit(self, request: GenerationRequest) -> Submission:
        body = _submission_body(request)
        if self._callback_url is not None:
            # Webhook is an accelerator, not a replacement: polling still closes every task.
            body["callBackUrl"] = self._callback_url
        try:
            response = await self._client.post(
                _CREATE_TASK_URL,
                headers={"Authorization": self._authorization},
                json=body,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise RetryableProviderError("KIE_SUBMIT_RETRYABLE") from None
        except httpx.ReadTimeout:
            raise SubmissionUncertainError("SUBMISSION_UNCERTAIN") from None
        except httpx.RequestError:
            raise PermanentProviderError("KIE_REQUEST_FAILED") from None

        _raise_for_http_status(response, operation="submit")
        payload = _response_payload(response)
        data = _successful_data(payload)
        task_id = data.get("taskId")
        if not isinstance(task_id, str) or not task_id.strip():
            raise PermanentProviderError("KIE_RESPONSE_INVALID")
        return Submission(external_task_id=task_id.strip())

    async def poll(self, external_task_id: str) -> TaskResult:
        try:
            response = await self._client.get(
                _TASK_DETAIL_URL,
                headers={"Authorization": self._authorization},
                params={"taskId": external_task_id},
            )
        except httpx.RequestError:
            raise RetryableProviderError("KIE_POLL_RETRYABLE") from None

        _raise_for_http_status(response, operation="poll")
        payload = _response_payload(response)
        return task_result_from_data(_successful_data(payload))


def task_result_from_data(data: dict[str, Any]) -> TaskResult:
    """Normalize one Kie task record.

    Polling and the webhook route both call this, so a callback and a poll of the same task
    always produce the same TaskResult and drive the same state transition.
    """
    state = data.get("state")
    if not isinstance(state, str):
        raise PermanentProviderError("KIE_RESPONSE_INVALID")
    if state in {"waiting", "queuing", "generating"}:
        return TaskResult(state="PENDING")
    if state == "fail":
        return TaskResult(
            state="FAILED",
            error_code="KIE_TASK_FAILED",
            error_message="Generation provider failed",
            retryable=data.get("retryable") is True,
        )
    if state != "success":
        raise PermanentProviderError("KIE_RESPONSE_INVALID")

    result_json = data.get("resultJson")
    if not isinstance(result_json, str):
        raise PermanentProviderError("KIE_RESPONSE_INVALID")
    try:
        parsed_result = json.loads(result_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        raise PermanentProviderError("KIE_RESPONSE_INVALID") from None
    if not isinstance(parsed_result, dict):
        raise PermanentProviderError("KIE_RESPONSE_INVALID")
    result_urls = parsed_result.get("resultUrls")
    if not isinstance(result_urls, list) or not result_urls:
        raise PermanentProviderError("KIE_RESPONSE_INVALID")
    result_url = result_urls[0]
    if not _is_http_url(result_url):
        raise PermanentProviderError("KIE_RESPONSE_INVALID")
    return TaskResult(state="SUCCEEDED", result_url=result_url.strip())


def _submission_body(request: GenerationRequest) -> dict[str, object]:
    if request.kind is GenerationKind.IMAGE:
        image_input: dict[str, object] = {
            "prompt": request.prompt,
            "aspect_ratio": "16:9",
            "output_format": "png",
        }
        # Cuts after the first send the scene anchor so the model redraws the same cast.
        if request.reference_image_url is not None and _is_http_url(request.reference_image_url):
            image_input["image_urls"] = [request.reference_image_url]
        return {"model": "google/nano-banana", "input": image_input}
    if request.source_image_url is None or not _is_http_url(request.source_image_url):
        raise PermanentProviderError("KIE_REQUEST_INVALID")
    return {
        "model": "kling-2.6/image-to-video",
        "input": {
            "prompt": request.prompt,
            "image_urls": [request.source_image_url],
            "sound": False,
            "duration": "5",
        },
    }


def _raise_for_http_status(response: httpx.Response, *, operation: str) -> None:
    if 200 <= response.status_code < 300:
        return
    if response.status_code == 429 or response.status_code >= 500:
        code = "KIE_SUBMIT_RETRYABLE" if operation == "submit" else "KIE_POLL_RETRYABLE"
        raise RetryableProviderError(
            code,
            retry_after_seconds=_retry_after_seconds(response.headers.get("Retry-After")),
        )
    raise PermanentProviderError("KIE_REQUEST_FAILED")


def _response_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        raise PermanentProviderError("KIE_RESPONSE_INVALID") from None
    if not isinstance(payload, dict):
        raise PermanentProviderError("KIE_RESPONSE_INVALID")
    return payload


def _successful_data(payload: dict[str, Any]) -> dict[str, Any]:
    if type(payload.get("code")) is not int or payload["code"] != 200:
        raise PermanentProviderError("KIE_RESPONSE_INVALID")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PermanentProviderError("KIE_RESPONSE_INVALID")
    return data


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _is_http_url(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
