"""Bestattungswald: Person und Vertretung je Beisetzung

Revision ID: d8f2b6e0a974
Revises: c7e1a5d9f863
"""
from alembic import op
import sqlalchemy as sa

revision = "d8f2b6e0a974"
down_revision = "c7e1a5d9f863"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("forest_burials") as batch:
        batch.add_column(sa.Column("grave_person_id", sa.Integer()))
        batch.add_column(sa.Column("grave_deputy_id", sa.Integer()))
        batch.create_foreign_key("fk_forest_burials_grave_person_id_users", "users", ["grave_person_id"], ["id"],
                                 ondelete="SET NULL")
        batch.create_foreign_key("fk_forest_burials_grave_deputy_id_users", "users", ["grave_deputy_id"], ["id"],
                                 ondelete="SET NULL")


def downgrade():
    with op.batch_alter_table("forest_burials") as batch:
        batch.drop_constraint("fk_forest_burials_grave_deputy_id_users", type_="foreignkey")
        batch.drop_constraint("fk_forest_burials_grave_person_id_users", type_="foreignkey")
        batch.drop_column("grave_deputy_id")
        batch.drop_column("grave_person_id")
