from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class GenerationKind(StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    SUBMITTING = "SUBMITTING"
    PROCESSING = "PROCESSING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


ACTIVE_JOB_STATUSES = (
    JobStatus.QUEUED,
    JobStatus.SUBMITTING,
    JobStatus.PROCESSING,
    JobStatus.RETRY_WAIT,
)
"""A job the queue still owns. Mirrors the partial unique index below."""

CLAIMABLE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RETRY_WAIT)
"""A job the worker may still pick up on its next tick."""


class Base(DeclarativeBase):
    pass


UuidPk = Annotated[
    UUID,
    mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4),
]


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[UuidPk]
    user_prompt: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(200))
    scenario: Mapped[str] = mapped_column(Text)
    # Serialized CharacterProfile list in camelCase; the anchor for cross-cut consistency.
    character_profiles: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp()
    )


class Cut(Base):
    __tablename__ = "cuts"
    __table_args__ = (
        UniqueConstraint("scene_id", "order", name="uq_cut_scene_order"),
        CheckConstraint('"order" BETWEEN 1 AND 6', name="ck_cut_order_range"),
        CheckConstraint("duration_sec = 5", name="ck_cut_duration_five"),
    )

    id: Mapped[UuidPk]
    scene_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), index=True
    )
    order: Mapped[int] = mapped_column(Integer)
    # What happens in this shot, kept separate from the scene-wide character sheet.
    shot_description: Mapped[str] = mapped_column(Text, default="")
    video_motion: Mapped[str] = mapped_column(Text, default="")
    # Final composed prompts: style guide + character sheet + shot. Stored so a regenerate
    # reuses the exact string that produced the earlier version.
    image_prompt: Mapped[str] = mapped_column(Text)
    video_prompt: Mapped[str] = mapped_column(Text)
    duration_sec: Mapped[int] = mapped_column(Integer, default=5)
    selected_image_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "cut_images.id",
            name="fk_cuts_selected_image_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    selected_video_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "cut_videos.id",
            name="fk_cuts_selected_video_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        UniqueConstraint("cut_id", "kind", "version", name="uq_generation_cut_kind_version"),
        CheckConstraint("version >= 1", name="ck_generation_version_positive"),
        CheckConstraint("attempt_count >= 0", name="ck_generation_attempt_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="ck_generation_max_attempts_positive"),
        CheckConstraint("kind IN ('IMAGE', 'VIDEO')", name="ck_generation_kind"),
        CheckConstraint(
            "status IN ('QUEUED', 'SUBMITTING', 'PROCESSING', 'RETRY_WAIT', 'SUCCEEDED', 'FAILED')",
            name="ck_generation_status",
        ),
        CheckConstraint("generation_mode IN ('MOCK', 'LIVE')", name="ck_generation_mode"),
        Index(
            "uq_active_generation_per_cut_kind",
            "cut_id",
            "kind",
            unique=True,
            sqlite_where=text("status IN ('QUEUED', 'SUBMITTING', 'PROCESSING', 'RETRY_WAIT')"),
        ),
    )

    id: Mapped[UuidPk]
    cut_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cuts.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[GenerationKind] = mapped_column(Enum(GenerationKind, native_enum=False))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False),
        default=JobStatus.QUEUED,
    )
    prompt: Mapped[str] = mapped_column(Text)
    source_image_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "cut_images.id",
            name="fk_generation_jobs_source_image_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    # Snapshot of the mode in force when the job was requested. The worker follows this value,
    # not the current runtime mode, so a mid-flight job is never handed to the other provider.
    generation_mode: Mapped[str] = mapped_column(String(10), default="MOCK")
    # The scene anchor image an IMAGE job was told to stay consistent with. Distinct from
    # source_image_id, which is the image a VIDEO job was generated from.
    reference_image_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "cut_images.id",
            name="fk_generation_jobs_reference_image_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    batch_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("generation_batches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    external_task_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    mock_scenario: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GenerationBatch(Base):
    """One scene-wide generation request. Batch progress is derived from its jobs, never stored,
    so there is no denormalized status to drift out of sync with the state machine."""

    __tablename__ = "generation_batches"
    __table_args__ = (CheckConstraint("kind IN ('IMAGE', 'VIDEO')", name="ck_batch_kind"),)

    id: Mapped[UuidPk]
    scene_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[GenerationKind] = mapped_column(Enum(GenerationKind, native_enum=False))
    requested_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp()
    )


class CutImage(Base):
    __tablename__ = "cut_images"

    id: Mapped[UuidPk]
    cut_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cuts.id", ondelete="CASCADE"), index=True
    )
    generation_job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("generation_jobs.id", ondelete="CASCADE"),
        unique=True,
    )
    url: Mapped[str] = mapped_column(Text)
    input_prompt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp()
    )


class CutVideo(Base):
    __tablename__ = "cut_videos"

    id: Mapped[UuidPk]
    cut_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cuts.id", ondelete="CASCADE"), index=True
    )
    cut_image_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cut_images.id", ondelete="RESTRICT")
    )
    generation_job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("generation_jobs.id", ondelete="CASCADE"),
        unique=True,
    )
    url: Mapped[str] = mapped_column(Text)
    input_prompt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp()
    )
