"""aenderungsprotokoll

Revision ID: fde71ec23094
Revises: 153eae6fc9f5
Create Date: 2026-10-24 06:08:39.074202

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fde71ec23094'
down_revision = '153eae6fc9f5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('audit_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('table', sa.String(length=50), nullable=False),
    sa.Column('object_id', sa.String(length=50), nullable=True),
    sa.Column('label', sa.String(length=200), nullable=True),
    sa.Column('changes', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_audit_log_user_id', ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_log_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_log_table'), ['table'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_log_user_id'), ['user_id'], unique=False)



def downgrade():
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_log_user_id'))
        batch_op.drop_index(batch_op.f('ix_audit_log_table'))
        batch_op.drop_index(batch_op.f('ix_audit_log_created_at'))

    op.drop_table('audit_log')
