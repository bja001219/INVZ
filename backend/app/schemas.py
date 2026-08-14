from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import GenerationKind, JobStatus


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


class MockScenario(StrEnum):
    SUCCESS = "SUCCESS"
    FAIL_TWICE_THEN_SUCCEED = "FAIL_TWICE_THEN_SUCCEED"
    ALWAYS_FAIL = "ALWAYS_FAIL"


def normalize_mock_scenario(raw_scenario: str | None) -> MockScenario | None:
    if raw_scenario is None:
        return None
    return MockScenario(raw_scenario)


class CreateGenerationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mock_scenario: str | None = Field(default=None, alias="mockScenario")


class SelectImageRequest(BaseModel):
    image_id: UUID = Field(alias="imageId")


class SelectVideoRequest(BaseModel):
    video_id: UUID = Field(alias="videoId")


class GenerationJobResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    kind: GenerationKind
    version: int
    status: JobStatus
    prompt: str
    source_image_id: UUID | None = Field(serialization_alias="sourceImageId")
    attempt_count: int = Field(serialization_alias="attemptCount")
    max_attempts: int = Field(serialization_alias="maxAttempts")
    next_run_at: datetime | None = Field(serialization_alias="nextRunAt")
    last_error_code: str | None = Field(serialization_alias="lastErrorCode")
    last_error_message: str | None = Field(serialization_alias="lastErrorMessage")
    mock_scenario: MockScenario | None = Field(serialization_alias="mockScenario")


class CutImageResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    generation_job_id: UUID = Field(serialization_alias="generationJobId")
    url: str
    input_prompt: str = Field(serialization_alias="inputPrompt")
    created_at: datetime = Field(serialization_alias="createdAt")


class CutVideoResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    cut_image_id: UUID = Field(serialization_alias="cutImageId")
    generation_job_id: UUID = Field(serialization_alias="generationJobId")
    url: str
    input_prompt: str = Field(serialization_alias="inputPrompt")
    created_at: datetime = Field(serialization_alias="createdAt")


class CutResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    order: int
    image_prompt: str = Field(serialization_alias="imagePrompt")
    video_prompt: str = Field(serialization_alias="videoPrompt")
    duration_sec: Literal[5] = Field(serialization_alias="durationSec")
    selected_image_id: UUID | None = Field(default=None, serialization_alias="selectedImageId")
    selected_video_id: UUID | None = Field(default=None, serialization_alias="selectedVideoId")
    image_jobs: list[GenerationJobResponse] = Field(
        default_factory=list, serialization_alias="imageJobs"
    )
    video_jobs: list[GenerationJobResponse] = Field(
        default_factory=list, serialization_alias="videoJobs"
    )
    images: list[CutImageResponse] = Field(default_factory=list)
    videos: list[CutVideoResponse] = Field(default_factory=list)


class SceneResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    user_prompt: str = Field(serialization_alias="userPrompt")
    title: str
    scenario: str
    cuts: list[CutResponse]
