"""users table for multi-user dashboard auth

Replaces the single shared DASHBOARD_PASSWORD login. password_hash is
'scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>' (stdlib hashlib.scrypt with a
per-user random salt; params live inside each hash so they can be raised
later without breaking existing rows). session_ver is embedded in every
session cookie and bumped on password change so previously issued
cookies stop validating. Registration is gated by the REGISTER_CODE env
(empty = registration disabled).

Revision ID: acb6c657deca
Revises: b7c2e4f6a8d1
Create Date: 2026-07-03 17:31:12.977609

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "acb6c657deca"
down_revision: Union[str, Sequence[str], None] = "b7c2e4f6a8d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "session_ver", sa.Integer(),
            nullable=False, server_default="1",
        ),
        sa.Column(
            "created_at", sa.DateTime(),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    # Case-insensitive uniqueness ('Alice' and 'alice' are the same
    # account). Functional index instead of a plain unique column so the
    # original casing is preserved for display.
    op.create_index(
        "uq_users_username_lower",
        "users",
        [sa.text("lower(username)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_users_username_lower", table_name="users")
    op.drop_table("users")
