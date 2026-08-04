"""Add workspace isolation and encrypted workspace secrets.

Revision ID: 7b2d9f4a1c63
Revises: 3998150805cb
"""

import os

from alembic import op
import sqlalchemy as sa

revision = "7b2d9f4a1c63"
down_revision = "3998150805cb"
branch_labels = None
depends_on = None


TENANT_TABLES = (
    "groups", "caption_prompts", "comment_prompts", "accounts", "videos",
    "publications", "publication_attempts", "engagement_tasks",
    "engagement_runs", "settings", "job_runs", "geelark_transactions",
    "discovered_videos", "phone_sessions", "task_runs", "video_assets",
    "writer_audit", "outbox", "cost_attributions", "geelark_callback_log",
    "callback_inbox",
)
NULLABLE_WORKSPACE_TABLES = {"geelark_callback_log", "callback_inbox"}


def _legacy_workspace_id() -> int:
    raw = os.getenv("STUDIO_LEGACY_WORKSPACE_ID", "1")
    value = int(raw)
    if value <= 0:
        raise RuntimeError("STUDIO_LEGACY_WORKSPACE_ID must be a positive integer")
    return value


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    present = set(inspector.get_table_names())
    legacy_id = _legacy_workspace_id()
    default_sql = (
        "COALESCE(NULLIF(current_setting('app.workspace_id', true), '')::bigint, "
        f"{legacy_id})"
    )

    for table in TENANT_TABLES:
        if table not in present:
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "workspace_id" not in columns:
            op.add_column(
                table,
                sa.Column("workspace_id", sa.BigInteger(), nullable=True),
            )
        op.execute(sa.text(f'UPDATE "{table}" SET workspace_id = :workspace_id '
                           "WHERE workspace_id IS NULL").bindparams(workspace_id=legacy_id))
        op.alter_column(
            table,
            "workspace_id",
            nullable=table in NULLABLE_WORKSPACE_TABLES,
            server_default=None if table in NULLABLE_WORKSPACE_TABLES
            else sa.text(default_sql),
        )
        op.create_index(
            f"idx_{table}_workspace_id", table, ["workspace_id"], unique=False,
        )

    # Settings were globally keyed. The workspace becomes part of its identity.
    if "settings" in present:
        pk = inspector.get_pk_constraint("settings")
        if pk.get("name"):
            op.drop_constraint(pk["name"], "settings", type_="primary")
        op.create_primary_key("settings_pkey", "settings", ["workspace_id", "key"])

    for table, identity_column in (
        ("geelark_transactions", "id"),
        ("cost_attributions", "transaction_id"),
    ):
        if table in present:
            pk = inspector.get_pk_constraint(table)
            if pk.get("name"):
                op.drop_constraint(pk["name"], table, type_="primary")
            op.create_primary_key(
                f"{table}_pkey", table, ["workspace_id", identity_column],
            )

    # PostgreSQL global UNIQUE constraints would let one tenant's name block
    # another tenant. Replace the known business keys with workspace keys.
    unique_specs = [
        ("groups", ("name",)),
        ("caption_prompts", ("name",)),
        ("comment_prompts", ("name",)),
        ("accounts", ("geelark_phone_id",)),
        ("accounts", ("profile_name",)),
        ("publications", ("video_id", "platform")),
        ("discovered_videos", ("url",)),
        ("video_assets", ("video_id", "idx")),
    ]
    for table, columns in unique_specs:
        if table not in present:
            continue
        for constraint in inspector.get_unique_constraints(table):
            if tuple(constraint.get("column_names") or ()) == columns:
                op.drop_constraint(constraint["name"], table, type_="unique")
        for index in inspector.get_indexes(table):
            if (index.get("unique")
                    and not index.get("duplicates_constraint")
                    and tuple(index.get("column_names") or ()) == columns):
                op.drop_index(index["name"], table_name=table)
        suffix = "_".join(columns)
        op.create_unique_constraint(
            f"uq_{table}_workspace_{suffix}", table,
            ["workspace_id", *columns],
        )

    if "callback_inbox" in present:
        for constraint in inspector.get_unique_constraints("callback_inbox"):
            if tuple(constraint.get("column_names") or ()) == (
                "task_id", "status", "body_hash",
            ):
                op.drop_constraint(
                    constraint["name"], "callback_inbox", type_="unique",
                )
        op.create_index(
            "uq_callback_inbox_workspace_event",
            "callback_inbox",
            [sa.text("COALESCE(workspace_id, 0)"), "task_id", "status", "body_hash"],
            unique=True,
        )

    if "outbox" in present:
        op.drop_index("uq_outbox_inflight_business_ref", table_name="outbox")
        op.create_index(
            "uq_outbox_workspace_inflight_business_ref",
            "outbox", ["workspace_id", "intent", "business_ref"],
            unique=True, postgresql_where=sa.text("sent_at IS NULL"),
        )

    op.create_table(
        "workspace_secrets",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("workspace_id", "key"),
    )

    # FORCE makes isolation apply even when the application role owns the table.
    # Background maintenance must opt in explicitly via app.workspace_bypass.
    for table in (*TENANT_TABLES, "workspace_secrets"):
        if table not in present and table != "workspace_secrets":
            continue
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'''
            CREATE POLICY workspace_isolation ON "{table}"
            USING (
                workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::bigint
                OR current_setting('app.workspace_bypass', true) = 'on'
            )
            WITH CHECK (
                workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::bigint
                OR current_setting('app.workspace_bypass', true) = 'on'
            )
        ''')


def downgrade() -> None:
    bind = op.get_bind()
    present = set(sa.inspect(bind).get_table_names())
    op.drop_table("workspace_secrets")

    # Restore every legacy uniqueness contract before older revisions run.
    for table, columns in (
        ("groups", ("name",)),
        ("caption_prompts", ("name",)),
        ("comment_prompts", ("name",)),
        ("accounts", ("geelark_phone_id",)),
        ("accounts", ("profile_name",)),
        ("publications", ("video_id", "platform")),
        ("discovered_videos", ("url",)),
        ("video_assets", ("video_id", "idx")),
    ):
        if table in present:
            op.drop_constraint(
                f"uq_{table}_workspace_{'_'.join(columns)}",
                table, type_="unique",
            )

    if "groups" in present:
        op.create_index("ix_groups_name", "groups", ["name"], unique=True)
    if "caption_prompts" in present:
        op.create_unique_constraint(
            "caption_prompts_name_key", "caption_prompts", ["name"],
        )
    if "comment_prompts" in present:
        op.create_unique_constraint(
            "comment_prompts_name_key", "comment_prompts", ["name"],
        )
    if "accounts" in present:
        op.create_index(
            "ix_accounts_geelark_phone_id", "accounts",
            ["geelark_phone_id"], unique=True,
        )
        op.create_index(
            "ix_accounts_profile_name", "accounts", ["profile_name"], unique=True,
        )
    if "publications" in present:
        op.create_unique_constraint(
            "uq_publications_video_platform", "publications", ["video_id", "platform"],
        )
    if "discovered_videos" in present:
        op.create_unique_constraint(
            "uq_discovered_videos_url", "discovered_videos", ["url"],
        )
    if "video_assets" in present:
        op.create_unique_constraint(
            "uq_video_assets_video_idx", "video_assets", ["video_id", "idx"],
        )

    for table, identity_column in (
        ("settings", "key"),
        ("geelark_transactions", "id"),
        ("cost_attributions", "transaction_id"),
    ):
        if table in present:
            op.drop_constraint(f"{table}_pkey", table, type_="primary")
            op.create_primary_key(f"{table}_pkey", table, [identity_column])

    if "callback_inbox" in present:
        op.drop_index(
            "uq_callback_inbox_workspace_event", table_name="callback_inbox",
        )
        op.create_unique_constraint(
            "uq_callback_inbox_event", "callback_inbox",
            ["task_id", "status", "body_hash"],
        )
    if "outbox" in present:
        op.drop_index(
            "uq_outbox_workspace_inflight_business_ref", table_name="outbox",
        )
        op.create_index(
            "uq_outbox_inflight_business_ref", "outbox",
            ["intent", "business_ref"], unique=True,
            postgresql_where=sa.text("sent_at IS NULL"),
        )
    for table in reversed(TENANT_TABLES):
        if table not in present:
            continue
        op.execute(f'DROP POLICY IF EXISTS workspace_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
        op.drop_index(f"idx_{table}_workspace_id", table_name=table)
        op.drop_column(table, "workspace_id")
