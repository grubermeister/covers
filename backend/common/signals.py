###################################################################################################
## WoCo Commons - Signals
## User activation: send email when admin sets user Active (True)
## Marking date-range cache: keep Marking.earliest_seen/latest_seen fresh (issue #59)
###################################################################################################
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from common.date_range import (
    markings_affected_by_date_seen,
    recompute_suppressed,
    refresh_marking_date_ranges,
)
from common.models import CoverMarking, DateSeen


User = get_user_model()
logger = logging.getLogger(__name__)


def _mail_backend_configured():
    """True if real SMTP (or similar) is likely usable; False for console / missing host."""
    backend = getattr(settings, "EMAIL_BACKEND", "")
    if "console" in backend or "dummy" in backend or "locmem" in backend:
        return False
    if not getattr(settings, "EMAIL_HOST", None):
        return False
    return True


@receiver(pre_save, sender=User)
def send_activation_email_when_user_activated(sender, instance, **kwargs):
    """
    When a user is changed from inactive to active in Django admin (or created as active),
    send them an email so they know they can sign in.
    """
    # Superusers (e.g. createsuperuser) should not trigger consumer activation mail or SMTP.
    if getattr(instance, "is_superuser", False):
        return

    # New user being created with Active checked and email set
    if not instance.pk:
        if instance.is_active and (instance.email or "").strip():
            _send_activation_email(instance.email.strip())
        return

    # Existing user: only send when transitioning inactive -> active
    try:
        previous = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return

    if not previous.is_active and instance.is_active and (instance.email or "").strip():
        _send_activation_email(instance.email.strip())


def _send_activation_email(to_email):
    """Send 'Your account is now active' email to the given address."""
    frontend_base = getattr(settings, "FRONTEND_BASE_URL", None) or f"https://{settings.DJANGO_APP_HOSTNAME}"
    if not frontend_base.startswith(("http://", "https://")):
        frontend_base = f"https://{frontend_base.lstrip('/')}"
    login_url = f"{frontend_base.rstrip('/')}/auth"

    subject = "Your WorldCovers Account Is Now Active"
    message_lines = [
        "Hello,",
        "",
        "Good news — your WorldCovers account has just been activated.",
        "",
        f"You can now sign in here: {login_url}",
        "",
        "If you did not expect this change, please contact the site administrator.",
    ]
    message = "\n".join(message_lines)

    # HTML version with a clickable login link/button
    html_message = f"""
            <p>Hello,</p>

            <p>Good news! Your <strong>WorldCovers</strong> account has been successfully activated.</p>

            <p>You can now sign in using the link below:</p>

            <p>
            <a href="{login_url}" 
                style="display:inline-block;padding:10px 16px;margin-top:8px;background-color:#7b4b4b;color:#ffffff;text-decoration:none;border-radius:4px;">
                Sign in to WorldCovers
            </a>
            </p>

            <p>If you did not expect this change or believe this was done in error, please contact the site administrator immediately.</p>

            <p>Best regards,<br>
            WorldCovers Team</p>
            """

    if not _mail_backend_configured():
        logger.info(
            "Skipping activation email to %s: email backend not configured (set EMAIL_HOST or use a real backend).",
            to_email,
        )
        return

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "no-reply@worldcovers.org"
    try:
        send_mail(
            subject,
            message,
            from_email,
            [to_email],
            fail_silently=False,
            html_message=html_message,
        )
    except Exception:
        # Best-effort: do not block user save if SMTP is misconfigured or unreachable.
        logger.exception("Failed to send activation email to %s", to_email)


###################################################################################################
## Marking date-range cache maintenance (issue #59)
##
## Marking.earliest_seen / latest_seen (+granularities) are a derived cache
## over DateSeen + CoverMarking. These receivers cover every ORM write path —
## API viewsets, admin (incl. inlines and bulk delete), contribution apply,
## import-export per-row saves, and cascades (a post_delete receiver disables
## fast-delete, so queryset.delete() fires per instance). Bulk paths that skip
## the ORM (bulk_create) wrap themselves in suppress_date_range_recompute()
## and run an explicit recompute afterward.
###################################################################################################


@receiver(pre_save, sender=DateSeen, dispatch_uid="date_range_dateseen_pre_save")
def stash_previous_date_seen_subject(sender, instance, **kwargs):
    """Remember the OLD subject so moving a date to another marking/cover
    also refreshes the marking it was moved away from."""
    if recompute_suppressed() or instance.pk is None:
        return
    prev = sender.objects.filter(pk=instance.pk).values_list(
        "subject_type", "subject_id"
    ).first()
    instance._previous_subject = prev


@receiver(post_save, sender=DateSeen, dispatch_uid="date_range_dateseen_post_save")
def refresh_date_range_on_date_seen_save(sender, instance, **kwargs):
    if recompute_suppressed():
        return
    affected = markings_affected_by_date_seen(instance.subject_type, instance.subject_id)
    prev = getattr(instance, "_previous_subject", None)
    if prev is not None:
        affected |= markings_affected_by_date_seen(*prev)
    refresh_marking_date_ranges(affected)


@receiver(post_delete, sender=DateSeen, dispatch_uid="date_range_dateseen_post_delete")
def refresh_date_range_on_date_seen_delete(sender, instance, **kwargs):
    if recompute_suppressed():
        return
    refresh_marking_date_ranges(
        markings_affected_by_date_seen(instance.subject_type, instance.subject_id)
    )


@receiver(pre_save, sender=CoverMarking, dispatch_uid="date_range_covermarking_pre_save")
def stash_previous_cover_marking_link(sender, instance, **kwargs):
    if recompute_suppressed() or instance.pk is None:
        return
    prev = sender.objects.filter(pk=instance.pk).values_list("marking_id", flat=True).first()
    instance._previous_marking_id = prev


@receiver(post_save, sender=CoverMarking, dispatch_uid="date_range_covermarking_post_save")
def refresh_date_range_on_cover_marking_save(sender, instance, **kwargs):
    if recompute_suppressed():
        return
    affected = {instance.marking_id}
    prev = getattr(instance, "_previous_marking_id", None)
    if prev is not None:
        affected.add(prev)
    refresh_marking_date_ranges(affected)


@receiver(post_delete, sender=CoverMarking, dispatch_uid="date_range_covermarking_post_delete")
def refresh_date_range_on_cover_marking_delete(sender, instance, **kwargs):
    if recompute_suppressed():
        return
    # Fires per instance even for queryset/cascade deletes; a marking being
    # deleted in the same cascade ends up as a 0-row UPDATE (harmless).
    refresh_marking_date_ranges({instance.marking_id})

###################################################################################################
