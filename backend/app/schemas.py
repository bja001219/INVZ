from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    generation_mode: str = Field(serialization_alias="generationMode")


class CutDraft(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    order: int = Field(ge=1, le=6)
    image_prompt: str = Field(alias="imagePrompt", min_length=1)
    video_prompt: str = Field(alias="videoPrompt", min_length=1)
    duration_sec: Literal[5] = Field(alias="durationSec")


class SceneDraft(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    cuts: Annotated[list[CutDraft], Field(min_length=6, max_length=6)]

    @model_validator(mode="after")
    def ordered_once(self) -> "SceneDraft":
        if [cut.order for cut in self.cuts] != [1, 2, 3, 4, 5, 6]:
            raise ValueError("cuts must be ordered 1 through 6")
        return self


class SceneCreateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)

    @field_validator("prompt", mode="before")
    @classmethod
    def strip_prompt(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class CutResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    order: int
    image_prompt: str = Field(serialization_alias="imagePrompt")
    video_prompt: str = Field(serialization_alias="videoPrompt")
    duration_sec: Literal[5] = Field(serialization_alias="durationSec")


class SceneResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    user_prompt: str = Field(serialization_alias="userPrompt")
    title: str
    scenario: str
    cuts: list[CutResponse]
