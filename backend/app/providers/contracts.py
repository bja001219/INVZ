from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from app.models import GenerationKind
from app.schemas import MockScenario, SceneDraft


class ProviderError(Exception):
    def __init__(self, code: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


class RetryableProviderError(ProviderError):
    """A transient upstream failure that can be retried safely."""


class SchemaProviderError(RetryableProviderError):
    """The provider answered, but not in a shape we accept.

    Retryable on purpose: the call has no side effect to duplicate, and a language model that
    got the shape wrong once usually gets it right on the next attempt. Kept distinct from a
    transport failure so the final error can say which one ran out of attempts.
    """


class PermanentProviderError(ProviderError):
    """An upstream failure that must not be retried."""


class SubmissionUncertainError(PermanentProviderError):
    """A submission may have been accepted and must never be repeated."""


@dataclass(frozen=True)
class GenerationRequest:
    job_id: UUID
    kind: GenerationKind
    prompt: str
    source_image_url: str | None
    duration_sec: Literal[5]
    mock_scenario: MockScenario | None
    attempt_count: int
    # The scene anchor image an IMAGE request should stay visually consistent with.
    reference_image_url: str | None = None
    # The mode snapshot taken when the job was created; decides which provider handles it.
    generation_mode: str = "MOCK"


@dataclass(frozen=True)
class Submission:
    external_task_id: str
    # Work the provider wants run once the job is durably PROCESSING, not before. A provider
    # that pushes its own callback would otherwise race the worker's commit and deliver a
    # result to a job that cannot accept one yet.
    on_processing: Callable[[], Awaitable[None]] | None = None


@dataclass(frozen=True)
class TaskResult:
    state: Literal["PENDING", "SUCCEEDED", "FAILED"]
    result_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


class SceneProvider(Protocol):
    async def generate(self, prompt: str) -> SceneDraft: ...


class GenerationProvider(Protocol):
    async def submit(self, request: GenerationRequest) -> Submission: ...

    async def poll(self, external_task_id: str) -> TaskResult: ...
