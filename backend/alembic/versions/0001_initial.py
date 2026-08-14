"""Create the initial Scene and generation schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_prompt", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("scenario", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "cuts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scene_id", sa.Uuid(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("image_prompt", sa.Text(), nullable=False),
        sa.Column("video_prompt", sa.Text(), nullable=False),
        sa.Column("duration_sec", sa.Integer(), nullable=False),
        sa.Column("selected_image_id", sa.Uuid(), nullable=True),
        sa.Column("selected_video_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint('"order" BETWEEN 1 AND 6', name="ck_cut_order_range"),
        sa.CheckConstraint("duration_sec = 5", name="ck_cut_duration_five"),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scene_id", "order", name="uq_cut_scene_order"),
    )
    op.create_index("ix_cuts_scene_id", "cuts", ["scene_id"], unique=False)
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cut_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "IMAGE",
                "VIDEO",
                name="generationkind",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "SUBMITTING",
                "PROCESSING",
                "RETRY_WAIT",
                "SUCCEEDED",
                "FAILED",
                name="jobstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("source_image_id", sa.Uuid(), nullable=True),
        sa.Column("external_task_id", sa.String(length=200), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("mock_scenario", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_generation_attempt_nonnegative"),
        sa.CheckConstraint("kind IN ('IMAGE', 'VIDEO')", name="ck_generation_kind"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_generation_max_attempts_positive"),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'SUBMITTING', 'PROCESSING', 'RETRY_WAIT', 'SUCCEEDED', 'FAILED')",
            name="ck_generation_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_generation_version_positive"),
        sa.ForeignKeyConstraint(["cut_id"], ["cuts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cut_id", "kind", "version", name="uq_generation_cut_kind_version"),
    )
    op.create_index("ix_generation_jobs_cut_id", "generation_jobs", ["cut_id"], unique=False)
    op.create_index(
        "uq_active_generation_per_cut_kind",
        "generation_jobs",
        ["cut_id", "kind"],
        unique=True,
        sqlite_where=sa.text("status IN ('QUEUED', 'SUBMITTING', 'PROCESSING', 'RETRY_WAIT')"),
    )
    op.create_table(
        "cut_images",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cut_id", sa.Uuid(), nullable=False),
        sa.Column("generation_job_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("input_prompt", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["cut_id"], ["cuts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generation_job_id"], ["generation_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generation_job_id"),
    )
    op.create_index("ix_cut_images_cut_id", "cut_images", ["cut_id"], unique=False)
    op.create_table(
        "cut_videos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cut_id", sa.Uuid(), nullable=False),
        sa.Column("cut_image_id", sa.Uuid(), nullable=False),
        sa.Column("generation_job_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("input_prompt", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["cut_id"], ["cuts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cut_image_id"], ["cut_images.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["generation_job_id"], ["generation_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generation_job_id"),
    )
    op.create_index("ix_cut_videos_cut_id", "cut_videos", ["cut_id"], unique=False)
    with op.batch_alter_table("cuts") as batch_op:
        batch_op.create_foreign_key(
            "fk_cuts_selected_image_id",
            "cut_images",
            ["selected_image_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_cuts_selected_video_id",
            "cut_videos",
            ["selected_video_id"],
            ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.create_foreign_key(
            "fk_generation_jobs_source_image_id",
            "cut_images",
            ["source_image_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.drop_constraint("fk_generation_jobs_source_image_id", type_="foreignkey")
    with op.batch_alter_table("cuts") as batch_op:
        batch_op.drop_constraint("fk_cuts_selected_video_id", type_="foreignkey")
        batch_op.drop_constraint("fk_cuts_selected_image_id", type_="foreignkey")
    op.drop_index("ix_cut_videos_cut_id", table_name="cut_videos")
    op.drop_table("cut_videos")
    op.drop_index("ix_cut_images_cut_id", table_name="cut_images")
    op.drop_table("cut_images")
    op.drop_index("uq_active_generation_per_cut_kind", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_cut_id", table_name="generation_jobs")
    op.drop_table("generation_jobs")
    op.drop_index("ix_cuts_scene_id", table_name="cuts")
    op.drop_table("cuts")
    op.drop_table("scenes")
