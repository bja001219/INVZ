from app.models import GenerationKind
from app.providers.contracts import (
    GenerationRequest,
    PermanentProviderError,
    RetryableProviderError,
    Submission,
    TaskResult,
)
from app.schemas import CharacterProfile, CutDraft, MockScenario, SceneDraft

_MOCK_CHARACTERS = (
    CharacterProfile(
        name="Mina",
        role="female lead high-school student",
        age_range="17",
        hair_color="dark brown",
        hair_style="long straight with a short fringe",
        outfit="navy sailor uniform with a red ribbon",
        build="slim",
        face_impression="soft calm expression with round eyes",
        signature_prop="a stack of library books",
    ),
    CharacterProfile(
        name="Jun",
        role="male lead high-school student",
        age_range="17",
        hair_color="black",
        hair_style="short messy",
        outfit="grey blazer uniform with a loosened tie",
        build="tall and lean",
        face_impression="bright open smile",
        signature_prop="a worn canvas backpack",
    ),
)

_MOCK_SHOTS = (
    "wide establishing shot of the school gate at golden hour as the two leads notice each other",
    "medium two-shot in the corridor while they walk side by side",
    "close-up on the female lead glancing up from her books",
    "over-the-shoulder shot of the male lead offering a folded note",
    "wide shot of the empty classroom as they sit by the window",
    "final wide shot on the rooftop with the city behind them",
)

_MOCK_MOTIONS = (
    "slow push in",
    "steady tracking alongside the characters",
    "gentle rack focus",
    "slight handheld drift",
    "slow dolly out",
    "static hold with drifting clouds",
)


class MockSceneProvider:
    """Deterministic scene drafts so Mock mode exercises the same composition path as Live."""

    async def generate(self, prompt: str) -> SceneDraft:
        return SceneDraft(
            title=prompt.strip().title(),
            scenario=f"A six-shot animation based on {prompt.strip()}.",
            character_profiles=list(_MOCK_CHARACTERS),
            cuts=[
                CutDraft(
                    order=order,
                    shot_description=_MOCK_SHOTS[order - 1],
                    video_motion=_MOCK_MOTIONS[order - 1],
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
