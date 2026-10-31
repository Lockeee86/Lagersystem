"""bestellung: Rechnung übergeben, Dateien an der Bestellung

Revision ID: c3e8a1f6d927
Revises: b7d2f4a8c615
Create Date: 2026-10-31 18:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3e8a1f6d927'
down_revision = 'b7d2f4a8c615'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('purchase_requests') as batch_op:
        batch_op.add_column(sa.Column('invoice_handed_on', sa.Date(), nullable=True))
    op.create_table(
        'purchase_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('filename', sa.String(length=80), nullable=False),
        sa.Column('original_name', sa.String(length=200), nullable=True),
        sa.Column('mimetype', sa.String(length=50), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['request_id'], ['purchase_requests.id'],
                                name='fk_purchase_documents_request_id_purchase_requests', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_purchase_documents_user_id_users'),
        sa.PrimaryKeyConstraint('id', name='pk_purchase_documents'),
        sa.UniqueConstraint('filename', name='uq_purchase_documents_filename'),
    )
    op.create_index('ix_purchase_documents_request_id', 'purchase_documents', ['request_id'])


def downgrade():
    op.drop_index('ix_purchase_documents_request_id', table_name='purchase_documents')
    op.drop_table('purchase_documents')
    with op.batch_alter_table('purchase_requests') as batch_op:
        batch_op.drop_column('invoice_handed_on')
