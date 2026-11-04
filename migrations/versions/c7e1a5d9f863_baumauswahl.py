"""Bestattungswald: Baumauswahl-Termine

Revision ID: c7e1a5d9f863
Revises: b6d0f4c8e752
"""
from alembic import op
import sqlalchemy as sa

revision = "c7e1a5d9f863"
down_revision = "b6d0f4c8e752"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forest_selections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("time", sa.String(length=5)),
        sa.Column("contact", sa.String(length=150), nullable=False),
        sa.Column("phone", sa.String(length=60)),
        sa.Column("email", sa.String(length=120)),
        sa.Column("deceased", sa.String(length=150)),
        sa.Column("places_wanted", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("kind_wanted", sa.String(length=20)),
        sa.Column("guide_id", sa.Integer()),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="geplant"),
        sa.Column("tree_id", sa.Integer()),
        sa.Column("place_numbers", sa.String(length=200)),
        sa.Column("note", sa.Text()),
        sa.Column("task_id", sa.Integer()),
        sa.Column("contract_id", sa.Integer()),
        sa.Column("created_by_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["guide_id"], ["users.id"], name="fk_forest_selections_guide_id_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tree_id"], ["forest_trees.id"], name="fk_forest_selections_tree_id_forest_trees",
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_forest_selections_task_id_tasks", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["contract_id"], ["forest_contracts.id"],
                                name="fk_forest_selections_contract_id_forest_contracts", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], name="fk_forest_selections_created_by_id_users",
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_forest_selections"),
    )
    op.create_index("ix_forest_selections_day", "forest_selections", ["day"])
    op.create_index("ix_forest_selections_tree_id", "forest_selections", ["tree_id"])
    with op.batch_alter_table("forest_burials") as batch:
        batch.add_column(sa.Column("selection_id", sa.Integer()))
        batch.create_foreign_key("fk_forest_burials_selection_id_forest_selections", "forest_selections",
                                 ["selection_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_forest_burials_selection_id", ["selection_id"])


def downgrade():
    with op.batch_alter_table("forest_burials") as batch:
        batch.drop_index("ix_forest_burials_selection_id")
        batch.drop_constraint("fk_forest_burials_selection_id_forest_selections", type_="foreignkey")
        batch.drop_column("selection_id")
    op.drop_index("ix_forest_selections_tree_id", table_name="forest_selections")
    op.drop_index("ix_forest_selections_day", table_name="forest_selections")
    op.drop_table("forest_selections")
