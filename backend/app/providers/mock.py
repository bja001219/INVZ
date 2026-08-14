from app.schemas import CutDraft, SceneDraft


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
