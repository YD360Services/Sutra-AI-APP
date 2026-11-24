"""LLD v1.3 core lifecycle, evidence, provenance, and model registry entities

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-25 01:06:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Extend ai_token_usage with generation_id reference
    with op.batch_alter_table('ai_token_usage') as batch_op:
        batch_op.add_column(sa.Column('generation_id', sa.String(255), nullable=True))
        batch_op.create_index('ix_ai_token_usage_generation_id', ['generation_id'])

    # 2. transcript_segments
    op.create_table(
        'transcript_segments',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('session_id', sa.String(255), nullable=False, index=True),
        sa.Column('tenant_id', sa.String(255), nullable=True, index=True),
        sa.Column('sequence', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('speaker', sa.String(100), nullable=False, server_default='interviewer'),
        sa.Column('raw_text', sa.Text(), nullable=False),
        sa.Column('corrected_text', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('is_final', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('speech_final', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('processor_version', sa.String(50), nullable=False, server_default='v1.3'),
        sa.Column('created_at', sa.DateTime(), nullable=False, index=True),
    )

    # 3. conversation_turns
    op.create_table(
        'conversation_turns',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('session_id', sa.String(255), nullable=False, index=True),
        sa.Column('tenant_id', sa.String(255), nullable=True, index=True),
        sa.Column('turn_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('question_id', sa.String(255), nullable=False, index=True),
        sa.Column('final_question', sa.Text(), nullable=False),
        sa.Column('final_answer', sa.Text(), nullable=True),
        sa.Column('generation_id', sa.String(255), nullable=True, index=True),
        sa.Column('topic', sa.String(255), nullable=True),
        sa.Column('intent', sa.String(100), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, index=True),
    )

    # 4. question_events
    op.create_table(
        'question_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('session_id', sa.String(255), nullable=False, index=True),
        sa.Column('question_id', sa.String(255), nullable=False, index=True),
        sa.Column('question_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('status', sa.String(50), nullable=False, server_default='predicted'),
        sa.Column('question_text', sa.Text(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, index=True),
    )

    # 5. question_predictions
    op.create_table(
        'question_predictions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('prediction_id', sa.String(255), nullable=False, index=True),
        sa.Column('session_id', sa.String(255), nullable=False, index=True),
        sa.Column('question_id', sa.String(255), nullable=False, index=True),
        sa.Column('predicted_question', sa.Text(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('fingerprint', sa.String(128), nullable=True),
        sa.Column('was_promoted', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )

    # 6. answer_generations
    op.create_table(
        'answer_generations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('generation_id', sa.String(255), nullable=False, unique=True, index=True),
        sa.Column('session_id', sa.String(255), nullable=False, index=True),
        sa.Column('question_id', sa.String(255), nullable=False, index=True),
        sa.Column('question_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('generation_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('is_provisional', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('status', sa.String(50), nullable=False, server_default='started'),
        sa.Column('provider', sa.String(100), nullable=True),
        sa.Column('model_id', sa.String(100), nullable=True),
        sa.Column('context_fingerprint', sa.String(128), nullable=True),
        sa.Column('superseded_by_id', sa.String(255), nullable=True),
        sa.Column('answer_text', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, index=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
    )

    # 7. candidate_profiles
    op.create_table(
        'candidate_profiles',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(255), nullable=False, index=True),
        sa.Column('tenant_id', sa.String(255), nullable=True, index=True),
        sa.Column('profile_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('skills_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('verified_facts_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('verification_status', sa.String(50), nullable=False, server_default='imported'),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )

    # 8. candidate_projects
    op.create_table(
        'candidate_projects',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('profile_id', sa.String(36), sa.ForeignKey('candidate_profiles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(255), nullable=False, index=True),
        sa.Column('project_name', sa.String(255), nullable=False),
        sa.Column('ownership_role', sa.String(255), nullable=False),
        sa.Column('evidence_source', sa.String(100), nullable=False, server_default='resume'),
        sa.Column('tech_stack_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('verified', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )

    # 9. source_provenance
    op.create_table(
        'source_provenance',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(255), nullable=False, index=True),
        sa.Column('user_id', sa.String(255), nullable=True, index=True),
        sa.Column('document_id', sa.String(255), nullable=False, index=True),
        sa.Column('access_scope', sa.String(50), nullable=False, server_default='private'),
        sa.Column('document_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('checksum', sa.String(128), nullable=True),
        sa.Column('storage_key', sa.String(512), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )

    # 10. llm_model_registry
    op.create_table(
        'llm_model_registry',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('provider', sa.String(100), nullable=False, index=True),
        sa.Column('model_id', sa.String(100), nullable=False, unique=True),
        sa.Column('supports_streaming', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('context_window', sa.Integer(), nullable=False, server_default='128000'),
        sa.Column('input_cost_per_1m', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('output_cost_per_1m', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('avg_ttft_ms', sa.Integer(), nullable=False, server_default='500'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('llm_model_registry')
    op.drop_table('source_provenance')
    op.drop_table('candidate_projects')
    op.drop_table('candidate_profiles')
    op.drop_table('answer_generations')
    op.drop_table('question_predictions')
    op.drop_table('question_events')
    op.drop_table('conversation_turns')
    op.drop_table('transcript_segments')
    with op.batch_alter_table('ai_token_usage') as batch_op:
        batch_op.drop_index('ix_ai_token_usage_generation_id')
        batch_op.drop_column('generation_id')
