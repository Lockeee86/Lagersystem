"""Bestattungswald: Fotos am Baum

Revision ID: c4e8a2d6f913
Revises: b7d3f1a9c542
"""
from alembic import op
import sqlalchemy as sa

revision = "c4e8a2d6f913"
down_revision = "b7d3f1a9c542"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("photos") as batch:
        batch.add_column(sa.Column("forest_tree_id", sa.Integer()))
        batch.create_foreign_key("fk_photos_forest_tree_id_forest_trees", "forest_trees", ["forest_tree_id"], ["id"],
                                 ondelete="CASCADE")
        batch.create_index("ix_photos_forest_tree_id", ["forest_tree_id"])


def downgrade():
    with op.batch_alter_table("photos") as batch:
        batch.drop_index("ix_photos_forest_tree_id")
        batch.drop_constraint("fk_photos_forest_tree_id_forest_trees", type_="foreignkey")
        batch.drop_column("forest_tree_id")
