"""
Celery Configuration — Background task processing.

Handles:
- Overdue invoice checks
- Payment reminders
- Job reminders
- Review request emails
"""

from celery import Celery
from celery.schedules import crontab

from app.config import settings

# Create Celery app
celery_app = Celery(
    "concreteiq",
    broker=settings.redis_url or "redis://localhost:6379/0",
    backend=settings.redis_url or "redis://localhost:6379/0",
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Australia/Sydney",
    enable_utc=False,

    # Beat schedule for periodic tasks
    beat_schedule={
        # Check for overdue invoices every hour
        "check-overdue-invoices": {
            "task": "app.tasks.invoices.check_overdue_invoices",
            "schedule": crontab(minute=0),  # Every hour on the hour
        },
        # Send payment reminders daily at 9am
        "send-payment-reminders": {
            "task": "app.tasks.invoices.send_payment_reminders",
            "schedule": crontab(hour=9, minute=0),
        },
        # Send job reminders daily at 7am
        "send-job-reminders": {
            "task": "app.tasks.jobs.send_job_reminders",
            "schedule": crontab(hour=7, minute=0),
        },
        # Send review requests daily at 2pm
        "send-review-requests": {
            "task": "app.tasks.reviews.send_review_requests",
            "schedule": crontab(hour=14, minute=0),
        },
        # Process due reminders every 30 minutes
        "process-due-reminders": {
            "task": "app.tasks.reminders.process_due_reminders",
            "schedule": crontab(minute="*/30"),  # Every 30 minutes
        },
        # Record weekly earnings snapshot every Monday at 1am
        "record-weekly-earnings": {
            "task": "app.tasks.earnings.record_weekly_snapshot",
            "schedule": crontab(hour=1, minute=0, day_of_week=1),  # Monday 1am
        },
        # Record monthly earnings snapshot on 1st of each month at 2am
        "record-monthly-earnings": {
            "task": "app.tasks.earnings.record_monthly_snapshot",
            "schedule": crontab(hour=2, minute=0, day_of_month=1),  # 1st of month 2am
        },
        # Check for quotes needing follow-up daily at 6:30pm (after work hours)
        "check-quote-followups": {
            "task": "app.tasks.followups.check_quote_followups",
            "schedule": crontab(hour=18, minute=30),  # 6:30pm daily
        },
        # Expire quotes past their expiry date daily at 12:30am
        "expire-old-quotes": {
            "task": "app.tasks.quotes.expire_old_quotes",
            "schedule": crontab(hour=0, minute=30),  # 12:30am daily
        },
    },
)

# Auto-discover tasks from the tasks module
celery_app.autodiscover_tasks(["app.tasks"])
