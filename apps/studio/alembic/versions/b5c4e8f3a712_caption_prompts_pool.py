"""caption_prompts pool + groups.caption_prompt_id

Replaces the per-group free-text `caption_brief` column and the global
`settings.caption_brief` fallback with a reusable prompt library.

- create table caption_prompts(id, name UNIQUE, body, created_at, updated_at)
- add groups.caption_prompt_id FK (ON DELETE SET NULL)
- for each group with a non-empty caption_brief, create a prompt row
  named after the group and point caption_prompt_id at it
- drop the old groups.caption_brief column
- drop the global settings.caption_brief row (it is no longer consulted;
  groups without a prompt are blocked from publishing instead)

Revision ID: b5c4e8f3a712
Revises: 566020ad4ad1
Create Date: 2026-06-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5c4e8f3a712"
down_revision: Union[str, Sequence[str], None] = "566020ad4ad1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "caption_prompts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime, nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime, nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(
        "groups",
        sa.Column(
            "caption_prompt_id",
            sa.Integer,
            sa.ForeignKey("caption_prompts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    conn = op.get_bind()
    groups_with_brief = conn.execute(sa.text(
        "SELECT id, name, caption_brief FROM groups "
        "WHERE caption_brief IS NOT NULL "
        "  AND length(trim(caption_brief)) > 0 "
        "ORDER BY id"
    )).fetchall()
    used_names: set[str] = set()
    for row in groups_with_brief:
        gid, gname, brief = row[0], row[1], row[2]
        base = (gname or f"group-{gid}").strip() or f"group-{gid}"
        candidate = base
        suffix = 2
        while candidate in used_names:
            candidate = f"{base} ({suffix})"
            suffix += 1
        used_names.add(candidate)
        result = conn.execute(
            sa.text(
                "INSERT INTO caption_prompts (name, body) "
                "VALUES (:name, :body) RETURNING id"
            ),
            {"name": candidate, "body": brief},
        )
        prompt_id = result.scalar_one()
        conn.execute(
            sa.text(
                "UPDATE groups SET caption_prompt_id = :pid WHERE id = :gid"
            ),
            {"pid": prompt_id, "gid": gid},
        )

    op.drop_column("groups", "caption_brief")
    conn.execute(sa.text("DELETE FROM settings WHERE key = 'caption_brief'"))


def downgrade() -> None:
    op.add_column(
        "groups",
        sa.Column("caption_brief", sa.String, nullable=True),
    )
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE groups g "
        "SET caption_brief = cp.body "
        "FROM caption_prompts cp "
        "WHERE g.caption_prompt_id = cp.id"
    ))
    with op.batch_alter_table("groups") as batch:
        batch.drop_column("caption_prompt_id")
    op.drop_table("caption_prompts")
