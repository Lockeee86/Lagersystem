"""feldnotizen

Revision ID: bc37d0a31abb
Revises: 6a4fdd595f09
Create Date: 2026-10-24 13:44:17.135499

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bc37d0a31abb'
down_revision = '6a4fdd595f09'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('field_notes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('plot_year_id', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('lat', sa.Numeric(precision=9, scale=6), nullable=True),
    sa.Column('lon', sa.Numeric(precision=9, scale=6), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['plot_year_id'], ['plot_years.id'], name='fk_field_notes_plot_year_id', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_field_notes_user_id'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('field_notes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_field_notes_plot_year_id'), ['plot_year_id'], unique=False)

    with op.batch_alter_table('photos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('field_note_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_photos_field_note_id'), ['field_note_id'], unique=False)
        batch_op.create_foreign_key('fk_photos_field_note_id', 'field_notes', ['field_note_id'], ['id'])



def downgrade():
    with op.batch_alter_table('photos', schema=None) as batch_op:
        batch_op.drop_constraint('fk_photos_field_note_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_photos_field_note_id'))
        batch_op.drop_column('field_note_id')

    with op.batch_alter_table('field_notes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_field_notes_plot_year_id'))

    op.drop_table('field_notes')
