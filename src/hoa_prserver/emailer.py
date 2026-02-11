"""SMTP mail helper.

Used to notify admins when a requested repo does not exist yet.
If SMTP is not configured, sending is skipped.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .settings import Settings


def send_admin_email(settings: Settings, *, subject: str, text: str) -> None:
    if not settings.smtp_host or not settings.admin_email or not settings.smtp_from:
        # Email not configured.
        return

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = settings.admin_email
    msg["Subject"] = subject
    msg.set_content(text)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if settings.smtp_user and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
