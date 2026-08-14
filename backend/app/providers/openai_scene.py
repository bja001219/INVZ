import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)
from pydantic import ValidationError

from app.providers.contracts import PermanentProviderError, RetryableProviderError
from app.schemas import SceneDraft


class OpenAISceneProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5.4-mini",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            max_retries=0,
            timeout=httpx.Timeout(30.0, connect=5.0, read=30.0, write=30.0),
        )

    async def generate(self, prompt: str) -> SceneDraft:
        try:
            response = await self._client.responses.parse(
                model=self._model,
                input=[{"role": "user", "content": prompt}],
                text_format=SceneDraft,
            )
            draft = response.output_parsed
        except (
            RateLimitError,
            InternalServerError,
            httpx.ConnectError,
            httpx.ConnectTimeout,
        ) as error:
            raise RetryableProviderError("OPENAI_RETRYABLE") from error
        except APIConnectionError as error:
            if _has_connect_cause(error):
                raise RetryableProviderError("OPENAI_RETRYABLE") from error
            raise PermanentProviderError("OPENAI_CONNECTION_FAILED") from error
        except APIStatusError as error:
            raise PermanentProviderError("OPENAI_REQUEST_FAILED") from error
        except (httpx.HTTPError, ValidationError, TypeError, ValueError) as error:
            raise PermanentProviderError("OPENAI_RESPONSE_INVALID") from error

        if not isinstance(draft, SceneDraft):
            raise PermanentProviderError("OPENAI_RESPONSE_INVALID")
        return draft


def _has_connect_cause(error: APIConnectionError) -> bool:
    return isinstance(
        error.__cause__ or error.__context__,
        (httpx.ConnectError, httpx.ConnectTimeout),
    )
