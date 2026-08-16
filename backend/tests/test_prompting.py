import pytest

from app.prompting import (
    NEGATIVE_STYLE_GUIDE,
    SCENE_SYSTEM_INSTRUCTION,
    VISUAL_STYLE_GUIDE,
    character_sheet,
    compose_image_prompt,
    compose_video_prompt,
)
from app.schemas import CharacterProfile


@pytest.fixture
def profiles() -> list[CharacterProfile]:
    return [
        CharacterProfile(
            name="Mina",
            role="female lead high-school student",
            age_range="17",
            hair_color="dark brown",
            hair_style="long straight",
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
            outfit="grey blazer uniform with loosened tie",
            build="tall lean",
            face_impression="bright open smile",
            signature_prop="a worn canvas backpack",
        ),
    ]


def test_character_sheet_is_stable_and_keeps_input_order(profiles: list[CharacterProfile]) -> None:
    sheet = character_sheet(profiles)

    assert sheet == character_sheet(profiles)
    assert sheet.index("Mina") < sheet.index("Jun")


def test_character_sheet_carries_every_identity_field(profiles: list[CharacterProfile]) -> None:
    sheet = character_sheet(profiles)

    for value in (
        "dark brown",
        "long straight",
        "navy sailor uniform with a red ribbon",
        "slim",
        "soft calm expression with round eyes",
        "a stack of library books",
    ):
        assert value in sheet


def test_image_prompt_contains_style_characters_shot_and_negative(
    profiles: list[CharacterProfile],
) -> None:
    prompt = compose_image_prompt(profiles, "two students meet at the school gate")

    assert VISUAL_STYLE_GUIDE in prompt
    assert "Mina" in prompt
    assert "Jun" in prompt
    assert "two students meet at the school gate" in prompt
    assert NEGATIVE_STYLE_GUIDE in prompt


def test_video_prompt_adds_motion_and_nominal_duration(profiles: list[CharacterProfile]) -> None:
    prompt = compose_video_prompt(profiles, "walking together", "slow dolly in")

    assert "slow dolly in" in prompt
    assert "5-second" in prompt
    assert VISUAL_STYLE_GUIDE in prompt
    assert NEGATIVE_STYLE_GUIDE in prompt


@pytest.mark.parametrize("banned", ["photorealistic", "live action", "3D render"])
def test_prompts_only_mention_realism_inside_the_negative_guide(
    profiles: list[CharacterProfile], banned: str
) -> None:
    for prompt in (
        compose_image_prompt(profiles, "a quiet classroom"),
        compose_video_prompt(profiles, "a quiet classroom", "static shot"),
    ):
        assert prompt.count(banned) == NEGATIVE_STYLE_GUIDE.count(banned)


def test_every_cut_shares_one_identical_character_section(
    profiles: list[CharacterProfile],
) -> None:
    sections = {
        compose_image_prompt(profiles, f"shot number {order}").split("Shot:")[0]
        for order in range(1, 7)
    }

    assert len(sections) == 1


def test_composition_is_pure_and_order_sensitive(profiles: list[CharacterProfile]) -> None:
    forward = compose_image_prompt(profiles, "a shot")
    reversed_profiles = compose_image_prompt(list(reversed(profiles)), "a shot")

    assert forward == compose_image_prompt(profiles, "a shot")
    assert forward != reversed_profiles


def test_scene_instruction_demands_recurring_characters_and_style() -> None:
    assert "recurring" in SCENE_SYSTEM_INSTRUCTION.lower()
    assert "shotdescription" in SCENE_SYSTEM_INSTRUCTION.lower()
    assert "videomotion" in SCENE_SYSTEM_INSTRUCTION.lower()
