from app.models import GenerationKind
from app.providers.contracts import (
    GenerationRequest,
    PermanentProviderError,
    RetryableProviderError,
    Submission,
    TaskResult,
)
from app.schemas import CutDraft, MockScenario, SceneDraft


class MockSceneProvider:
    async def generate(self, prompt: str) -> SceneDraft:
        title = prompt.strip().title()
        return SceneDraft(
            title=title,
            scenario=f"A six-shot animation based on {prompt.strip()}.",
            cuts=[
                CutDraft(
                    order=order,
                    image_prompt=f"{prompt.strip()}, shot {order}, cinematic still",
                    video_prompt=f"{prompt.strip()}, shot {order}, gentle camera movement",
                    duration_sec=5,
                )
                for order in range(1, 7)
            ],
        )


class MockGenerationProvider:
    async def submit(self, request: GenerationRequest) -> Submission:
        scenario = request.mock_scenario or MockScenario.SUCCESS
        if scenario is MockScenario.ALWAYS_FAIL:
            raise RetryableProviderError("MOCK_GENERATION_FAILED")
        if scenario is MockScenario.FAIL_TWICE_THEN_SUCCEED and request.attempt_count <= 2:
            raise RetryableProviderError("MOCK_GENERATION_FAILED")
        return Submission(
            external_task_id=f"mock:{request.kind.value}:{request.job_id}:{request.attempt_count}"
        )

    async def poll(self, external_task_id: str) -> TaskResult:
        parts = external_task_id.split(":", maxsplit=3)
        if len(parts) != 4 or parts[0] != "mock":
            raise PermanentProviderError("MOCK_TASK_INVALID")
        if parts[1] == GenerationKind.IMAGE.value:
            return TaskResult(state="SUCCEEDED", result_url="/media/mock/cut-image.png")
        if parts[1] == GenerationKind.VIDEO.value:
            return TaskResult(state="SUCCEEDED", result_url="/media/mock/cut-video.mp4")
        raise PermanentProviderError("MOCK_TASK_INVALID")
