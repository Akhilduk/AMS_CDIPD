from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import EMAIL_NOTIFICATIONS_ENABLED, SMTP_FROM_EMAIL, SMTP_FROM_NAME, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USERNAME, SMTP_USE_TLS
from app.models.models import NotificationDeliveryLog, NotificationTemplate, User

DEFAULT_EMAIL_EVENTS = {
    "allocation_submitted_hr": "Allocation submitted to HR",
    "allocation_approved": "Allocation approved",
    "allocation_rejected": "Allocation rejected",
    "employee_signature_pending": "Employee signature pending",
    "signature_completed": "Signature completed",
    "ticket_raised": "Ticket raised",
    "ticket_assigned": "Ticket assigned",
    "ticket_closed": "Ticket closed",
    "replacement_initiated": "Replacement initiated",
    "backup_asset_issued": "Backup asset issued",
    "return_initiated": "Return initiated",
    "return_verified": "Return verified",
    "exit_clearance_completed": "Exit clearance completed",
    "liability_created": "Liability created",
    "abroad_request_submitted": "Abroad request submitted",
    "abroad_decision": "Abroad approved/rejected",
    "abroad_return_due": "Abroad return due",
    "abroad_return_overdue": "Abroad return overdue",
    "verification_campaign_started": "Verification campaign started",
    "disposal_request_submitted": "Disposal request submitted",
    "disposal_decision": "Disposal approved/rejected",
    "procurement_plan_generated": "Procurement plan generated",
    "warranty_expiring": "Warranty expiring",
}


def smtp_configured() -> bool:
    return bool(EMAIL_NOTIFICATIONS_ENABLED and SMTP_HOST and SMTP_FROM_EMAIL)


def ensure_email_templates(db: Session) -> None:
    existing = {item.event_code for item in db.scalars(select(NotificationTemplate)).all()}
    for event_code, subject in DEFAULT_EMAIL_EVENTS.items():
        if event_code not in existing:
            db.add(NotificationTemplate(event_code=event_code, subject=subject, html_body="<p>{{ message }}</p><p><a href='{{ link_url }}'>Open in CDIPD AMS</a></p>"))


def _render_template(body: str, context: dict[str, str]) -> str:
    rendered = body
    for key, value in context.items():
        rendered = rendered.replace("{{ " + key + " }}", value).replace("{{" + key + "}}", value)
    return rendered


def send_notification_email(db: Session, user: User, event_code: str, title: str, message: str, link_url: str = "#", notification_id: int | None = None) -> bool:
    if not smtp_configured() or not user.email:
        db.add(NotificationDeliveryLog(notification_id=notification_id, event_code=event_code, recipient=user.email if user else "", channel="email", status="fallback_in_app", error_message="SMTP not configured"))
        return False
    template = db.scalar(select(NotificationTemplate).where(NotificationTemplate.event_code == event_code, NotificationTemplate.active == True, NotificationTemplate.is_deleted == False))
    subject = template.subject if template else title
    html_body = _render_template(template.html_body if template else "<p>{{ message }}</p>", {"message": message, "link_url": link_url, "title": title})
    mime = MIMEMultipart("alternative")
    mime["Subject"] = subject
    mime["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    mime["To"] = user.email
    mime.attach(MIMEText(message, "plain"))
    mime.attach(MIMEText(html_body, "html"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            if SMTP_USE_TLS:
                smtp.starttls()
            if SMTP_USERNAME:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.sendmail(SMTP_FROM_EMAIL, [user.email], mime.as_string())
        db.add(NotificationDeliveryLog(notification_id=notification_id, event_code=event_code, recipient=user.email, channel="email", status="sent"))
        return True
    except Exception as exc:
        db.add(NotificationDeliveryLog(notification_id=notification_id, event_code=event_code, recipient=user.email, channel="email", status="failed_fallback_in_app", error_message=str(exc)[:1000]))
        return False
