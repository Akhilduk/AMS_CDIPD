from __future__ import annotations

from collections import defaultdict
from difflib import HtmlDiff
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select, func
from sqlalchemy.orm import Session

from app.models.models import (
    Allocation,
    Asset,
    Category,
    ComplianceDeviation,
    DisposalRequest,
    Location,
    MaintenanceTicket,
    NotificationDeliveryLog,
    NotificationTemplate,
    PolicyMaster,
    PolicyTemplate,
    PolicyVersion,
    ProcurementPlan,
    ReturnRequest,
    Role,
    ScheduledJobRun,
    SignedDocument,
    User,
    Vendor,
    WorkflowSetting,
)
from app.services.email import ensure_email_templates
from app.services.helpers import get_current_user, get_db, log_event, redirect_with_flash, render, require_permission, require_roles
from app.services.scheduler import JOB_DEFINITIONS, run_job

router = APIRouter()


def _like(value: str):
    return f"%{value.strip()}%"


@router.get("/search", response_class=HTMLResponse)
@require_permission("assets", "view")
async def global_search(request: Request, q: str = "", db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    query = q.strip()
    grouped: dict[str, list[dict]] = defaultdict(list)
    if query:
        pattern = _like(query)
        for asset in db.scalars(select(Asset).where(or_(Asset.asset_code.ilike(pattern), Asset.serial_number.ilike(pattern), Asset.qr_path.ilike(pattern))).limit(20)).all():
            grouped["Assets"].append({"icon": "🗃️", "title": asset.asset_code, "subtitle": f"Serial: {asset.serial_number}", "status": asset.status, "url": f"/assets/{asset.id}/history"})
        for user in db.scalars(select(User).where(or_(User.full_name.ilike(pattern), User.employee_code.ilike(pattern), User.email.ilike(pattern))).limit(20)).all():
            grouped["Employees"].append({"icon": "👤", "title": user.full_name, "subtitle": user.employee_code, "status": "active" if user.active else "inactive", "url": "/users"})
        for allocation in db.scalars(select(Allocation).where(func.cast(Allocation.id, __import__('sqlalchemy').String).ilike(pattern)).limit(20)).all():
            grouped["Allocations"].append({"icon": "🧾", "title": f"Allocation #{allocation.id}", "subtitle": allocation.asset.asset_code, "status": allocation.status, "url": "/allocations"})
        for ticket in db.scalars(select(MaintenanceTicket).where(or_(func.cast(MaintenanceTicket.id, __import__('sqlalchemy').String).ilike(pattern), MaintenanceTicket.description.ilike(pattern))).limit(20)).all():
            grouped["Tickets"].append({"icon": "🛠️", "title": f"Ticket #{ticket.id}", "subtitle": ticket.asset.asset_code, "status": ticket.status, "url": "/maintenance"})
        for item in db.scalars(select(ReturnRequest).where(func.cast(ReturnRequest.id, __import__('sqlalchemy').String).ilike(pattern)).limit(20)).all():
            grouped["Returns"].append({"icon": "🔄", "title": f"Return #{item.id}", "subtitle": item.asset.asset_code, "status": item.status, "url": f"/returns/{item.id}"})
        for policy in db.scalars(select(PolicyVersion).where(or_(PolicyVersion.title.ilike(pattern), PolicyVersion.version.ilike(pattern))).limit(20)).all():
            grouped["Policies"].append({"icon": "📜", "title": policy.title, "subtitle": f"Version {policy.version}", "status": policy.status, "url": f"/policies/{policy.id}/compare"})
        for document in db.scalars(select(SignedDocument).where(SignedDocument.document_number.ilike(pattern)).limit(20)).all():
            grouped["Documents"].append({"icon": "📝", "title": document.document_number, "subtitle": document.template_name, "status": document.source_module, "url": f"/documents/{document.id}"})
        for disposal in db.scalars(select(DisposalRequest).where(func.cast(DisposalRequest.id, __import__('sqlalchemy').String).ilike(pattern)).limit(20)).all():
            grouped["Disposal"].append({"icon": "♻️", "title": f"Disposal #{disposal.id}", "subtitle": disposal.asset.asset_code, "status": disposal.status, "url": "/disposal"})
        for plan in db.scalars(select(ProcurementPlan).where(or_(func.cast(ProcurementPlan.id, __import__('sqlalchemy').String).ilike(pattern), ProcurementPlan.category_name.ilike(pattern))).limit(20)).all():
            grouped["Procurement"].append({"icon": "📈", "title": f"Plan #{plan.id}", "subtitle": plan.category_name, "status": plan.workflow_status, "url": "/procurement"})
    return render(request, "search.html", {"q": q, "grouped_results": dict(grouped), "page_title": "Global Search", "page_subtitle": "Universal asset, employee, workflow, and document lookup"}, current_user)


@router.get("/admin/scheduled-jobs", response_class=HTMLResponse)
@require_roles("super_admin")
@require_permission("settings", "view")
async def scheduled_jobs_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    latest = {}
    for run in db.scalars(select(ScheduledJobRun).order_by(ScheduledJobRun.started_at.desc())).all():
        latest.setdefault(run.job_code, run)
    jobs = [{"code": code, "name": meta[0], "last_run": latest.get(code), "next_run": "02:00 UTC daily"} for code, meta in JOB_DEFINITIONS.items()]
    return render(request, "scheduled_jobs.html", {"jobs": jobs, "page_title": "Scheduled Jobs", "page_subtitle": "Reminder and escalation job monitor"}, current_user)


@router.post("/admin/scheduled-jobs/run/{job_code}")
@require_roles("super_admin")
@require_permission("settings", "edit")
async def run_scheduled_job(job_code: str, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if job_code not in JOB_DEFINITIONS:
        raise HTTPException(status_code=404)
    result = run_job(job_code)
    log_event(db, "scheduled_jobs", "manual_run", current_user.full_name, job_code, new_value=result.status)
    return redirect_with_flash("/admin/scheduled-jobs", f"Job {job_code} finished with status {result.status}.", "success" if result.status == "success" else "error")


SOFT_DELETE_MODELS = {
    "users": User,
    "roles": Role,
    "categories": Category,
    "vendors": Vendor,
    "locations": Location,
    "assets": Asset,
    "policies": PolicyVersion,
    "templates": PolicyTemplate,
    "workflow_settings": WorkflowSetting,
    "notification_templates": NotificationTemplate,
}


@router.get("/admin/deleted-records", response_class=HTMLResponse)
@require_roles("super_admin")
async def deleted_records_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    records = []
    for key, model in SOFT_DELETE_MODELS.items():
        if hasattr(model, "is_deleted"):
            for item in db.scalars(select(model).where(model.is_deleted == True).limit(100)).all():
                label = getattr(item, "full_name", None) or getattr(item, "name", None) or getattr(item, "title", None) or getattr(item, "asset_code", None) or getattr(item, "code", None) or f"#{item.id}"
                records.append({"module": key, "id": item.id, "label": label, "deleted_at": item.deleted_at, "deleted_by": item.deleted_by, "reason": item.delete_reason})
    return render(request, "deleted_records.html", {"records": records, "page_title": "Deleted Records Recovery", "page_subtitle": "Restore soft-deleted master and business records"}, current_user)


@router.post("/admin/deleted-records/{module}/{record_id}/restore")
@require_roles("super_admin")
async def restore_deleted_record(module: str, record_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    model = SOFT_DELETE_MODELS.get(module)
    if not model:
        raise HTTPException(status_code=404)
    item = db.get(model, record_id)
    if not item:
        raise HTTPException(status_code=404)
    item.is_deleted = False
    item.deleted_at = None
    item.deleted_by = ""
    item.delete_reason = ""
    db.commit()
    log_event(db, module, "deleted_record_restored", current_user.full_name, str(record_id))
    return redirect_with_flash("/admin/deleted-records", "Record restored.")


@router.post("/admin/deleted-records/{module}/{record_id}/permanent-delete")
@require_roles("super_admin")
async def permanent_delete_record(module: str, record_id: int, request: Request, reason: str = Form(...), db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if not reason.strip():
        return redirect_with_flash("/admin/deleted-records", "Permanent delete requires a reason.", "error")
    model = SOFT_DELETE_MODELS.get(module)
    if not model:
        raise HTTPException(status_code=404)
    item = db.get(model, record_id)
    if not item:
        raise HTTPException(status_code=404)
    db.delete(item)
    db.commit()
    log_event(db, module, "permanent_delete", current_user.full_name, str(record_id), new_value=reason.strip())
    return redirect_with_flash("/admin/deleted-records", "Record permanently deleted.")


@router.get("/policies/{policy_id}/compare", response_class=HTMLResponse)
@require_permission("policies", "view")
async def policy_compare(policy_id: int, request: Request, left_id: int | None = None, right_id: int | None = None, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    policy = db.get(PolicyVersion, policy_id)
    if not policy:
        raise HTTPException(status_code=404)
    versions = db.scalars(select(PolicyVersion).where(PolicyVersion.policy_master_id == policy.policy_master_id).order_by(PolicyVersion.created_at.desc())).all() if policy.policy_master_id else [policy]
    left = db.get(PolicyVersion, left_id) if left_id else (versions[1] if len(versions) > 1 else policy)
    right = db.get(PolicyVersion, right_id) if right_id else policy
    diff_html = HtmlDiff(wrapcolumn=90).make_table((left.body or "").splitlines(), (right.body or "").splitlines(), f"{left.version}", f"{right.version}", context=True, numlines=3)
    signed_counts = dict(db.execute(select(SignedDocument.policy_id, func.count(SignedDocument.id)).group_by(SignedDocument.policy_id)).all())
    return render(request, "policy_compare.html", {"policy": policy, "versions": versions, "left": left, "right": right, "diff_html": diff_html, "signed_counts": signed_counts, "page_title": "Policy Version Compare"}, current_user)


@router.get("/compliance/deviations", response_class=HTMLResponse)
@require_permission("audit", "view")
async def deviations_page(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    stmt = select(ComplianceDeviation).order_by(ComplianceDeviation.created_at.desc())
    if q.strip():
        stmt = stmt.where(or_(ComplianceDeviation.deviation_number.ilike(_like(q)), ComplianceDeviation.source_module.ilike(_like(q))))
    if status:
        stmt = stmt.where(ComplianceDeviation.status == status)
    rows = db.scalars(stmt.limit(100)).all()
    return render(request, "deviations.html", {"rows": rows, "q": q, "status": status, "page_title": "Compliance Deviation Register"}, current_user)


@router.get("/compliance/deviations/{deviation_id}", response_class=HTMLResponse)
@require_permission("audit", "view")
async def deviation_detail(deviation_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    item = db.get(ComplianceDeviation, deviation_id)
    if not item:
        raise HTTPException(status_code=404)
    return render(request, "deviation_detail.html", {"item": item, "page_title": item.deviation_number}, current_user)


@router.post("/compliance/deviations")
@require_permission("audit", "create")
async def create_deviation(request: Request, source_module: str = Form(...), severity: str = Form("medium"), corrective_action: str = Form(""), preventive_action: str = Form(""), db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    number = f"DEV-{(db.scalar(select(func.count(ComplianceDeviation.id))) or 0) + 1:05d}"
    db.add(ComplianceDeviation(deviation_number=number, source_module=source_module, severity=severity, corrective_action=corrective_action, preventive_action=preventive_action, responsible_person_id=current_user.id))
    db.commit()
    log_event(db, "compliance", "deviation_created", current_user.full_name, number, new_value=severity)
    return redirect_with_flash("/compliance/deviations", f"Deviation {number} created.")


@router.get("/admin/email-templates", response_class=HTMLResponse)
@require_roles("super_admin")
async def email_templates_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    ensure_email_templates(db)
    db.commit()
    templates = db.scalars(select(NotificationTemplate).order_by(NotificationTemplate.event_code)).all()
    return render(request, "email_templates.html", {"templates": templates, "page_title": "Email Template Management"}, current_user)


@router.get("/admin/notification-delivery", response_class=HTMLResponse)
@require_roles("super_admin")
async def notification_delivery_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    rows = db.scalars(select(NotificationDeliveryLog).order_by(NotificationDeliveryLog.created_at.desc()).limit(200)).all()
    return render(request, "notification_delivery.html", {"rows": rows, "page_title": "Notification Delivery Log"}, current_user)
