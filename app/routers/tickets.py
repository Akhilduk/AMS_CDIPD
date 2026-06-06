from typing import Optional
from datetime import date, datetime
from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.models import User, Category, Vendor, Location, PolicyVersion, Asset, Allocation, OTPChallenge, SignedDocument, MaintenanceTicket, TravelRequest, ReturnRequest, AuditVerification, AuditLog, WorkflowSetting, NumberingSetting, ROLE_MENUS
from app.services.helpers import get_db, render, verify_password, serializer, require_user, require_permission, require_roles, redirect_with_flash, log_event, hash_password, get_current_user, generate_asset_code, generate_document_number, build_role_dashboard, build_request_history, build_asset_history, extract_policy_sections, build_management_summary, save_qr, get_current_holder, build_policy_status, get_numbering_setting, get_published_policy, generate_signed_pdf, create_signed_document_record, create_workflow_document_record, build_otp_state, parse_change_payload, DEMO_OTP_MODE
from app.services.workflows import ASSET_TRANSITIONS, TICKET_TRANSITIONS, TRAVEL_TRANSITIONS, TransitionError, apply_transition
import io
import csv

router = APIRouter()

@router.get("/maintenance", response_class=HTMLResponse)
@require_roles("employee", "hardware_admin", "super_admin")
@require_permission("tickets", "view")
async def maintenance_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    tickets = db.scalars(select(MaintenanceTicket).order_by(MaintenanceTicket.created_at.desc())).all()
    assets_query = select(Asset)
    if current_user.role == "employee":
        allocations = db.scalars(select(Allocation).where(Allocation.employee_id == current_user.id, Allocation.status.in_(["pending_signature", "signed"]))).all()
        asset_ids = [allocation.asset_id for allocation in allocations]
        assets = db.scalars(assets_query.where(Asset.id.in_(asset_ids) if asset_ids else Asset.id == -1)).all()
    else:
        assets = db.scalars(assets_query).all()
    return render(request, "maintenance.html", {"tickets": tickets, "assets": assets}, current_user)

@router.post("/maintenance")
@require_roles("employee", "hardware_admin", "super_admin")
@require_permission("tickets", "create")
async def create_ticket(
    request: Request,
    asset_id: int = Form(...),
    issue_type: str = Form(...),
    description: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    ticket = MaintenanceTicket(asset_id=asset_id, raised_by_id=current_user.id, issue_type=issue_type, description=description)
    asset = db.get(Asset, asset_id)
    try:
        apply_transition(asset, "status", "under_maintenance", ASSET_TRANSITIONS, "asset")
    except TransitionError as exc:
        return redirect_with_flash("/maintenance", str(exc), "error")
    db.add(ticket)
    db.commit()
    log_event(db, "maintenance", "raise", current_user.full_name, str(ticket.id), new_value=issue_type)
    return redirect_with_flash("/maintenance", f"Maintenance ticket #{ticket.id} created.")

@router.get("/maintenance/{ticket_id}/update", response_class=HTMLResponse)
@require_roles("hardware_admin", "super_admin")
@require_permission("tickets", "edit")
async def ticket_update_form(request: Request, ticket_id: int, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    ticket = db.get(MaintenanceTicket, ticket_id)
    if not ticket:
        return redirect_with_flash("/maintenance", "Ticket not found.", "error")
    assets = db.scalars(select(Asset).where(Asset.status.in_(["available", "under_repair"]))).all()
    return render(
        request,
        "maintenance_update.html",
        {
            "ticket": ticket,
            "assets": assets,
            "page_title": f"Update Ticket #{ticket.id}",
            "page_subtitle": f"Asset: {ticket.asset.asset_code} — {ticket.issue_type}",
            "page_actions": [{"href": "/maintenance", "label": "Back", "variant": "secondary", "icon": "◀️"}],
            "active_path": "/maintenance",
        },
        current_user,
    )

@router.post("/maintenance/{ticket_id}/update")
@require_roles("hardware_admin", "super_admin")
@require_permission("tickets", "edit")
async def update_ticket(
    request: Request,
    ticket_id: int,
    status: str = Form(...),
    resolution_notes: str = Form(""),
    replacement_asset_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    ticket = db.get(MaintenanceTicket, ticket_id)
    old_status = ticket.status
    try:
        apply_transition(ticket, "status", status, TICKET_TRANSITIONS, "ticket")
    except TransitionError as exc:
        return redirect_with_flash("/maintenance", str(exc), "error")
    ticket.assigned_to_id = current_user.id
    ticket.resolution_notes = resolution_notes
    ticket.updated_at = datetime.utcnow()
    if status == "closed":
        try:
            apply_transition(ticket.asset, "status", "active", ASSET_TRANSITIONS, "asset")
        except TransitionError as exc:
            return redirect_with_flash("/maintenance", str(exc), "error")
    if status == "replaced" and replacement_asset_id:
        replacement = db.get(Asset, replacement_asset_id)
        ticket.replacement_asset_id = replacement_asset_id
        try:
            apply_transition(replacement, "status", "pending_allocation", ASSET_TRANSITIONS, "asset")
            apply_transition(ticket.asset, "status", "retired", ASSET_TRANSITIONS, "asset")
        except TransitionError as exc:
            return redirect_with_flash("/maintenance", str(exc), "error")
        db.add(
            Allocation(
                asset_id=replacement_asset_id,
                employee_id=ticket.raised_by_id,
                requested_by_id=current_user.id,
                status="submitted",
                remarks="Replacement asset requires fresh signing.",
            )
        )
    db.commit()
    log_event(
        db,
        "maintenance",
        status,
        current_user.full_name,
        str(ticket.id),
        old_value=f"status={old_status};replacement_asset={ticket.replacement_asset_id or '-'}",
        new_value=f"status={ticket.status};notes={resolution_notes or '-'};replacement_asset={ticket.replacement_asset_id or '-'}",
    )
    return redirect_with_flash("/maintenance", f"Ticket #{ticket.id} updated to {status.replace('_', ' ')}.")

@router.get("/travel", response_class=HTMLResponse)
@require_roles("employee", "director", "super_admin")
@require_permission("abroad_approval", "view")
async def travel_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    query = select(TravelRequest).order_by(TravelRequest.created_at.desc())
    if current_user.role == "employee":
        query = query.where(TravelRequest.employee_id == current_user.id)
        allocations = db.scalars(select(Allocation).where(Allocation.employee_id == current_user.id, Allocation.status == "signed")).all()
        assets = [allocation.asset for allocation in allocations]
    else:
        assets = db.scalars(select(Asset)).all()
    travel_requests = db.scalars(query).all()
    document_map = {
        item.source_reference: item
        for item in db.scalars(select(SignedDocument).where(SignedDocument.source_module == "travel")).all()
    }
    return render(
        request,
        "travel.html",
        {"travel_requests": travel_requests, "assets": assets, "document_map": document_map},
        current_user,
    )

@router.get("/requests", response_class=HTMLResponse)
@require_roles("employee")
async def request_history_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    history = build_request_history(db, current_user)
    return render(request, "requests.html", {"history": history}, current_user)

@router.post("/travel")
@require_roles("employee")
@require_permission("abroad_approval", "create")
async def create_travel_request(
    request: Request,
    asset_id: int = Form(...),
    destination_country: str = Form(...),
    purpose: str = Form(...),
    departure_date: date = Form(...),
    return_date: date = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    travel = TravelRequest(
        asset_id=asset_id,
        employee_id=current_user.id,
        destination_country=destination_country,
        purpose=purpose,
        departure_date=departure_date,
        return_date=return_date,
    )
    db.add(travel)
    db.commit()
    log_event(db, "travel", "submit", current_user.full_name, str(travel.id), new_value=destination_country)
    return redirect_with_flash("/travel", f"Travel request #{travel.id} submitted for approval.")

@router.get("/travel/{travel_id}/decision", response_class=HTMLResponse)
@require_roles("director", "super_admin")
@require_permission("abroad_approval", "approve")
async def travel_decision_form(request: Request, travel_id: int, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    travel = db.get(TravelRequest, travel_id)
    if not travel:
        return redirect_with_flash("/travel", "Travel request not found.", "error")
    return render(
        request,
        "travel_decision.html",
        {
            "travel": travel,
            "page_title": "Approve Travel Request",
            "page_subtitle": f"{travel.employee.full_name} — {travel.destination_country}",
            "page_actions": [{"href": "/travel", "label": "Back", "variant": "secondary", "icon": "◀️"}],
            "active_path": "/travel",
        },
        current_user,
    )

@router.post("/travel/{travel_id}/decision")
@require_roles("director", "super_admin")
@require_permission("abroad_approval", "approve")
async def travel_decision(
    request: Request,
    travel_id: int,
    decision: str = Form(...),
    director_comment: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    travel = db.get(TravelRequest, travel_id)
    target_status = "approved" if decision == "approve" else "rejected"
    try:
        apply_transition(travel, "status", target_status, TRAVEL_TRANSITIONS, "travel")
        if target_status == "approved":
            apply_transition(travel.asset, "status", "active_abroad", ASSET_TRANSITIONS, "asset")
    except TransitionError as exc:
        return redirect_with_flash("/travel", str(exc), "error")
    travel.director_comment = director_comment
    if target_status == "approved":
        create_workflow_document_record(
            db,
            template_code="TPL-ABROAD-DECL",
            actor_name=current_user.full_name,
            source_module="travel",
            source_reference=f"travel:{travel.id}",
            evidence=f"Travel approval granted by {current_user.full_name} for {travel.destination_country}.",
            asset_id=travel.asset_id,
            employee_id=travel.employee_id,
            context_overrides={
                "destination_country": travel.destination_country,
                "purpose": travel.purpose,
                "departure_date": travel.departure_date.isoformat(),
                "return_date": travel.return_date.isoformat(),
            },
        )
    db.commit()
    log_event(db, "travel", decision, current_user.full_name, str(travel.id), new_value=director_comment)
    return redirect_with_flash("/travel", f"Travel request #{travel.id} {travel.status}.", "success" if travel.status == "approved" else "warning")
