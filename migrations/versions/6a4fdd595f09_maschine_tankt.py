"""maschine_tankt

Revision ID: 6a4fdd595f09
Revises: 27cd2aedbeaa
Create Date: 2026-10-24 10:56:05.627873

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6a4fdd595f09'
down_revision = '27cd2aedbeaa'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('machines', schema=None) as batch_op:
        batch_op.add_column(sa.Column('refuels', sa.Boolean(), nullable=True))
    # Maschinen, die schon getankt wurden, aber zu einer Art ohne Tank gehören: weiter tanken lassen
    op.execute(sa.text(
        "UPDATE machines SET refuels = :yes WHERE category NOT IN "
        "('schlepper', 'fahrzeug', 'maehdrescher', 'roder', 'stapler') "
        "AND id IN (SELECT machine_id FROM fuel_entries WHERE machine_id IS NOT NULL)").bindparams(yes=True))



def downgrade():
    with op.batch_alter_table('machines', schema=None) as batch_op:
        batch_op.drop_column('refuels')

