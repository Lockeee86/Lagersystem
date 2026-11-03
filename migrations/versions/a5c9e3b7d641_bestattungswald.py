"""bestattungswald: bäume, plätze, verträge, beisetzungen; rubrik je benutzer (admins eingeschaltet)

Revision ID: a5c9e3b7d641
Revises: f4b8d2a6c530
Create Date: 2026-11-04 09:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a5c9e3b7d641'
down_revision = 'f4b8d2a6c530'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('forest', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE users SET forest = true WHERE role = 'admin'")
    op.create_table(
        'forest_trees',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('species', sa.String(length=60)),
        sa.Column('section', sa.String(length=60)),
        sa.Column('kind', sa.String(length=20), nullable=False, server_default='gemeinschaft'),
        sa.Column('lat', sa.Float()),
        sa.Column('lon', sa.Float()),
        sa.Column('note', sa.Text()),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_forest_trees'),
        sa.UniqueConstraint('number', name='uq_forest_trees_number'),
    )
    op.create_index('ix_forest_trees_section', 'forest_trees', ['section'])
    op.create_table(
        'forest_contracts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('number', sa.String(length=20), nullable=False),
        sa.Column('holder', sa.String(length=150), nullable=False),
        sa.Column('address', sa.Text()),
        sa.Column('phone', sa.String(length=60)),
        sa.Column('email', sa.String(length=120)),
        sa.Column('signed_on', sa.Date(), nullable=False),
        sa.Column('ends_on', sa.Date()),
        sa.Column('price', sa.Numeric(10, 2)),
        sa.Column('note', sa.Text()),
        sa.Column('created_by_id', sa.Integer()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_forest_contracts_created_by_id_users',
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name='pk_forest_contracts'),
        sa.UniqueConstraint('number', name='uq_forest_contracts_number'),
    )
    op.create_table(
        'forest_places',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tree_id', sa.Integer(), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('contract_id', sa.Integer()),
        sa.ForeignKeyConstraint(['tree_id'], ['forest_trees.id'], name='fk_forest_places_tree_id_forest_trees',
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['contract_id'], ['forest_contracts.id'],
                                name='fk_forest_places_contract_id_forest_contracts', ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name='pk_forest_places'),
        sa.UniqueConstraint('tree_id', 'number', name='uq_forest_places_tree_number'),
    )
    op.create_index('ix_forest_places_tree_id', 'forest_places', ['tree_id'])
    op.create_index('ix_forest_places_contract_id', 'forest_places', ['contract_id'])
    op.create_table(
        'forest_burials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('place_id', sa.Integer(), nullable=False),
        sa.Column('contract_id', sa.Integer()),
        sa.Column('deceased', sa.String(length=150), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('time', sa.String(length=5)),
        sa.Column('undertaker', sa.String(length=150)),
        sa.Column('note', sa.Text()),
        sa.Column('dig_task_id', sa.Integer()),
        sa.Column('close_task_id', sa.Integer()),
        sa.Column('created_by_id', sa.Integer()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['place_id'], ['forest_places.id'], name='fk_forest_burials_place_id_forest_places',
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['contract_id'], ['forest_contracts.id'],
                                name='fk_forest_burials_contract_id_forest_contracts', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['dig_task_id'], ['tasks.id'], name='fk_forest_burials_dig_task_id_tasks',
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['close_task_id'], ['tasks.id'], name='fk_forest_burials_close_task_id_tasks',
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_forest_burials_created_by_id_users',
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name='pk_forest_burials'),
    )
    op.create_index('ix_forest_burials_place_id', 'forest_burials', ['place_id'])
    op.create_index('ix_forest_burials_contract_id', 'forest_burials', ['contract_id'])
    op.create_index('ix_forest_burials_day', 'forest_burials', ['day'])


def downgrade():
    op.drop_table('forest_burials')
    op.drop_table('forest_places')
    op.drop_table('forest_contracts')
    op.drop_table('forest_trees')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('forest')
