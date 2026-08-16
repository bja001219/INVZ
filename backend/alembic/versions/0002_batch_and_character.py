"""Add character sheet, shot separation, job traceability, and generation batches.

Revision ID: 0002_batch_and_character
Revises: 0001_initial
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_batch_and_character"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generation_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scene_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("IMAGE", "VIDEO", name="generationkind", native_enum=False),
            nullable=False,
        ),
        sa.Column("requested_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('IMAGE', 'VIDEO')", name="ck_batch_kind"),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_generation_batches_scene_id", "generation_batches", ["scene_id"], unique=False
    )

    # Existing rows predate the character sheet, so they backfill to an empty cast and empty
    # shot fields. Their stored prompts stay exactly as generated.
    with op.batch_alter_table("scenes") as batch_op:
        batch_op.add_column(
            sa.Column("character_profiles", sa.JSON(), nullable=False, server_default="[]")
        )
    with op.batch_alter_table("cuts") as batch_op:
        batch_op.add_column(
            sa.Column("shot_description", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("video_motion", sa.Text(), nullable=False, server_default="")
        )

    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "generation_mode", sa.String(length=10), nullable=False, server_default="MOCK"
            )
        )
        batch_op.add_column(sa.Column("reference_image_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("batch_id", sa.Uuid(), nullable=True))
        batch_op.create_check_constraint(
            "ck_generation_mode", "generation_mode IN ('MOCK', 'LIVE')"
        )
        batch_op.create_foreign_key(
            "fk_generation_jobs_reference_image_id",
            "cut_images",
            ["reference_image_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_generation_jobs_batch_id",
            "generation_batches",
            ["batch_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_generation_jobs_batch_id", "generation_jobs", ["batch_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_generation_jobs_batch_id", table_name="generation_jobs")
    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.drop_constraint("fk_generation_jobs_batch_id", type_="foreignkey")
        batch_op.drop_constraint("fk_generation_jobs_reference_image_id", type_="foreignkey")
        batch_op.drop_constraint("ck_generation_mode", type_="check")
        batch_op.drop_column("batch_id")
        batch_op.drop_column("reference_image_id")
        batch_op.drop_column("generation_mode")
    with op.batch_alter_table("cuts") as batch_op:
        batch_op.drop_column("video_motion")
        batch_op.drop_column("shot_description")
    with op.batch_alter_table("scenes") as batch_op:
        batch_op.drop_column("character_profiles")
    op.drop_index("ix_generation_batches_scene_id", table_name="generation_batches")
    op.drop_table("generation_batches")
