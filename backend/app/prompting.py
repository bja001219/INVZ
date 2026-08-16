"""Deterministic prompt composition.

Character consistency and visual style are decided here, not by the language model. The model
supplies a character sheet and per-cut shot descriptions; these pure functions splice them into
one fixed template so every cut of a scene carries byte-identical style and character sections.
Regenerating a cut therefore reuses the exact prompt that produced the earlier version.
"""

from collections.abc import Sequence

from app.schemas import CharacterProfile

VISUAL_STYLE_GUIDE = (
    "stylized 2D animation still, cute cinematic anime look, soft cel shading, "
    "clean appealing character design, expressive faces, warm slightly dreamy "
    "high-school mood, hand-painted background, gentle rim light"
)

NEGATIVE_STYLE_GUIDE = (
    "photorealistic rendering, live action footage, 3D render, "
    "photographic skin texture, hyperrealistic detail, uncanny realism"
)

SCENE_SYSTEM_INSTRUCTION = (
    "You plan short animated scenes. Given one user prompt, invent two to four recurring "
    "characters who appear across the whole scene, then break the scene into exactly six shots "
    "of five seconds each.\n"
    "\n"
    "Character rules:\n"
    "- Describe every character field concretely; never write 'unspecified' or 'varies'.\n"
    "- The same characters must carry through all six shots. Do not introduce new people.\n"
    "\n"
    "Shot rules:\n"
    "- shotDescription states framing, setting, and what the characters do in that shot.\n"
    "- videoMotion states how the shot moves over its five seconds.\n"
    "- Never restate hair, outfit, build, or props in shotDescription or videoMotion. A separate "
    "character sheet is injected into the final prompt, and repeating those details makes the "
    "shots contradict it.\n"
    "- Never mention rendering style, realism, or medium. A separate style guide handles that."
)


def character_sheet(profiles: Sequence[CharacterProfile]) -> str:
    """Render the scene's cast as one stable clause, in the order the model produced it."""
    return "; ".join(
        f"{profile.name} ({profile.role}, age {profile.age_range}): "
        f"{profile.hair_color} {profile.hair_style} hair, "
        f"wearing {profile.outfit}, {profile.build} build, "
        f"{profile.face_impression}, carries {profile.signature_prop}"
        for profile in profiles
    )


def compose_image_prompt(profiles: Sequence[CharacterProfile], shot_description: str) -> str:
    return (
        f"{VISUAL_STYLE_GUIDE}. "
        f"Characters, keep identical in every shot: {character_sheet(profiles)}. "
        f"Shot: {shot_description}. "
        f"Avoid: {NEGATIVE_STYLE_GUIDE}."
    )


def compose_video_prompt(
    profiles: Sequence[CharacterProfile], shot_description: str, video_motion: str
) -> str:
    return (
        f"{VISUAL_STYLE_GUIDE}. "
        f"Characters, keep identical in every shot: {character_sheet(profiles)}. "
        f"Shot: {shot_description}. Motion: {video_motion}. "
        f"5-second continuous animated shot, hold the character designs steady throughout. "
        f"Avoid: {NEGATIVE_STYLE_GUIDE}."
    )
