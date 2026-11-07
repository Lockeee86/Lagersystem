"""Benutzer: Haken „BGA füttern“ für den Fütterungsplan

Revision ID: f2c9a4e7d651
Revises: e5b2d8f4a173
"""
from alembic import op
import sqlalchemy as sa

revision = "f2c9a4e7d651"
down_revision = "e5b2d8f4a173"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("feeds_plant", sa.Boolean(), nullable=False, server_default=sa.false()))
    # wer schon im Fütterungsplan steht, füttert offenbar – Haken gleich setzen
    op.execute("UPDATE users SET feeds_plant = true WHERE id IN (SELECT DISTINCT user_id FROM feeding_shifts)")


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("feeds_plant")
