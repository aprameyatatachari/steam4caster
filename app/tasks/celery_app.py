"""Celery application and Beat schedule.

API, worker and scheduler all run from this one codebase/image as separate processes.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("steam4caster", broker=settings.broker_url, backend=settings.result_backend)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # At-least-once delivery: acknowledge after the work is done. Every task is idempotent.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=900,
    task_soft_time_limit=840,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    task_default_queue="default",
    imports=("app.tasks.tasks",),
    beat_schedule={
        "refresh-watched-prices": {
            "task": "prices.refresh_watched",
            "schedule": settings.schedule_price_refresh_minutes * 60.0,
        },
        "backfill-pending-history": {
            "task": "prices.backfill_pending",
            "schedule": crontab(minute="20"),
        },
        "daily-forecasts": {
            "task": "forecasts.generate_daily",
            "schedule": crontab(hour="5", minute="10"),
        },
        "evaluate-forecast-outcomes": {
            "task": "forecasts.evaluate_outcomes",
            "schedule": crontab(hour="6", minute="0"),
        },
        "reconcile-metadata": {
            "task": "catalog.reconcile_metadata",
            "schedule": crontab(hour="3", minute="30"),
        },
        "dispatch-notification-outbox": {
            "task": "notifications.dispatch_outbox",
            "schedule": 60.0,
        },
        "send-notification-digests": {
            "task": "notifications.send_digests",
            "schedule": crontab(minute="0"),
        },
        # No-op unless TRAINING_SCHEDULE_ENABLED=true and enough new data exists.
        "train-candidate-model": {
            "task": "models.train_if_due",
            "schedule": crontab(day_of_week="sunday", hour="2", minute="0"),
        },
    },
)
