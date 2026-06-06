from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.models import Allocation, Asset, BackupAllocation, DisposalRequest, MaintenanceTicket, ScheduledJobRun, TravelRequest, User, VerificationCampaign
from app.services.helpers import create_notification, log_event

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:  # pragma: no cover - optional serverless dependency
    BackgroundScheduler = None


def _admins(db: Session) -> list[User]:
    return db.scalars(select(User).where(User.role.in_(["super_admin", "hardware_admin", "hr_admin"]), User.active == True)).all()


def _notify_admins(db: Session, event_code: str, title: str, message: str, link_url: str = "#") -> int:
    count = 0
    for user in _admins(db):
        create_notification(db, user.id, event_code, title, message, link_url)
        count += 1
    return count


def pending_signature_reminders(db: Session) -> str:
    rows = db.scalars(select(Allocation).where(Allocation.status == "pending_signature")).all()
    for item in rows:
        create_notification(db, item.employee_id, "employee_signature_pending", "Signature pending", f"Please sign allocation #{item.id} for {item.asset.asset_code}.", f"/allocations/{item.id}/sign")
    return f"{len(rows)} pending signature reminders queued"


def hr_approval_pending_reminders(db: Session) -> str:
    count = db.query(Allocation).filter(Allocation.status == "submitted").count()
    recipients = _notify_admins(db, "allocation_submitted_hr", "HR approval pending", f"{count} allocation(s) are waiting for HR approval.", "/allocations") if count else 0
    return f"{count} pending approvals; {recipients} admins notified"


def return_due_reminders(db: Session) -> str:
    count = _notify_admins(db, "return_initiated", "Return verification reminder", "Review initiated asset returns and exit-clearance cases.", "/returns")
    return f"{count} admins notified"


def abroad_return_due_reminders(db: Session) -> str:
    today = date.today()
    rows = db.scalars(select(TravelRequest).where(TravelRequest.status == "approved", TravelRequest.return_date >= today, TravelRequest.return_date <= today + timedelta(days=3))).all()
    for item in rows:
        create_notification(db, item.employee_id, "abroad_return_due", "Abroad asset return due", f"Asset {item.asset.asset_code} return is due on {item.return_date}.", "/travel")
    return f"{len(rows)} abroad return due reminders queued"


def abroad_overdue_escalation(db: Session) -> str:
    rows = db.scalars(select(TravelRequest).where(TravelRequest.status == "approved", TravelRequest.return_date < date.today())).all()
    if rows:
        _notify_admins(db, "abroad_return_overdue", "Abroad returns overdue", f"{len(rows)} abroad asset return(s) are overdue.", "/travel")
    return f"{len(rows)} overdue abroad returns escalated"


def warranty_expiry_reminders(db: Session) -> str:
    today = date.today()
    rows = db.scalars(select(Asset).where(Asset.warranty_until >= today, Asset.warranty_until <= today + timedelta(days=30))).all()
    if rows:
        _notify_admins(db, "warranty_expiring", "Warranty expiring", f"{len(rows)} asset warranty record(s) expire within 30 days.", "/reports/drilldown/warranty_expiring")
    return f"{len(rows)} expiring warranties found"


def verification_campaign_due_reminders(db: Session) -> str:
    today = date.today()
    rows = db.scalars(select(VerificationCampaign).where(VerificationCampaign.status.in_(["planned", "active"]), VerificationCampaign.end_date != None, VerificationCampaign.end_date <= today + timedelta(days=3))).all()
    return f"{len(rows)} campaign due reminders queued; {_notify_admins(db, 'verification_campaign_started', 'Verification campaign due', f'{len(rows)} campaign(s) need attention.', '/verification') if rows else 0} admins notified"


def backup_asset_return_reminders(db: Session) -> str:
    today = date.today()
    rows = db.scalars(select(BackupAllocation).where(BackupAllocation.status == "active", BackupAllocation.expected_return_date != None, BackupAllocation.expected_return_date <= today + timedelta(days=2))).all()
    for item in rows:
        create_notification(db, item.employee_id, "backup_asset_issued", "Backup asset return due", f"Backup asset {item.backup_asset.asset_code} return is due by {item.expected_return_date}.", f"/backup/{item.id}")
    return f"{len(rows)} backup return reminders queued"


def open_ticket_ageing_escalation(db: Session) -> str:
    cutoff = datetime.utcnow() - timedelta(days=7)
    rows = db.scalars(select(MaintenanceTicket).where(MaintenanceTicket.status != "closed", MaintenanceTicket.created_at < cutoff)).all()
    if rows:
        _notify_admins(db, "ticket_assigned", "Open ticket ageing escalation", f"{len(rows)} ticket(s) have been open for more than seven days.", "/maintenance")
    return f"{len(rows)} aged open tickets escalated"


def disposal_request_pending_reminder(db: Session) -> str:
    rows = db.scalars(select(DisposalRequest).where(DisposalRequest.status.in_(["submitted", "committee_review"]))).all()
    if rows:
        _notify_admins(db, "disposal_request_submitted", "Disposal review pending", f"{len(rows)} disposal request(s) need committee/director action.", "/disposal")
    return f"{len(rows)} disposal requests pending"


JOB_DEFINITIONS: dict[str, tuple[str, Callable[[Session], str]]] = {
    "pending_signature_reminders": ("Pending signature reminders", pending_signature_reminders),
    "hr_approval_pending_reminders": ("HR approval pending reminders", hr_approval_pending_reminders),
    "return_due_reminders": ("Return due reminders", return_due_reminders),
    "abroad_return_due_reminders": ("Abroad return due reminders", abroad_return_due_reminders),
    "abroad_overdue_escalation": ("Abroad overdue escalation", abroad_overdue_escalation),
    "warranty_expiry_reminders": ("Warranty expiry reminders", warranty_expiry_reminders),
    "verification_campaign_due_reminders": ("Verification campaign due reminders", verification_campaign_due_reminders),
    "backup_asset_return_reminders": ("Backup asset return reminders", backup_asset_return_reminders),
    "open_ticket_ageing_escalation": ("Open ticket ageing escalation", open_ticket_ageing_escalation),
    "disposal_request_pending_reminder": ("Disposal request pending reminder", disposal_request_pending_reminder),
}


def run_job(job_code: str) -> ScheduledJobRun:
    if job_code not in JOB_DEFINITIONS:
        raise KeyError(job_code)
    db = SessionLocal()
    run = ScheduledJobRun(job_code=job_code, status="running")
    db.add(run)
    db.commit()
    try:
        message = JOB_DEFINITIONS[job_code][1](db)
        run.status = "success"
        run.message = message
        log_event(db, "scheduled_jobs", "run", "system", job_code, new_value=message)
    except Exception as exc:
        run.status = "failed"
        run.message = str(exc)[:2000]
        log_event(db, "scheduled_jobs", "failed", "system", job_code, new_value=run.message)
    finally:
        run.finished_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
        db.close()
    return run


def start_scheduler() -> object | None:
    if BackgroundScheduler is None:
        return None
    scheduler = BackgroundScheduler(timezone="UTC")
    for job_code in JOB_DEFINITIONS:
        scheduler.add_job(run_job, "cron", hour=2, minute=0, args=[job_code], id=job_code, replace_existing=True)
    scheduler.start()
    return scheduler
