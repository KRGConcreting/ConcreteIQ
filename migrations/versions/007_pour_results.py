"""Add pour_results table for prediction logging.

Revision ID: 007_pour_results
Revises: 006_sequence_starting_numbers
Create Date: 2026-02-01

This migration adds the pour_results table which stores predictions vs actuals
for pour planner calibration. Each result is linked to a pour_plan and captures
both what was predicted and what actually happened during the pour.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = '007_pour_results'
down_revision = '006_sequence_starting_numbers'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'pour_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('pour_plan_id', sa.Integer(), nullable=False),

        # Predictions (captured at planning time)
        sa.Column('predicted_initial_set_hours', sa.Float(), nullable=True),
        sa.Column('predicted_finish_window_start', sa.String(20), nullable=True),
        sa.Column('predicted_finish_window_end', sa.String(20), nullable=True),
        sa.Column('recommended_admixture', sa.String(100), nullable=True),
        sa.Column('recommended_dose_ml', sa.Integer(), nullable=True),

        # Actuals (logged post-pour)
        sa.Column('actual_admixture_used', sa.String(100), nullable=True),
        sa.Column('actual_dose_ml', sa.Integer(), nullable=True),
        sa.Column('actual_initial_set_hours', sa.Float(), nullable=True),
        sa.Column('actual_finish_time', sa.String(20), nullable=True),
        sa.Column('actual_conditions_notes', sa.Text(), nullable=True),

        # Assessment
        sa.Column('prediction_accuracy', sa.String(20), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),

        # Constraints
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['pour_plan_id'], ['pour_plans.id'], ),
    )

    op.create_index('idx_pour_result_plan', 'pour_results', ['pour_plan_id'])


def downgrade() -> None:
    op.drop_index('idx_pour_result_plan', table_name='pour_results')
    op.drop_table('pour_results')
