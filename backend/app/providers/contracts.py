from typing import Protocol

from app.schemas import SceneDraft


class RetryableProviderError(Exception):
    """A transient upstream failure that can be retried safely."""


class PermanentProviderError(Exception):
    """An upstream failure that must not be retried."""


class SceneProvider(Protocol):
    async def generate(self, prompt: str) -> SceneDraft: ...
