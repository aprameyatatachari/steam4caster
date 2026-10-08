"""Initial schema.

Portable column types: JSON is JSONB on PostgreSQL, and timestamps are timezone-aware.

Revision ID: 0001
Revises: 
Create Date: 2026-10-08 20:37:50.894012
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('games',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('itad_id', sa.String(length=64), nullable=True),
    sa.Column('steam_app_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('slug', sa.String(length=512), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=True),
    sa.Column('mature', sa.Boolean(), nullable=False),
    sa.Column('early_access', sa.Boolean(), nullable=True),
    sa.Column('release_date', sa.Date(), nullable=True),
    sa.Column('developers', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('publishers', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('tags', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('primary_publisher', sa.String(length=255), nullable=True),
    sa.Column('primary_tag', sa.String(length=128), nullable=True),
    sa.Column('assets', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('provider_url', sa.Text(), nullable=True),
    sa.Column('info_fetched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_games')),
    sa.UniqueConstraint('itad_id', name=op.f('uq_games_itad_id')),
    sa.UniqueConstraint('steam_app_id', name=op.f('uq_games_steam_app_id'))
    )
    op.create_index(op.f('ix_games_primary_publisher'), 'games', ['primary_publisher'], unique=False)
    op.create_index(op.f('ix_games_primary_tag'), 'games', ['primary_tag'], unique=False)
    op.create_index(op.f('ix_games_slug'), 'games', ['slug'], unique=False)
    op.create_index(op.f('ix_games_title'), 'games', ['title'], unique=False)
    op.create_table('model_versions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('version', sa.String(length=64), nullable=False),
    sa.Column('artifact_uri', sa.Text(), nullable=False),
    sa.Column('artifact_checksum', sa.String(length=64), nullable=False),
    sa.Column('training_cutoff', sa.DateTime(timezone=True), nullable=False),
    sa.Column('feature_schema_version', sa.String(length=32), nullable=False),
    sa.Column('hyperparameters', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('metrics', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('CANDIDATE', 'ACTIVE', 'RETIRED')", name=op.f('ck_model_versions_status_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_model_versions')),
    sa.UniqueConstraint('version', name=op.f('uq_model_versions_version'))
    )
    op.create_table('shops',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('provider_shop_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('slug', sa.String(length=128), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shops')),
    sa.UniqueConstraint('provider_shop_id', name=op.f('uq_shops_provider_shop_id')),
    sa.UniqueConstraint('slug', name=op.f('uq_shops_slug'))
    )
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=True),
    sa.Column('external_subject', sa.String(length=255), nullable=True),
    sa.Column('default_country', sa.String(length=2), nullable=False),
    sa.Column('default_currency', sa.String(length=3), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('email_verified', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email')),
    sa.UniqueConstraint('external_subject', name=op.f('uq_users_external_subject'))
    )
    op.create_table('forecasts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('cutoff_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('method', sa.String(length=16), nullable=False),
    sa.Column('currently_on_sale', sa.Boolean(), nullable=False),
    sa.Column('sale_prob_7d', sa.Float(), nullable=False),
    sa.Column('sale_prob_30d', sa.Float(), nullable=False),
    sa.Column('sale_prob_90d', sa.Float(), nullable=False),
    sa.Column('new_sale_prob_7d', sa.Float(), nullable=False),
    sa.Column('new_sale_prob_30d', sa.Float(), nullable=False),
    sa.Column('new_sale_prob_90d', sa.Float(), nullable=False),
    sa.Column('discount_class_probs', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('expected_discount_pct', sa.Float(), nullable=False),
    sa.Column('current_price_minor', sa.Integer(), nullable=True),
    sa.Column('regular_price_minor', sa.Integer(), nullable=True),
    sa.Column('historical_low_minor', sa.Integer(), nullable=True),
    sa.Column('price_lower_minor', sa.Integer(), nullable=True),
    sa.Column('price_median_minor', sa.Integer(), nullable=True),
    sa.Column('price_upper_minor', sa.Integer(), nullable=True),
    sa.Column('window_start', sa.Date(), nullable=True),
    sa.Column('window_end', sa.Date(), nullable=True),
    sa.Column('confidence_score', sa.Float(), nullable=False),
    sa.Column('data_quality', sa.String(length=16), nullable=False),
    sa.Column('explanation_factors', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('model_version', sa.String(length=64), nullable=False),
    sa.Column('model_version_id', sa.Uuid(), nullable=True),
    sa.Column('feature_schema_version', sa.String(length=32), nullable=False),
    sa.Column('feature_snapshot', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('actual_sale_7d', sa.Boolean(), nullable=True),
    sa.Column('actual_sale_30d', sa.Boolean(), nullable=True),
    sa.Column('actual_sale_90d', sa.Boolean(), nullable=True),
    sa.Column('actual_max_discount_pct', sa.Integer(), nullable=True),
    sa.Column('actual_min_price_minor', sa.Integer(), nullable=True),
    sa.Column('actual_min_price_30d_minor', sa.Integer(), nullable=True),
    sa.Column('evaluated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('evaluation_complete', sa.Boolean(), nullable=False),
    sa.CheckConstraint("data_quality IN ('GOOD', 'LIMITED', 'INSUFFICIENT')", name=op.f('ck_forecasts_data_quality_valid')),
    sa.CheckConstraint("method IN ('BASELINE', 'ML')", name=op.f('ck_forecasts_method_valid')),
    sa.CheckConstraint('confidence_score BETWEEN 0 AND 1', name=op.f('ck_forecasts_confidence_range')),
    sa.CheckConstraint('price_lower_minor IS NULL OR (price_lower_minor >= 0 AND price_lower_minor <= price_median_minor AND price_median_minor <= price_upper_minor)', name=op.f('ck_forecasts_price_interval_ordered')),
    sa.CheckConstraint('sale_prob_7d BETWEEN 0 AND 1 AND sale_prob_30d BETWEEN 0 AND 1 AND sale_prob_90d BETWEEN 0 AND 1', name=op.f('ck_forecasts_probability_range')),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_forecasts_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['model_version_id'], ['model_versions.id'], name=op.f('fk_forecasts_model_version_id_model_versions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], name=op.f('fk_forecasts_shop_id_shops')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_forecasts'))
    )
    op.create_index('ix_forecasts_pending_evaluation', 'forecasts', ['evaluation_complete', 'cutoff_at'], unique=False)
    op.create_index('ix_forecasts_series_created', 'forecasts', ['game_id', 'shop_id', 'country', 'created_at'], unique=False)
    op.create_table('ingestion_watermarks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('history_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('covered_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('backfill_completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error_code', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_ingestion_watermarks_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], name=op.f('fk_ingestion_watermarks_shop_id_shops')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ingestion_watermarks')),
    sa.UniqueConstraint('game_id', 'shop_id', 'country', name='uq_ingestion_watermarks_identity')
    )
    op.create_table('notification_preferences',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('quiet_hours_start', sa.Time(), nullable=True),
    sa.Column('quiet_hours_end', sa.Time(), nullable=True),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('delivery_mode', sa.String(length=16), nullable=False),
    sa.Column('destination', sa.String(length=320), nullable=True),
    sa.Column('destination_verified', sa.Boolean(), nullable=False),
    sa.Column('opted_in_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("channel IN ('WEB_PUSH', 'EMAIL', 'SMS')", name=op.f('ck_notification_preferences_channel_valid')),
    sa.CheckConstraint("delivery_mode IN ('IMMEDIATE', 'DIGEST')", name=op.f('ck_notification_preferences_mode_valid')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notification_preferences_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_preferences')),
    sa.UniqueConstraint('user_id', 'channel', name='uq_notification_preferences_user_channel')
    )
    op.create_table('price_observations',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('price_minor', sa.Integer(), nullable=False),
    sa.Column('regular_minor', sa.Integer(), nullable=False),
    sa.Column('discount_pct', sa.Integer(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ingested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.CheckConstraint('discount_pct BETWEEN 0 AND 100', name=op.f('ck_regional_game_prices_discount_range')),
    sa.CheckConstraint('price_minor >= 0', name=op.f('ck_regional_game_prices_price_non_negative')),
    sa.CheckConstraint('regular_minor >= 0', name=op.f('ck_regional_game_prices_regular_non_negative')),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_price_observations_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], name=op.f('fk_price_observations_shop_id_shops')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_price_observations')),
    sa.UniqueConstraint('game_id', 'shop_id', 'country', 'observed_at', 'price_minor', 'regular_minor', name='uq_price_observations_identity')
    )
    op.create_index('ix_price_observations_series_time', 'price_observations', ['game_id', 'shop_id', 'country', 'observed_at'], unique=False)
    op.create_table('push_subscriptions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('endpoint_encrypted', sa.Text(), nullable=False),
    sa.Column('endpoint_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('endpoint_host', sa.String(length=255), nullable=False),
    sa.Column('p256dh_encrypted', sa.Text(), nullable=False),
    sa.Column('auth_encrypted', sa.Text(), nullable=False),
    sa.Column('device_label', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_push_subscriptions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_push_subscriptions')),
    sa.UniqueConstraint('endpoint_fingerprint', name=op.f('uq_push_subscriptions_endpoint_fingerprint'))
    )
    op.create_index('ix_push_subscriptions_user', 'push_subscriptions', ['user_id', 'revoked_at'], unique=False)
    op.create_table('refresh_tokens',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('family_id', sa.Uuid(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_refresh_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refresh_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_refresh_tokens_token_hash'))
    )
    op.create_index('ix_refresh_tokens_user_family', 'refresh_tokens', ['user_id', 'family_id'], unique=False)
    op.create_table('regional_game_prices',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('price_minor', sa.Integer(), nullable=False),
    sa.Column('regular_minor', sa.Integer(), nullable=False),
    sa.Column('discount_pct', sa.Integer(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('historical_low_minor', sa.Integer(), nullable=True),
    sa.Column('historical_low_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('discount_pct BETWEEN 0 AND 100', name=op.f('ck_regional_game_prices_discount_range')),
    sa.CheckConstraint('price_minor >= 0', name=op.f('ck_regional_game_prices_price_non_negative')),
    sa.CheckConstraint('regular_minor >= 0', name=op.f('ck_regional_game_prices_regular_non_negative')),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_regional_game_prices_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], name=op.f('fk_regional_game_prices_shop_id_shops')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_regional_game_prices')),
    sa.UniqueConstraint('game_id', 'shop_id', 'country', name='uq_regional_game_prices_identity')
    )
    op.create_table('sale_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('initial_price_minor', sa.Integer(), nullable=False),
    sa.Column('min_price_minor', sa.Integer(), nullable=False),
    sa.Column('regular_minor', sa.Integer(), nullable=False),
    sa.Column('max_discount_pct', sa.Integer(), nullable=False),
    sa.Column('derivation_version', sa.String(length=16), nullable=False),
    sa.CheckConstraint('ended_at IS NULL OR ended_at >= started_at', name=op.f('ck_sale_events_end_after_start')),
    sa.CheckConstraint('max_discount_pct BETWEEN 1 AND 100', name=op.f('ck_sale_events_discount_range')),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_sale_events_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], name=op.f('fk_sale_events_shop_id_shops')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sale_events')),
    sa.UniqueConstraint('game_id', 'shop_id', 'country', 'started_at', name='uq_sale_events_identity')
    )
    op.create_index('ix_sale_events_cohort', 'sale_events', ['shop_id', 'country', 'started_at'], unique=False)
    op.create_table('watchlist_entries',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('target_price_minor', sa.Integer(), nullable=True),
    sa.Column('min_discount_pct', sa.Integer(), nullable=True),
    sa.Column('max_wait_days', sa.Integer(), nullable=True),
    sa.Column('historical_low_only', sa.Boolean(), nullable=False),
    sa.Column('notify_on_buy', sa.Boolean(), nullable=False),
    sa.Column('channels', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('last_recommendation_action', sa.String(length=16), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('max_wait_days IS NULL OR max_wait_days BETWEEN 1 AND 365', name=op.f('ck_watchlist_entries_wait_range')),
    sa.CheckConstraint('min_discount_pct IS NULL OR min_discount_pct BETWEEN 1 AND 100', name=op.f('ck_watchlist_entries_discount_range')),
    sa.CheckConstraint('target_price_minor IS NULL OR target_price_minor >= 0', name=op.f('ck_watchlist_entries_target_non_negative')),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_watchlist_entries_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_watchlist_entries_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_watchlist_entries'))
    )
    op.create_index('ix_watchlist_entries_series', 'watchlist_entries', ['game_id', 'country', 'is_active'], unique=False)
    op.create_index('uq_watchlist_entries_user_game', 'watchlist_entries', ['user_id', 'game_id'], unique=True)
    op.create_table('notification_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('game_id', sa.Uuid(), nullable=True),
    sa.Column('watchlist_entry_id', sa.Uuid(), nullable=True),
    sa.Column('event_type', sa.String(length=32), nullable=False),
    sa.Column('trigger_identity', sa.String(length=255), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_notification_events_game_id_games'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notification_events_user_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['watchlist_entry_id'], ['watchlist_entries.id'], name=op.f('fk_notification_events_watchlist_entry_id_watchlist_entries'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_events'))
    )
    op.create_index('ix_notification_events_entry_type', 'notification_events', ['watchlist_entry_id', 'event_type', 'created_at'], unique=False)
    op.create_index('ix_notification_events_user_created', 'notification_events', ['user_id', 'created_at'], unique=False)
    op.create_table('recommendations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('forecast_id', sa.Uuid(), nullable=False),
    sa.Column('watchlist_entry_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(length=16), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('expected_savings_minor', sa.Integer(), nullable=True),
    sa.Column('expected_future_price_minor', sa.Integer(), nullable=True),
    sa.Column('waiting_cost_minor', sa.Integer(), nullable=True),
    sa.Column('max_wait_days', sa.Integer(), nullable=False),
    sa.Column('selected_horizon_days', sa.Integer(), nullable=False),
    sa.Column('sale_probability', sa.Float(), nullable=False),
    sa.Column('reason_codes', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('thresholds', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('ruleset_version', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("action IN ('BUY', 'WAIT', 'NEUTRAL')", name=op.f('ck_recommendations_action_valid')),
    sa.ForeignKeyConstraint(['forecast_id'], ['forecasts.id'], name=op.f('fk_recommendations_forecast_id_forecasts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['watchlist_entry_id'], ['watchlist_entries.id'], name=op.f('fk_recommendations_watchlist_entry_id_watchlist_entries'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recommendations'))
    )
    op.create_index('ix_recommendations_entry_created', 'recommendations', ['watchlist_entry_id', 'created_at'], unique=False)
    op.create_index('ix_recommendations_forecast', 'recommendations', ['forecast_id', 'max_wait_days'], unique=False)
    op.create_table('notification_outbox',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('event_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('is_digest', sa.Boolean(), nullable=False),
    sa.Column('attempt_count', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('provider_message_id', sa.String(length=255), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("channel IN ('WEB_PUSH', 'EMAIL', 'SMS')", name=op.f('ck_notification_outbox_channel_valid')),
    sa.CheckConstraint("state IN ('PENDING', 'SENDING', 'SENT', 'FAILED', 'SUPPRESSED')", name=op.f('ck_notification_outbox_state_valid')),
    sa.ForeignKeyConstraint(['event_id'], ['notification_events.id'], name=op.f('fk_notification_outbox_event_id_notification_events'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notification_outbox_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_outbox')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_notification_outbox_idempotency_key'))
    )
    op.create_index('ix_notification_outbox_pending', 'notification_outbox', ['state', 'next_attempt_at'], unique=False)
    op.create_index('ix_notification_outbox_user_created', 'notification_outbox', ['user_id', 'created_at'], unique=False)
    op.create_table('notification_deliveries',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('outbox_id', sa.Uuid(), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('attempt_number', sa.Integer(), nullable=False),
    sa.Column('success', sa.Boolean(), nullable=False),
    sa.Column('transient', sa.Boolean(), nullable=False),
    sa.Column('provider_message_id', sa.String(length=255), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('push_subscription_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['outbox_id'], ['notification_outbox.id'], name=op.f('fk_notification_deliveries_outbox_id_notification_outbox'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['push_subscription_id'], ['push_subscriptions.id'], name=op.f('fk_notification_deliveries_push_subscription_id_push_subscriptions'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_deliveries'))
    )
    op.create_index('ix_notification_deliveries_outbox', 'notification_deliveries', ['outbox_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_notification_deliveries_outbox', table_name='notification_deliveries')
    op.drop_table('notification_deliveries')
    op.drop_index('ix_notification_outbox_user_created', table_name='notification_outbox')
    op.drop_index('ix_notification_outbox_pending', table_name='notification_outbox')
    op.drop_table('notification_outbox')
    op.drop_index('ix_recommendations_forecast', table_name='recommendations')
    op.drop_index('ix_recommendations_entry_created', table_name='recommendations')
    op.drop_table('recommendations')
    op.drop_index('ix_notification_events_user_created', table_name='notification_events')
    op.drop_index('ix_notification_events_entry_type', table_name='notification_events')
    op.drop_table('notification_events')
    op.drop_index('uq_watchlist_entries_user_game', table_name='watchlist_entries')
    op.drop_index('ix_watchlist_entries_series', table_name='watchlist_entries')
    op.drop_table('watchlist_entries')
    op.drop_index('ix_sale_events_cohort', table_name='sale_events')
    op.drop_table('sale_events')
    op.drop_table('regional_game_prices')
    op.drop_index('ix_refresh_tokens_user_family', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index('ix_push_subscriptions_user', table_name='push_subscriptions')
    op.drop_table('push_subscriptions')
    op.drop_index('ix_price_observations_series_time', table_name='price_observations')
    op.drop_table('price_observations')
    op.drop_table('notification_preferences')
    op.drop_table('ingestion_watermarks')
    op.drop_index('ix_forecasts_series_created', table_name='forecasts')
    op.drop_index('ix_forecasts_pending_evaluation', table_name='forecasts')
    op.drop_table('forecasts')
    op.drop_table('users')
    op.drop_table('shops')
    op.drop_table('model_versions')
    op.drop_index(op.f('ix_games_title'), table_name='games')
    op.drop_index(op.f('ix_games_slug'), table_name='games')
    op.drop_index(op.f('ix_games_primary_tag'), table_name='games')
    op.drop_index(op.f('ix_games_primary_publisher'), table_name='games')
    op.drop_table('games')
