"""Drop leftover understanding columns from discovered_videos.

The understanding pipeline moved to the videos table (writer flow);
these columns stayed behind in the schema while the model dropped
them — the only model/migration drift in the codebase (found by the
2026-07-03 schema audit). Production data check before writing this:
`understanding` is NULL on all 13868 rows, `understanding_status`
holds only its column default. No information is lost.

Revision ID: b7c2e4f6a8d1
Revises: a30d4cb53cc7
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa

revision = "b7c2e4f6a8d1"
down_revision = "a30d4cb53cc7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("discovered_videos", "understanding")
    op.drop_column("discovered_videos", "understanding_status")


def downgrade() -> None:
    op.add_column(
        "discovered_videos",
        sa.Column("understanding", sa.Text(), nullable=True),
    )
    op.add_column(
        "discovered_videos",
        sa.Column(
            "understanding_status", sa.Text(),
            server_default="pending", nullable=True,
        ),
    )
