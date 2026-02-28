"""notifications module."""

from app.notifications.service import (
    # Core functions
    create_notification,
    get_all_notifications,
    get_unread_notifications,
    get_unread_count,
    mark_as_read,
    mark_all_as_read,
    delete_notification,
    # Quote notifications
    notify_quote_viewed,
    notify_quote_accepted,
    notify_quote_declined,
    notify_quote_expired,
    notify_quote_sent,
    # Invoice notifications
    notify_invoice_sent,
    notify_invoice_sent_admin,
    notify_invoice_viewed,
    notify_invoice_overdue,
    # Payment notifications
    notify_payment_received,
    # Email tracking notifications
    notify_email_opened,
    notify_email_clicked,
    notify_email_bounced,
    # Job notifications
    notify_job_tomorrow,
    notify_job_scheduled,
    # Customer notifications
    notify_new_customer,
    # Batch processing
    check_overdue_invoices,
    check_jobs_tomorrow,
    check_expiring_quotes,
    check_quote_followups,
)

__all__ = [
    # Core functions
    "create_notification",
    "get_all_notifications",
    "get_unread_notifications",
    "get_unread_count",
    "mark_as_read",
    "mark_all_as_read",
    "delete_notification",
    # Quote notifications
    "notify_quote_viewed",
    "notify_quote_accepted",
    "notify_quote_declined",
    "notify_quote_expired",
    "notify_quote_sent",
    # Invoice notifications
    "notify_invoice_sent",
    "notify_invoice_sent_admin",
    "notify_invoice_viewed",
    "notify_invoice_overdue",
    # Payment notifications
    "notify_payment_received",
    # Email tracking notifications
    "notify_email_opened",
    "notify_email_clicked",
    "notify_email_bounced",
    # Job notifications
    "notify_job_tomorrow",
    "notify_job_scheduled",
    # Customer notifications
    "notify_new_customer",
    # Batch processing
    "check_overdue_invoices",
    "check_jobs_tomorrow",
    "check_expiring_quotes",
    "check_quote_followups",
]
