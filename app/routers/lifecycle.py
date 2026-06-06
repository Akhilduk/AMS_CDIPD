from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.models import Asset, BackupAllocation, Category, LiabilityRecord, MaintenanceTicket, ProcurementPlan, RepairEntry, ReplacementRecord, DisposalRequest, SignedDocument, User, VerificationCampaign, VerificationExecution
from app.services.helpers import create_notification, create_workflow_document_record, get_current_user, get_db, log_event, redirect_with_flash, render, require_permission, require_roles
from app.services.workflows import ASSET_TRANSITIONS, TransitionError, apply_transition

router = APIRouter()


@router.get("/repairs", response_class=HTMLResponse)
@require_roles("hardware_admin", "super_admin", "auditor")
@require_permission("repairs", "view")
async def repairs_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    repairs = db.scalars(select(RepairEntry).order_by(RepairEntry.created_at.desc())).all()
    assets = db.scalars(select(Asset).order_by(Asset.asset_code)).all()
    tickets = db.scalars(select(MaintenanceTicket).order_by(MaintenanceTicket.created_at.desc())).all()
    return render(request, "repairs.html", {"repairs": repairs, "assets": assets, "tickets": tickets}, current_user)


@router.get("/backup", response_class=HTMLResponse)
@require_roles("hardware_admin", "hr_admin", "super_admin")
@require_permission("backup", "view")
async def backup_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    backups = db.scalars(select(BackupAllocation).order_by(BackupAllocation.created_at.desc())).all()
    return render(request, "backup.html", {"backups": backups}, current_user)


@router.get("/backup/new", response_class=HTMLResponse)
@require_roles("hardware_admin", "hr_admin", "super_admin")
@require_permission("backup", "create")
async def new_backup_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    employees = db.scalars(select(User).where(User.role == "employee", User.active == True).order_by(User.full_name)).all()
    primary_assets = db.scalars(select(Asset).where(Asset.status.in_(["active", "under_maintenance"])).order_by(Asset.asset_code)).all()
    backup_assets = db.scalars(select(Asset).where(Asset.status == "available").order_by(Asset.asset_code)).all()
    return render(request, "backup_new.html", {"employees": employees, "primary_assets": primary_assets, "backup_assets": backup_assets}, current_user)


@router.get("/backup/{backup_id}", response_class=HTMLResponse)
@require_roles("hardware_admin", "hr_admin", "super_admin")
@require_permission("backup", "view")
async def backup_detail(request: Request, backup_id: int, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    item = db.get(BackupAllocation, backup_id)
    if not item:
        raise HTTPException(status_code=404, detail="Backup allocation not found")
    return render(request, "backup_detail.html", {"item": item}, current_user)


@router.post("/backup")
@require_roles("hardware_admin", "hr_admin", "super_admin")
@require_permission("backup", "create")
async def create_backup_allocation(
    request: Request,
    employee_id: int = Form(...),
    primary_asset_id: int = Form(...),
    backup_asset_id: int = Form(...),
    issue_date: date = Form(...),
    expected_return_date: date | None = Form(None),
    reason: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if expected_return_date and expected_return_date <= issue_date:
        return redirect_with_flash("/backup/new", "Expected return date must be after issue date.", "error")
    if backup_asset_id == primary_asset_id:
        return redirect_with_flash("/backup/new", "Backup asset cannot be the same as primary asset.", "error")
    primary_asset = db.get(Asset, primary_asset_id)
    if not primary_asset or primary_asset.status not in {"under_maintenance", "active"}:
        return redirect_with_flash("/backup/new", "Primary asset must be active or under repair/ticket-linked.", "error")
    backup_asset = db.get(Asset, backup_asset_id)
    if not backup_asset:
        return redirect_with_flash("/backup", "Backup asset not found.", "error")
    if backup_asset.status != "available":
        return redirect_with_flash("/backup/new", "Backup asset must be available.", "error")
    try:
        apply_transition(backup_asset, "status", "backup_allocated", ASSET_TRANSITIONS, "asset")
    except TransitionError as exc:
        return redirect_with_flash("/backup", str(exc), "error")
    item = BackupAllocation(
        employee_id=employee_id,
        primary_asset_id=primary_asset_id,
        backup_asset_id=backup_asset_id,
        issue_date=issue_date,
        expected_return_date=expected_return_date,
        reason=reason,
        status="active",
        created_by_id=current_user.id,
    )
    db.add(item)
    db.commit()
    create_notification(db, employee_id, "backup_asset_allocated", "Backup asset issued", f"A backup asset has been issued for primary asset support.", "/backup")
    db.commit()
    log_event(db, "backup", "create", current_user.full_name, str(item.id), new_value=f"backup_asset={backup_asset.asset_code}")
    return redirect_with_flash("/backup", f"Backup allocation #{item.id} created.")


@router.post("/backup/{backup_id}/close")
@require_roles("hardware_admin", "hr_admin", "super_admin")
@require_permission("backup", "create")
async def close_backup_allocation(backup_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    item = db.get(BackupAllocation, backup_id)
    if not item:
        return redirect_with_flash("/backup", "Backup allocation not found.", "error")
    try:
        apply_transition(item.backup_asset, "status", "available", ASSET_TRANSITIONS, "asset")
    except TransitionError as exc:
        return redirect_with_flash("/backup", str(exc), "error")
    item.status = "returned"
    db.commit()
    log_event(db, "backup", "close", current_user.full_name, str(item.id), new_value="returned")
    return redirect_with_flash("/backup", f"Backup allocation #{item.id} closed.")


@router.post("/repairs")
@require_roles("hardware_admin", "super_admin")
@require_permission("repairs", "create")
async def create_repair_entry(
    request: Request,
    asset_id: int = Form(...),
    ticket_id: int | None = Form(None),
    repair_type: str = Form(...),
    repair_mode: str = Form(...),
    warranty_claim: str | None = Form(None),
    repair_start_date: date | None = Form(None),
    repair_end_date: date | None = Form(None),
    repair_cost: int = Form(0),
    diagnosis: str = Form(""),
    resolution: str = Form(""),
    final_condition: str = Form("serviceable"),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if estimated_residual_value < 0:
        return redirect_with_flash("/disposal", "Estimated residual value cannot be negative.", "error")
    asset = db.get(Asset, asset_id)
    if not asset:
        return redirect_with_flash("/repairs", "Asset not found.", "error")
    repair = RepairEntry(
        asset_id=asset_id,
        ticket_id=ticket_id,
        repair_type=repair_type,
        repair_mode=repair_mode,
        warranty_claim=bool(warranty_claim),
        repair_start_date=repair_start_date,
        repair_end_date=repair_end_date,
        repair_cost=repair_cost,
        diagnosis=diagnosis,
        resolution=resolution,
        final_condition=final_condition,
        created_by_id=current_user.id,
    )
    if repair_cost > 20000:
        try:
            apply_transition(asset, "status", "disposal_candidate", ASSET_TRANSITIONS, "asset")
        except TransitionError:
            pass
    db.add(repair)
    db.commit()
    log_event(db, "repairs", "create", current_user.full_name, str(repair.id), new_value=f"asset={asset.asset_code};cost={repair_cost}")
    return redirect_with_flash("/repairs", f"Repair entry #{repair.id} created.")


@router.get("/verification", response_class=HTMLResponse)
@require_roles("hardware_admin", "hr_admin", "super_admin", "auditor")
@require_permission("verification", "view")
async def verification_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    campaigns = db.scalars(select(VerificationCampaign).order_by(VerificationCampaign.created_at.desc())).all()
    executions = db.scalars(select(VerificationExecution).order_by(VerificationExecution.created_at.desc())).all()
    assets = db.scalars(select(Asset).order_by(Asset.asset_code)).all()
    categories = db.scalars(select(Category).order_by(Category.name)).all()
    departments = sorted({item.department for item in db.scalars(select(User)).all() if item.department})
    locations = sorted({asset.location.name for asset in assets})
    return render(request, "verification.html", {"campaigns": campaigns, "executions": executions, "assets": assets, "categories": categories, "departments": departments, "locations": locations}, current_user)


@router.post("/verification/campaigns")
@require_roles("hardware_admin", "hr_admin", "super_admin")
@require_permission("verification", "create")
async def create_verification_campaign(
    request: Request,
    campaign_name: str = Form(...),
    asset_category: str = Form(""),
    department: str = Form(""),
    location: str = Form(""),
    start_date: date | None = Form(None),
    end_date: date | None = Form(None),
    verification_mode: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    campaign = VerificationCampaign(
        campaign_name=campaign_name,
        asset_category=asset_category,
        department=department,
        location=location,
        start_date=start_date,
        end_date=end_date,
        verification_mode=verification_mode,
        status="active",
        created_by_id=current_user.id,
    )
    db.add(campaign)
    db.commit()
    log_event(db, "verification", "campaign_create", current_user.full_name, str(campaign.id), new_value=campaign_name)
    return redirect_with_flash("/verification", f"Verification campaign #{campaign.id} created.")


@router.post("/verification/executions")
@require_roles("hardware_admin", "hr_admin", "super_admin", "auditor")
@require_permission("verification", "create")
async def create_verification_execution(
    request: Request,
    campaign_id: int = Form(...),
    asset_id: int = Form(...),
    result: str = Form(...),
    condition_confirmed: str = Form(""),
    location_confirmed: str = Form(""),
    holder_confirmed: str = Form(""),
    deviation_notes: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    execution = VerificationExecution(
        campaign_id=campaign_id,
        asset_id=asset_id,
        verified_by_id=current_user.id,
        result=result,
        condition_confirmed=condition_confirmed,
        location_confirmed=location_confirmed,
        holder_confirmed=holder_confirmed,
        deviation_notes=deviation_notes,
    )
    db.add(execution)
    db.commit()
    log_event(db, "verification", "execution_create", current_user.full_name, str(execution.id), new_value=f"result={result}")
    return redirect_with_flash("/verification", f"Verification execution #{execution.id} recorded.")


@router.get("/replacement", response_class=HTMLResponse)
@require_roles("hardware_admin", "super_admin")
@require_permission("replacement", "view")
async def replacement_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    replacements = db.scalars(select(ReplacementRecord).order_by(ReplacementRecord.created_at.desc())).all()
    assets = db.scalars(select(Asset).order_by(Asset.asset_code)).all()
    employees = db.scalars(select(User).where(User.role == "employee", User.active == True).order_by(User.full_name)).all()
    tickets = db.scalars(select(MaintenanceTicket).order_by(MaintenanceTicket.created_at.desc())).all()
    return render(request, "replacement.html", {"replacements": replacements, "assets": assets, "employees": employees, "tickets": tickets}, current_user)


@router.post("/replacement")
@require_roles("hardware_admin", "super_admin")
@require_permission("replacement", "create")
async def create_replacement_record(
    request: Request,
    old_asset_id: int = Form(...),
    new_asset_id: int = Form(...),
    employee_id: int = Form(...),
    ticket_id: int | None = Form(None),
    reason: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    old_asset = db.get(Asset, old_asset_id)
    new_asset = db.get(Asset, new_asset_id)
    if not old_asset or not new_asset:
        return redirect_with_flash("/replacement", "Replacement assets are invalid.", "error")
    record = ReplacementRecord(
        old_asset_id=old_asset_id,
        new_asset_id=new_asset_id,
        employee_id=employee_id,
        ticket_id=ticket_id,
        reason=reason,
        status="initiated",
        created_by_id=current_user.id,
    )
    try:
        apply_transition(new_asset, "status", "pending_allocation", ASSET_TRANSITIONS, "asset")
    except TransitionError as exc:
        return redirect_with_flash("/replacement", str(exc), "error")
    db.add(record)
    db.commit()
    create_notification(db, employee_id, "replacement_initiated", "Replacement initiated", f"A replacement workflow has been created for {old_asset.asset_code}.", "/replacement")
    db.commit()
    log_event(db, "replacement", "initiate", current_user.full_name, str(record.id), new_value=f"{old_asset.asset_code}->{new_asset.asset_code}")
    return redirect_with_flash("/replacement", f"Replacement record #{record.id} created.")


@router.get("/liabilities", response_class=HTMLResponse)
@require_roles("hr_admin", "super_admin")
@require_permission("liability", "view")
async def liabilities_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    liabilities = db.scalars(select(LiabilityRecord).order_by(LiabilityRecord.created_at.desc())).all()
    employees = db.scalars(select(User).where(User.role == "employee").order_by(User.full_name)).all()
    assets = db.scalars(select(Asset).order_by(Asset.asset_code)).all()
    return render(request, "liabilities.html", {"liabilities": liabilities, "employees": employees, "assets": assets}, current_user)


@router.post("/liabilities")
@require_roles("hr_admin", "super_admin")
@require_permission("liability", "approve")
async def create_liability(
    request: Request,
    employee_id: int = Form(...),
    asset_id: int | None = Form(None),
    liability_type: str = Form(...),
    amount: int = Form(...),
    remarks: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    record = LiabilityRecord(
        employee_id=employee_id,
        asset_id=asset_id,
        liability_type=liability_type,
        amount=amount,
        remarks=remarks,
        recovery_status="pending",
        hr_approval_status="approved",
        source_reference=f"manual-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
    )
    db.add(record)
    db.commit()
    create_notification(db, employee_id, "liability_created", "Liability recorded", f"A liability of {amount} has been recorded against your account.", "/liabilities")
    db.commit()
    log_event(db, "liability", "create", current_user.full_name, str(record.id), new_value=f"type={liability_type};amount={amount}")
    return redirect_with_flash("/liabilities", f"Liability record #{record.id} created.")


@router.get("/disposal", response_class=HTMLResponse)
@require_roles("hardware_admin", "director", "super_admin")
@require_permission("disposal", "view")
async def disposal_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    disposal_requests = db.scalars(select(DisposalRequest).order_by(DisposalRequest.created_at.desc())).all()
    candidates = db.scalars(select(Asset).where(Asset.status.in_(["damaged", "retired", "disposal_candidate", "liability_hold"])).order_by(Asset.asset_code)).all()
    document_map = {
        item.source_reference: item
        for item in db.scalars(select(SignedDocument).where(SignedDocument.source_module == "disposal")).all()
    }
    return render(
        request,
        "disposal.html",
        {"disposal_requests": disposal_requests, "candidates": candidates, "document_map": document_map},
        current_user,
    )


@router.post("/disposal")
@require_roles("hardware_admin", "super_admin")
@require_permission("disposal", "create")
async def create_disposal_request(
    request: Request,
    asset_id: int = Form(...),
    reason: str = Form(...),
    condition: str = Form(...),
    estimated_residual_value: int = Form(0),
    recommendation: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if estimated_residual_value < 0:
        return redirect_with_flash("/disposal", "Estimated residual value cannot be negative.", "error")
    asset = db.get(Asset, asset_id)
    if not asset:
        return redirect_with_flash("/disposal", "Asset not found.", "error")
    try:
        if asset.status in {"damaged", "retired", "liability_hold"}:
            apply_transition(asset, "status", "disposal_candidate", ASSET_TRANSITIONS, "asset")
    except TransitionError as exc:
        return redirect_with_flash("/disposal", str(exc), "error")
    item = DisposalRequest(
        asset_id=asset_id,
        requested_by_id=current_user.id,
        reason=reason,
        condition=condition,
        estimated_residual_value=estimated_residual_value,
        recommendation=recommendation,
        status="submitted",
    )
    db.add(item)
    db.commit()
    directors = db.scalars(select(User).where(User.role == "director", User.active == True)).all()
    for director in directors:
        create_notification(db, director.id, "disposal_submitted", "Disposal submitted", f"Disposal request for {asset.asset_code} requires review.", "/disposal")
    db.commit()
    log_event(db, "disposal", "submit", current_user.full_name, str(item.id), new_value=f"asset={asset.asset_code};condition={condition}")
    return redirect_with_flash("/disposal", f"Disposal request #{item.id} submitted.")


@router.post("/disposal/{disposal_id}/close")
@require_roles("director", "super_admin")
@require_permission("disposal", "approve")
async def close_disposal_request(
    request: Request,
    disposal_id: int,
    certificate_number: str = Form(...),
    disposal_method: str = Form(...),
    vendor_agency: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if not certificate_number.strip():
        return redirect_with_flash("/disposal", "Disposal certificate is required before closure.", "error")
    item = db.get(DisposalRequest, disposal_id)
    if not item:
        return redirect_with_flash("/disposal", "Disposal request not found.", "error")
    if item.status not in {"approved", "certificate_uploaded", "submitted"}:
        return redirect_with_flash("/disposal", "Disposal must be approved before scrapping/closure.", "error")
    try:
        apply_transition(item.asset, "status", "scrapped", ASSET_TRANSITIONS, "asset")
    except TransitionError as exc:
        return redirect_with_flash("/disposal", str(exc), "error")
    item.status = "closed"
    item.certificate_number = certificate_number
    item.disposal_method = disposal_method
    item.vendor_agency = vendor_agency
    item.disposal_date = date.today()
    create_workflow_document_record(
        db,
        template_code="TPL-DISPOSAL-CERT",
        actor_name=current_user.full_name,
        source_module="disposal",
        source_reference=f"disposal:{item.id}",
        evidence=f"Disposal certificate {certificate_number} closed by {current_user.full_name}.",
        asset_id=item.asset_id,
        context_overrides={
            "certificate_number": certificate_number,
            "disposal_method": disposal_method,
            "vendor_agency": vendor_agency or "-",
            "condition_notes": item.condition,
        },
    )
    db.commit()
    log_event(db, "disposal", "close", current_user.full_name, str(item.id), new_value=f"certificate={certificate_number}")
    return redirect_with_flash("/disposal", f"Disposal request #{item.id} closed.")


@router.get("/procurement", response_class=HTMLResponse)
@require_roles("director", "super_admin")
@require_permission("procurement", "view")
async def procurement_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    plans = db.scalars(select(ProcurementPlan).order_by(ProcurementPlan.created_at.desc())).all()
    categories = db.scalars(select(Category).order_by(Category.name)).all()
    stock_map = {
        category.name: db.scalar(select(func.count(Asset.id)).join(Category, Asset.category_id == Category.id).where(Category.name == category.name)) or 0
        for category in categories
    }
    return render(request, "procurement.html", {"plans": plans, "categories": categories, "stock_map": stock_map}, current_user)


@router.post("/procurement")
@require_roles("director", "super_admin")
@require_permission("procurement", "create")
async def create_procurement_plan(
    request: Request,
    category_name: str = Form(...),
    department: str = Form(...),
    forecast_period_months: int = Form(...),
    expected_demand: int = Form(...),
    replacement_due: int = Form(...),
    available_reusable_stock: int = Form(...),
    estimated_budget: int = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if forecast_period_months <= 0:
        return redirect_with_flash("/procurement", "Forecast period is required.", "error")
    if estimated_budget < 0:
        return redirect_with_flash("/procurement", "Estimated budget must be non-negative.", "error")
    suggested_procurement = max(0, expected_demand + replacement_due - available_reusable_stock)
    plan = ProcurementPlan(
        category_name=category_name,
        department=department,
        forecast_period_months=forecast_period_months,
        expected_demand=expected_demand,
        replacement_due=replacement_due,
        available_reusable_stock=available_reusable_stock,
        suggested_procurement=suggested_procurement,
        estimated_budget=estimated_budget,
        created_by_id=current_user.id,
    )
    db.add(plan)
    db.commit()
    log_event(db, "procurement", "generate", current_user.full_name, str(plan.id), new_value=f"category={category_name};suggested={suggested_procurement}")
    return redirect_with_flash("/procurement", f"Procurement plan #{plan.id} generated.")
