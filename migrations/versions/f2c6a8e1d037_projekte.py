"""Projekte: Vorhaben mit Schritten (Aufgaben), Tagebuch, Dateien und Fotos

Revision ID: f2c6a8e1d037
Revises: e8b2c4d6f915
"""
import sqlalchemy as sa
from alembic import op

revision = "f2c6a8e1d037"
down_revision = "e8b2c4d6f915"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("start_on", sa.Date()),
        sa.Column("target_on", sa.Date()),
        sa.Column("finished_on", sa.Date()),
        sa.Column("budget", sa.Numeric(10, 2)),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_projects_status", "projects", ["status"])
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_table(
        "project_members",
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])
    op.create_table(
        "project_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2)),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_entries_project_id", "project_entries", ["project_id"])
    op.create_table(
        "project_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("filename", sa.String(80), nullable=False, unique=True),
        sa.Column("original_name", sa.String(200)),
        sa.Column("mimetype", sa.String(50), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_documents_project_id", "project_documents", ["project_id"])
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("project_position", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("milestone", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_foreign_key("fk_tasks_project_id", "projects", ["project_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_tasks_project_id", ["project_id"])
    with op.batch_alter_table("photos") as batch:
        batch.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_photos_project_id", "projects", ["project_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_photos_project_id", ["project_id"])


def downgrade():
    with op.batch_alter_table("photos") as batch:
        batch.drop_index("ix_photos_project_id")
        batch.drop_constraint("fk_photos_project_id", type_="foreignkey")
        batch.drop_column("project_id")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_index("ix_tasks_project_id")
        batch.drop_constraint("fk_tasks_project_id", type_="foreignkey")
        batch.drop_column("milestone")
        batch.drop_column("project_position")
        batch.drop_column("project_id")
    op.drop_table("project_documents")
    op.drop_table("project_entries")
    op.drop_table("project_members")
    op.drop_table("projects")
