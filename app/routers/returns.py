from typing import Optional
from datetime import date, datetime
from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.models import User, Category, Vendor, Location, PolicyVersion, Asset, Allocation, OTPChallenge, SignedDocument, MaintenanceTicket, TravelRequest, ReturnRequest, AuditVerification, AuditLog, WorkflowSetting, NumberingSetting, ROLE_MENUS
from app.services.helpers import get_db, render, verify_password, serializer, require_user, require_permission, require_roles, redirect_with_flash, log_event, hash_password, get_current_user, generate_asset_code, generate_document_number, build_role_dashboard, build_request_history, build_asset_history, extract_policy_sections, build_management_summary, save_qr, get_current_holder, build_policy_status, get_numbering_setting, get_published_policy, generate_signed_pdf, create_signed_document_record, create_workflow_document_record, build_otp_state, parse_change_payload, DEMO_OTP_MODE
from app.services.workflows import ALLOCATION_TRANSITIONS, ASSET_TRANSITIONS, EXIT_CLEARANCE_TRANSITIONS, RETURN_TRANSITIONS, TransitionError, apply_transition
import io
import csv

router = APIRouter()

@router.get("/returns", response_class=HTMLResponse)
@require_roles("employee", "hardware_admin", "hr_admin", "super_admin")
@require_permission("returns", "view")
async def returns_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    returns = db.scalars(select(ReturnRequest).order_by(ReturnRequest.created_at.desc())).all()
    if current_user.role == "employee":
        allocations = db.scalars(select(Allocation).where(Allocation.employee_id == current_user.id, Allocation.status == "signed")).all()
        assets = [allocation.asset for allocation in allocations]
    else:
        assets = db.scalars(select(Asset)).all()
    documents = db.scalars(select(SignedDocument).where(SignedDocument.source_module.in_(["return", "exit_clearance"]))).all()
    document_map = {(item.source_module, item.source_reference): item for item in documents}
    return render(
        request,
        "returns.html",
        {"returns": returns, "assets": assets, "document_map": document_map},
        current_user,
    )

@router.get("/returns/new", response_class=HTMLResponse)
@require_roles("employee", "hardware_admin", "hr_admin", "super_admin")
@require_permission("returns", "create")
async def new_return_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if current_user.role == "employee":
        allocations = db.scalars(select(Allocation).where(Allocation.employee_id == current_user.id, Allocation.status == "signed")).all()
        assets = [allocation.asset for allocation in allocations]
    else:
        assets = db.scalars(select(Asset)).all()
    return render(request, "returns_new.html", {"assets": assets}, current_user)

@router.get("/returns/{return_id}", response_class=HTMLResponse)
@require_roles("hardware_admin", "hr_admin", "super_admin")
@require_permission("returns", "view")
async def return_detail(request: Request, return_id: int, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if liability_amount < 0:
        return redirect_with_flash("/returns", "Liability amount cannot be negative.", "error")
    if exit_clearance_status == "completed" and not qr_verified:
        return redirect_with_flash("/returns", "Final clearance is blocked until the asset is verified.", "error")
    if exit_clearance_status == "completed" and liability_amount > 0:
        return redirect_with_flash("/returns", "No-dues/final clearance is blocked while liability is pending.", "error")
    item = db.get(ReturnRequest, return_id)
    if not item:
        raise HTTPException(status_code=404, detail="Return request not found")
    documents = db.scalars(select(SignedDocument).where(SignedDocument.source_module.in_(["return", "exit_clearance"]))).all()
    document_map = {(doc.source_module, doc.source_reference): doc for doc in documents}
    return render(request, "returns_detail.html", {"item": item, "document_map": document_map}, current_user)

@router.post("/returns")
@require_roles("employee", "hardware_admin", "hr_admin", "super_admin")
@require_permission("returns", "create")
async def create_return(
    request: Request,
    asset_id: int = Form(...),
    reason: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    employee_id = current_user.id
    if current_user.role != "employee":
        latest = db.scalar(select(Allocation).where(Allocation.asset_id == asset_id).order_by(Allocation.created_at.desc()))
        employee_id = latest.employee_id if latest else current_user.id
    item = ReturnRequest(asset_id=asset_id, employee_id=employee_id, initiated_by_role=current_user.role, reason=reason)
    asset = db.get(Asset, asset_id)
    try:
        if asset:
            apply_transition(asset, "status", "return_pending", ASSET_TRANSITIONS, "asset")
    except TransitionError as exc:
        return redirect_with_flash("/returns", str(exc), "error")
    db.add(item)
    db.commit()
    log_event(db, "return", "initiate", current_user.full_name, str(item.id), new_value=reason)
    return redirect_with_flash("/returns", f"Return request #{item.id} initiated.")

@router.post("/returns/{return_id}/verify")
@require_roles("hardware_admin", "hr_admin", "super_admin")
@require_permission("returns", "approve")
async def verify_return(
    request: Request,
    return_id: int,
    condition_notes: str = Form(...),
    qr_verified: Optional[str] = Form(None),
    liability_amount: int = Form(0),
    exit_clearance_status: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if liability_amount < 0:
        return redirect_with_flash("/returns", "Liability amount cannot be negative.", "error")
    if exit_clearance_status == "completed" and not qr_verified:
        return redirect_with_flash("/returns", "Final clearance is blocked until the asset is verified.", "error")
    if exit_clearance_status == "completed" and liability_amount > 0:
        return redirect_with_flash("/returns", "No-dues/final clearance is blocked while liability is pending.", "error")
    item = db.get(ReturnRequest, return_id)
    old_status = item.status
    old_clearance = item.exit_clearance_status
    old_liability = item.liability_amount
    item.condition_notes = condition_notes
    item.qr_verified = bool(qr_verified)
    item.liability_amount = liability_amount
    try:
        apply_transition(item, "status", "verified", RETURN_TRANSITIONS, "return")
        apply_transition(item, "exit_clearance_status", exit_clearance_status, EXIT_CLEARANCE_TRANSITIONS, "exit clearance")
        if exit_clearance_status == "completed" and liability_amount == 0:
            target_asset_status = "available"
        elif exit_clearance_status == "liability_open" or liability_amount > 0:
            target_asset_status = "liability_hold"
        else:
            target_asset_status = "return_pending"
        apply_transition(item.asset, "status", target_asset_status, ASSET_TRANSITIONS, "asset")
    except TransitionError as exc:
        return redirect_with_flash("/returns", str(exc), "error")
    latest_allocation = db.scalar(select(Allocation).where(Allocation.asset_id == item.asset_id).order_by(Allocation.created_at.desc()))
    if latest_allocation and exit_clearance_status == "completed":
        try:
            apply_transition(latest_allocation, "status", "returned", ALLOCATION_TRANSITIONS, "allocation")
        except TransitionError as exc:
            return redirect_with_flash("/returns", str(exc), "error")
    create_workflow_document_record(
        db,
        template_code="TPL-RETURN-DECL",
        actor_name=current_user.full_name,
        source_module="return",
        source_reference=f"return:{item.id}",
        evidence=f"Return verified by {current_user.full_name}.",
        asset_id=item.asset_id,
        employee_id=item.employee_id,
        allocation=latest_allocation,
        context_overrides={
            "condition_notes": item.condition_notes,
            "exit_clearance_status": item.exit_clearance_status,
            "liability_amount": str(item.liability_amount),
        },
    )
    if exit_clearance_status == "completed" and liability_amount == 0:
        create_workflow_document_record(
            db,
            template_code="TPL-NO-DUES",
            actor_name=current_user.full_name,
            source_module="exit_clearance",
            source_reference=f"return:{item.id}:no-dues",
            evidence=f"No-dues clearance completed by {current_user.full_name}.",
            asset_id=item.asset_id,
            employee_id=item.employee_id,
            allocation=latest_allocation,
            context_overrides={
                "condition_notes": item.condition_notes,
                "exit_clearance_status": item.exit_clearance_status,
            },
        )
    db.commit()
    log_event(
        db,
        "return",
        "verify",
        current_user.full_name,
        str(item.id),
        old_value=f"status={old_status};clearance={old_clearance};liability={old_liability}",
        new_value=f"status={item.status};clearance={item.exit_clearance_status};liability={item.liability_amount};notes={condition_notes}",
    )
    return redirect_with_flash("/returns", f"Return request #{item.id} verified with clearance status {exit_clearance_status.replace('_', ' ')}.")
