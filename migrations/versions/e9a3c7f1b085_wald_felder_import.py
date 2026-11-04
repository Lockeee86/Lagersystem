"""Bestattungswald: Feld je Baum (A/B), Kategorie, Stamm; Vorsorge-Person am Platz; Daten aus dem Altsystem

Revision ID: e9a3c7f1b085
Revises: d8f2b6e0a974
"""
from alembic import op
import sqlalchemy as sa

revision = "e9a3c7f1b085"
down_revision = "d8f2b6e0a974"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("forest_trees") as batch:
        batch.add_column(sa.Column("field", sa.String(length=10), nullable=False, server_default=""))
        batch.add_column(sa.Column("category", sa.String(length=10)))
        batch.add_column(sa.Column("trunk_cm", sa.Integer()))
        batch.drop_constraint("uq_forest_trees_number", type_="unique")
        batch.create_unique_constraint("uq_forest_trees_field_number", ["field", "number"])
    with op.batch_alter_table("forest_places") as batch:
        batch.add_column(sa.Column("person", sa.String(length=150)))
    with op.batch_alter_table("forest_contracts") as batch:
        batch.add_column(sa.Column("holder_birthday", sa.Date()))
    with op.batch_alter_table("forest_burials") as batch:
        batch.add_column(sa.Column("died_on", sa.Date()))
        batch.add_column(sa.Column("died_in", sa.String(length=100)))
        batch.add_column(sa.Column("number", sa.String(length=20)))


def downgrade():
    with op.batch_alter_table("forest_burials") as batch:
        batch.drop_column("number")
        batch.drop_column("died_in")
        batch.drop_column("died_on")
    with op.batch_alter_table("forest_contracts") as batch:
        batch.drop_column("holder_birthday")
    with op.batch_alter_table("forest_places") as batch:
        batch.drop_column("person")
    with op.batch_alter_table("forest_trees") as batch:
        batch.drop_constraint("uq_forest_trees_field_number", type_="unique")
        batch.create_unique_constraint("uq_forest_trees_number", ["number"])
        batch.drop_column("trunk_cm")
        batch.drop_column("category")
        batch.drop_column("field")
