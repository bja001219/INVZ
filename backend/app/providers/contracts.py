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


@dataclass(frozen=True)
class Submission:
    external_task_id: str


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
