"""users.email — required at registration, not yet verified

Registration now collects an email address (unique, case-insensitive,
same functional-index pattern as username). No verification flow yet —
the column exists so verification / password reset can be added later
without another cutover. NOT NULL is safe: the users table was empty in
every environment when this shipped (self-registration reopened after
the multi-user cutover, nobody had registered yet).

Revision ID: 3998150805cb
Revises: acb6c657deca
Create Date: 2026-07-06 11:22:34.754258

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "3998150805cb"
down_revision: Union[str, Sequence[str], None] = "acb6c657deca"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email", sa.String(length=255), nullable=False),
    )
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_column("users", "email")
