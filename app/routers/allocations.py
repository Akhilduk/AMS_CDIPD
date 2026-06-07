import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import or_, select, func
from sqlalchemy.orm import Session

from app.core.config import OTP_RESEND_COOLDOWN_SECONDS, STORAGE_DIR
from app.models.models import Allocation, Asset, AuditLog, OTPChallenge, PolicyVersion, SignedDocument, User, Notification
from app.services.helpers import POLICY_TERMS, DEMO_OTP_MODE, build_otp_state, create_notification, create_signed_document_record, ensure_signed_document_templates, ensure_workflow_documents, get_current_user, get_db, log_event, redirect_with_flash, render, require_permission, require_roles, require_user, user_has_permission, user_has_role
from app.services.workflows import ALLOCATION_TRANSITIONS, ASSET_TRANSITIONS, TransitionError, apply_transition, ensure_transition

router = APIRouter()

@router.get("/allocations", response_class=HTMLResponse)
@require_roles("hardware_admin", "hr_admin", "super_admin", "employee")
@require_permission("allocation", "view")
async def allocations_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    query = select(Allocation).order_by(Allocation.created_at.desc())
    if user_has_role(current_user, "employee") and not user_has_role(current_user, "hr_admin", "hardware_admin", "super_admin", "director", "auditor"):
        query = query.where(Allocation.employee_id == current_user.id)
    allocations = db.scalars(query).all()
    assets = db.scalars(select(Asset).where(Asset.status.in_(["available", "active", "under_maintenance"]))).all()
    employees = db.scalars(select(User).where(User.role == "employee")).all()
    policies = db.scalars(select(PolicyVersion).where(PolicyVersion.published == True)).all()
    documents = db.scalars(select(SignedDocument)).all()
    return render(
        request,
        "allocations.html",
        {
            "allocations": allocations,
            "assets": assets,
            "employees": employees,
            "policies": policies,
            "demo_otp_mode": DEMO_OTP_MODE,
            "active_policy": policies[0] if policies else None,
            "otp_state": build_otp_state(db, allocations),
            "document_map": {item.allocation_id: item for item in documents},
            "policy_terms": POLICY_TERMS[:5],
        },
        current_user,
    )

@router.post("/allocations")
@require_roles("hardware_admin", "super_admin")
@require_permission("allocation", "create")
async def create_allocation(
    request: Request,
    asset_id: int = Form(...),
    employee_id: int = Form(...),
    remarks: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    asset = db.get(Asset, asset_id)
    if not asset or asset.status != "available":
        return redirect_with_flash("/allocations", "Selected asset is not available for allocation.", "error")
    employee = db.get(User, employee_id)
    if not employee or not employee.active:
        return redirect_with_flash("/allocations", "Selected employee is not active.", "error")
    allocation = Allocation(asset_id=asset_id, employee_id=employee_id, requested_by_id=current_user.id, status="submitted", remarks=remarks)
    apply_transition(asset, "status", "pending_allocation", ASSET_TRANSITIONS, "asset")
    db.add(allocation)
    db.flush()
    hr_admins = db.scalars(select(User).where(User.role == "hr_admin", User.active == True)).all()
    for hr_admin in hr_admins:
        create_notification(
            db,
            hr_admin.id,
            "allocation_submitted",
            "Allocation approval pending",
            f"Allocation #{allocation.id} for asset {asset.asset_code} is waiting for HR review.",
            "/allocations",
        )
    db.commit()
    log_event(db, "allocation", "submit", current_user.full_name, str(allocation.id), new_value=f"{asset.asset_code}->{employee_id}")
    return redirect_with_flash("/allocations", f"Allocation for {asset.asset_code} submitted for HR approval.")

@router.get("/allocations/{allocation_id}/decision", response_class=HTMLResponse)
@require_roles("hr_admin", "super_admin")
@require_permission("allocation", "approve")
async def allocation_decision_form(request: Request, allocation_id: int, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    allocation = db.get(Allocation, allocation_id)
    if not allocation:
        return redirect_with_flash("/allocations", "Allocation not found.", "error")
    policies = db.scalars(select(PolicyVersion).where(PolicyVersion.published == True)).all()
    return render(
        request,
        "allocations_decision.html",
        {
            "allocation": allocation,
            "policies": policies,
            "page_title": "Allocation Decision",
            "page_subtitle": "",
            "page_actions": [],
            "active_path": "/allocations",
        },
        current_user,
    )

@router.post("/allocations/{allocation_id}/decision")
@require_roles("hr_admin", "super_admin")
@require_permission("allocation", "approve")
async def allocation_decision(
    request: Request,
    allocation_id: int,
    decision: str = Form(...),
    policy_id: Optional[int] = Form(None),
    remarks: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    allocation = db.get(Allocation, allocation_id)
    old_status = allocation.status
    allocation.approved_by_id = current_user.id
    allocation.remarks = remarks
    try:
        if decision == "approve":
            apply_transition(allocation, "status", "pending_signature", ALLOCATION_TRANSITIONS, "allocation")
            apply_transition(allocation.asset, "status", "pending_signature", ASSET_TRANSITIONS, "asset")
            if policy_id:
                allocation.policy_id = policy_id
            create_notification(
                db,
                allocation.employee_id,
                "signature_pending",
                "Signature required",
                f"Allocation #{allocation.id} is approved and waiting for your OTP-based signature.",
                "/allocations",
            )
        elif decision == "send_back":
            apply_transition(allocation, "status", "sent_back", ALLOCATION_TRANSITIONS, "allocation")
        else:
            apply_transition(allocation, "status", "rejected", ALLOCATION_TRANSITIONS, "allocation")
            apply_transition(allocation.asset, "status", "available", ASSET_TRANSITIONS, "asset")
            create_notification(
                db,
                allocation.requested_by_id,
                "allocation_rejected",
                "Allocation rejected",
                f"Allocation #{allocation.id} was rejected by HR. Review the remarks and resubmit if needed.",
                "/allocations",
            )
    except TransitionError as exc:
        return redirect_with_flash("/allocations", str(exc), "error")
    allocation.updated_at = datetime.utcnow()
    db.commit()
    log_event(
        db,
        "allocation",
        decision,
        current_user.full_name,
        str(allocation.id),
        old_value=f"status={old_status};remarks={allocation.remarks or '-'}",
        new_value=f"status={allocation.status};remarks={remarks or '-'}",
    )
    decision_text = {"approve": "approved", "send_back": "sent back", "reject": "rejected"}[decision]
    level = "success" if decision == "approve" else "warning"
    return redirect_with_flash("/allocations", f"Allocation #{allocation.id} {decision_text}.", level)

@router.post("/allocations/{allocation_id}/send-otp")
@require_roles("employee", "hardware_admin", "hr_admin", "super_admin")
@require_permission("signing", "view")
async def send_otp(
    request: Request,
    allocation_id: int,
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    allocation = db.get(Allocation, allocation_id)
    if current_user.role == "employee" and allocation.employee_id != current_user.id:
        raise HTTPException(status_code=403)
    latest_challenge = db.scalar(
        select(OTPChallenge)
        .where(OTPChallenge.allocation_id == allocation.id)
        .order_by(OTPChallenge.created_at.desc())
    )
    return_path = f"/allocations/{allocation.id}/sign" if current_user.role == "employee" else "/allocations"
    if latest_challenge:
        resend_at = latest_challenge.created_at + timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS)
        remaining = int((resend_at - datetime.utcnow()).total_seconds())
        if remaining > 0:
            return redirect_with_flash(return_path, f"Please wait {remaining} seconds before requesting another OTP.", "warning")
    otp_code = f"{secrets.randbelow(1000000):06d}"
    db.add(OTPChallenge(allocation_id=allocation.id, otp_code=otp_code, expires_at=datetime.utcnow() + timedelta(minutes=10)))
    db.commit()
    log_event(db, "otp", "generate", current_user.full_name, str(allocation.id), new_value=otp_code if DEMO_OTP_MODE else "generated")
    message = f"OTP generated for allocation #{allocation.id}."
    if DEMO_OTP_MODE:
        message += f" Demo OTP: {otp_code}"
    return redirect_with_flash(return_path, message)

@router.get("/allocations/{allocation_id}/sign", response_class=HTMLResponse)
@require_roles("employee")
@require_permission("signing", "sign")
async def sign_allocation_form(request: Request, allocation_id: int, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    allocation = db.get(Allocation, allocation_id)
    if not allocation:
        return redirect_with_flash("/allocations", "Allocation not found.", "error")
    return render(
        request,
        "allocations_sign.html",
        {
            "allocation": allocation,
            "demo_otp_mode": DEMO_OTP_MODE,
            "otp_state": build_otp_state(db, [allocation]),
            "page_title": "Sign Allocation",
            "page_subtitle": "",
            "page_actions": [],
            "active_path": "/allocations",
        },
        current_user,
    )

@router.post("/allocations/{allocation_id}/sign")
@require_roles("employee")
@require_permission("signing", "sign")
async def sign_allocation(
    request: Request,
    allocation_id: int,
    otp_code: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    allocation = db.get(Allocation, allocation_id)
    if allocation.employee_id != current_user.id:
        raise HTTPException(status_code=403)
    challenge = db.scalar(
        select(OTPChallenge)
        .where(OTPChallenge.allocation_id == allocation_id, OTPChallenge.consumed == False)
        .order_by(OTPChallenge.created_at.desc())
    )
    if not challenge or challenge.expires_at < datetime.utcnow() or challenge.otp_code != otp_code:
        return redirect_with_flash(f"/allocations/{allocation.id}/sign", "OTP is invalid or expired. Generate a new code and try again.", "error")
    challenge.consumed = True
    old_status = allocation.status
    try:
        apply_transition(allocation, "status", "signed", ALLOCATION_TRANSITIONS, "allocation")
        apply_transition(allocation.asset, "status", "active", ASSET_TRANSITIONS, "asset")
    except TransitionError as exc:
        return redirect_with_flash(f"/allocations/{allocation.id}/sign", str(exc), "error")
    document = create_signed_document_record(db, allocation, otp_code, current_user.full_name)
    for user in db.scalars(select(User).where(User.role.in_(["hr_admin", "hardware_admin"]), User.active == True)).all():
        create_notification(
            db,
            user.id,
            "signature_completed",
            "Asset signing completed",
            f"Allocation #{allocation.id} has been signed. Document {document.document_number} is available.",
            "/documents",
        )
    db.commit()
    log_event(
        db,
        "allocation",
        "sign",
        current_user.full_name,
        str(allocation.id),
        old_value=f"status={old_status};asset_status=pending_signature",
        new_value=f"status={allocation.status};document={document.document_number};asset_status={allocation.asset.status}",
    )
    return redirect_with_flash("/allocations", f"Allocation signed successfully. Document {document.document_number} is ready.")

@router.get("/documents/{document_id}")
async def view_document(document_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    if not user_has_permission(current_user, "signing", "view"):
        raise HTTPException(status_code=403)
    document = db.get(SignedDocument, document_id)
    if not document:
        raise HTTPException(status_code=404)
    if current_user.role == "employee" and document.employee_id != current_user.id:
        raise HTTPException(status_code=403)
    log_event(db, "document", "download", current_user.full_name, document.document_number, new_value=document.hash_sha256)
    return FileResponse(STORAGE_DIR / document.file_path, filename=Path(document.file_path).name)

@router.get("/documents", response_class=HTMLResponse)
@require_roles("employee", "auditor", "super_admin", "hr_admin", "hardware_admin", "director")
@require_permission("signing", "view")
async def documents_page(request: Request, db: Session = Depends(get_db)):
    ensure_signed_document_templates(db)
    ensure_workflow_documents(db)
    current_user = get_current_user(request, db)
    query = select(SignedDocument).order_by(SignedDocument.created_at.desc())
    if current_user.role == "employee":
        query = query.where(SignedDocument.employee_id == current_user.id)
    documents = db.scalars(query).all()
    return render(request, "documents.html", {"documents": documents}, current_user)

@router.get("/documents/{document_id}/view", response_class=HTMLResponse)
async def view_document_html(document_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    if not user_has_permission(current_user, "signing", "view"):
        raise HTTPException(status_code=403)
    ensure_signed_document_templates(db)
    ensure_workflow_documents(db)
    document = db.get(SignedDocument, document_id)
    if not document:
        raise HTTPException(status_code=404)
    if current_user.role == "employee" and document.employee_id != current_user.id:
        raise HTTPException(status_code=403)
    allocation = document.allocation
    subject_employee = document.employee or (allocation.employee if allocation else None)
    subject_asset = document.asset or (allocation.asset if allocation else None)
    subject_policy = document.policy or (allocation.policy if allocation else None)
    compare_id = request.query_params.get("compare_id")
    related_filters = []
    if document.employee_id:
        related_filters.append(SignedDocument.employee_id == document.employee_id)
    if document.asset_id:
        related_filters.append(SignedDocument.asset_id == document.asset_id)
    related_documents = []
    if related_filters:
        related_documents = db.scalars(
            select(SignedDocument)
            .where(
                SignedDocument.id != document.id,
                or_(*related_filters),
            )
            .order_by(SignedDocument.created_at.desc())
            .limit(6)
        ).all()
    compare_document = next((item for item in related_documents if str(item.id) == compare_id), None)
    compare_allocation = compare_document.allocation if compare_document else None
    compare_employee = (compare_document.employee if compare_document else None) or (compare_allocation.employee if compare_allocation else None)
    compare_asset = (compare_document.asset if compare_document else None) or (compare_allocation.asset if compare_allocation else None)
    compare_policy = (compare_document.policy if compare_document else None) or (compare_allocation.policy if compare_allocation else None)
    timeline_filters = [AuditLog.reference_id == document.document_number]
    if document.source_reference:
        timeline_filters.append(AuditLog.reference_id == document.source_reference)
    if allocation:
        timeline_filters.append(AuditLog.reference_id == str(allocation.id))
    if document.asset:
        timeline_filters.append(AuditLog.reference_id == document.asset.asset_code)
    document_timeline = db.scalars(select(AuditLog).where(or_(*timeline_filters)).order_by(AuditLog.created_at.asc())).all()
    return render(
        request,
        "document_view.html",
        {
            "document": document,
            "allocation": allocation,
            "subject_employee": subject_employee,
            "subject_asset": subject_asset,
            "subject_policy": subject_policy,
            "document_timeline": document_timeline,
            "related_documents": related_documents,
            "compare_document": compare_document,
            "compare_allocation": compare_allocation,
            "compare_employee": compare_employee,
            "compare_asset": compare_asset,
            "compare_policy": compare_policy,
        },
        current_user,
    )
