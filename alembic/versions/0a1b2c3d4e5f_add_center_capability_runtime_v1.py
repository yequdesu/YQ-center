"""add_center_capability_runtime_v1

Revision ID: 0a1b2c3d4e5f
Revises: e9f0a1b2c3d4
Create Date: 2026-06-28 00:00:00.000000

"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0a1b2c3d4e5f"
down_revision: str | Sequence[str] | None = "e9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capability_definitions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("capability_id", sa.String(length=64), nullable=False),
        sa.Column("canonical_name", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("capability_type", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("agent_description", sa.Text(), nullable=True),
        sa.Column("input_schema", sa.JSON(), nullable=True),
        sa.Column("output_schema", sa.JSON(), nullable=True),
        sa.Column("value_schema", sa.JSON(), nullable=True),
        sa.Column("risk", sa.String(length=32), nullable=True),
        sa.Column("effect", sa.String(length=32), nullable=True),
        sa.Column("artifact_inputs", sa.JSON(), nullable=False),
        sa.Column("artifact_outputs", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("examples", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("capability_id"),
        sa.UniqueConstraint(
            "canonical_name",
            "capability_type",
            name="uq_capability_definitions_canonical_type",
        ),
    )
    op.create_index("ix_capability_definitions_capability_id", "capability_definitions", ["capability_id"])
    op.create_index("ix_capability_definitions_canonical_name", "capability_definitions", ["canonical_name"])

    op.create_table(
        "capability_sources",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("definition_id", sa.String(length=32), nullable=False),
        sa.Column("node_record_id", sa.String(length=32), nullable=False),
        sa.Column("plugin_id", sa.String(length=256), nullable=False),
        sa.Column("plugin_version", sa.String(length=32), nullable=False),
        sa.Column("registered_name", sa.String(length=256), nullable=False),
        sa.Column("runtime_id", sa.String(length=256), nullable=True),
        sa.Column("platform_os", sa.String(length=32), nullable=True),
        sa.Column("platform_arch", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("unavailable_reason", sa.Text(), nullable=True),
        sa.Column("execution_requirements", sa.JSON(), nullable=True),
        sa.Column("timeout_sec", sa.Integer(), nullable=True),
        sa.Column("idempotency", sa.String(length=32), nullable=True),
        sa.Column("resource_keys", sa.JSON(), nullable=True),
        sa.Column("conflict_policy", sa.String(length=32), nullable=True),
        sa.Column("hidden_input_fields", sa.JSON(), nullable=True),
        sa.Column("failure_modes", sa.JSON(), nullable=True),
        sa.Column("preflight_supported", sa.Boolean(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=True),
        sa.Column("ttl_sec", sa.Integer(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["definition_id"], ["capability_definitions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_record_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id"),
        sa.UniqueConstraint(
            "node_record_id",
            "plugin_id",
            "registered_name",
            "definition_id",
            name="uq_capability_sources_node_plugin_name_definition",
        ),
    )
    op.create_index("ix_capability_sources_definition_id", "capability_sources", ["definition_id"])
    op.create_index("ix_capability_sources_node_record_id", "capability_sources", ["node_record_id"])
    op.create_index("ix_capability_sources_plugin_id", "capability_sources", ["plugin_id"])
    op.create_index("ix_capability_sources_registered_name", "capability_sources", ["registered_name"])
    op.create_index("ix_capability_sources_runtime_id", "capability_sources", ["runtime_id"])
    op.create_index("ix_capability_sources_source_id", "capability_sources", ["source_id"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("session_id", sa.String(length=32), nullable=True),
        sa.Column("invocation_id", sa.String(length=32), nullable=True),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("node_id", sa.String(length=128), nullable=True),
        sa.Column("capability_source_id", sa.String(length=64), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id"),
    )
    for column in ("artifact_id", "artifact_type", "session_id", "invocation_id", "job_id", "node_id", "capability_source_id"):
        op.create_index(f"ix_artifacts_{column}", "artifacts", [column])

    op.create_table(
        "artifact_blobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("blob_id", sa.String(length=64), nullable=False),
        sa.Column("artifact_id", sa.String(length=32), nullable=False),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("blob_id"),
    )
    op.create_index("ix_artifact_blobs_artifact_id", "artifact_blobs", ["artifact_id"])
    op.create_index("ix_artifact_blobs_blob_id", "artifact_blobs", ["blob_id"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("turn_id", sa.String(length=32), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("provider_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("execution_mode", sa.String(length=16), nullable=False),
        sa.Column("target_node_id", sa.String(length=128), nullable=True),
        sa.Column("user_message", sa.Text(), nullable=True),
        sa.Column("final_message", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    for column in ("run_id", "session_id", "turn_id", "trace_id", "status"):
        op.create_index(f"ix_agent_runs_{column}", "agent_runs", [column])

    op.create_table(
        "agent_run_steps",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("run_record_id", sa.String(length=32), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("parent_step_id", sa.String(length=64), nullable=True),
        sa.Column("tool_call_id", sa.String(length=128), nullable=True),
        sa.Column("capability_id", sa.String(length=64), nullable=True),
        sa.Column("capability_source_id", sa.String(length=64), nullable=True),
        sa.Column("node_id", sa.String(length=128), nullable=True),
        sa.Column("invocation_id", sa.String(length=32), nullable=True),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("input_data", sa.JSON(), nullable=True),
        sa.Column("output_data", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_record_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("step_id"),
    )
    for column in (
        "step_id",
        "run_record_id",
        "status",
        "parent_step_id",
        "tool_call_id",
        "capability_id",
        "capability_source_id",
        "node_id",
        "invocation_id",
        "job_id",
    ):
        op.create_index(f"ix_agent_run_steps_{column}", "agent_run_steps", [column])


def downgrade() -> None:
    for column in (
        "job_id",
        "invocation_id",
        "node_id",
        "capability_source_id",
        "capability_id",
        "tool_call_id",
        "parent_step_id",
        "status",
        "run_record_id",
        "step_id",
    ):
        op.drop_index(f"ix_agent_run_steps_{column}", table_name="agent_run_steps")
    op.drop_table("agent_run_steps")

    for column in ("status", "trace_id", "turn_id", "session_id", "run_id"):
        op.drop_index(f"ix_agent_runs_{column}", table_name="agent_runs")
    op.drop_table("agent_runs")

    op.drop_index("ix_artifact_blobs_blob_id", table_name="artifact_blobs")
    op.drop_index("ix_artifact_blobs_artifact_id", table_name="artifact_blobs")
    op.drop_table("artifact_blobs")

    for column in ("capability_source_id", "node_id", "job_id", "invocation_id", "session_id", "artifact_type", "artifact_id"):
        op.drop_index(f"ix_artifacts_{column}", table_name="artifacts")
    op.drop_table("artifacts")

    op.drop_index("ix_capability_sources_source_id", table_name="capability_sources")
    op.drop_index("ix_capability_sources_runtime_id", table_name="capability_sources")
    op.drop_index("ix_capability_sources_registered_name", table_name="capability_sources")
    op.drop_index("ix_capability_sources_plugin_id", table_name="capability_sources")
    op.drop_index("ix_capability_sources_node_record_id", table_name="capability_sources")
    op.drop_index("ix_capability_sources_definition_id", table_name="capability_sources")
    op.drop_table("capability_sources")

    op.drop_index("ix_capability_definitions_canonical_name", table_name="capability_definitions")
    op.drop_index("ix_capability_definitions_capability_id", table_name="capability_definitions")
    op.drop_table("capability_definitions")
