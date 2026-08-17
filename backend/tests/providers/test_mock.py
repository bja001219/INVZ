from uuid import uuid4

from app.models import GenerationKind
from app.providers.contracts import GenerationRequest
from app.providers.mock import MockGenerationProvider
from app.schemas import MockScenario


def image_request(*, scenario: MockScenario | None) -> GenerationRequest:
    return GenerationRequest(
        job_id=uuid4(),
        kind=GenerationKind.IMAGE,
        prompt="prompt",
        source_image_url=None,
        duration_sec=5,
        mock_scenario=scenario,
        attempt_count=1,
    )


async def test_webhook_scenario_defers_delivery_until_the_job_is_processing() -> None:
    """The callback is handed back, not sent.

    Sending it inside `submit()` races the worker's own PROCESSING commit: with a zero delay
    the callback can arrive first, find no job to apply it to, and be dropped.
    """
    delivered: list[tuple[str, GenerationKind]] = []

    async def sender(task_id: str, kind: GenerationKind) -> None:
        delivered.append((task_id, kind))

    provider = MockGenerationProvider(webhook_sender=sender)

    submission = await provider.submit(image_request(scenario=MockScenario.SUCCEED_VIA_WEBHOOK))

    assert delivered == []
    assert submission.on_processing is not None
    await submission.on_processing()
    assert delivered == [(submission.external_task_id, GenerationKind.IMAGE)]


async def test_polling_scenarios_carry_no_callback() -> None:
    async def sender(task_id: str, kind: GenerationKind) -> None:  # pragma: no cover - unused
        raise AssertionError("polling scenarios must not schedule a callback")

    provider = MockGenerationProvider(webhook_sender=sender)

    submission = await provider.submit(image_request(scenario=MockScenario.SUCCESS))

    assert submission.on_processing is None


async def test_webhook_scenario_falls_back_to_polling_without_a_sender() -> None:
    provider = MockGenerationProvider()

    submission = await provider.submit(image_request(scenario=MockScenario.SUCCEED_VIA_WEBHOOK))

    assert submission.on_processing is None
    assert submission.external_task_id.startswith("mock:")
