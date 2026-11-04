"""Bestattungswald: Grundkarte (Wege, Flächen, Andachtsplatz aus CAD/GIS)

Revision ID: a2c6e0b4d318
Revises: f1b5d9a3c217
"""
from alembic import op
import sqlalchemy as sa

revision = "a2c6e0b4d318"
down_revision = "f1b5d9a3c217"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forest_shapes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("layer", sa.String(length=100)),
        sa.Column("points", sa.Text(), nullable=False),
        sa.Column("line", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(length=150)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_forest_shapes"),
    )


def downgrade():
    op.drop_table("forest_shapes")
