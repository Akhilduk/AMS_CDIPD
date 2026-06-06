from typing import Optional
from datetime import date, datetime
from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.models import User, Category, Vendor, Location, PolicyVersion, Asset, Allocation, OTPChallenge, SignedDocument, MaintenanceTicket, TravelRequest, ReturnRequest, AuditVerification, AuditLog, WorkflowSetting, NumberingSetting, ROLE_MENUS
from app.services.helpers import get_db, render, verify_password, serializer, require_user, require_permission, require_roles, redirect_with_flash, log_event, hash_password, get_current_user, generate_asset_code, generate_document_number, build_role_dashboard, build_request_history, build_asset_history, extract_policy_sections, build_management_summary, save_qr, get_current_holder, build_policy_status, get_numbering_setting, get_published_policy, generate_signed_pdf, create_signed_document_record, build_otp_state, parse_change_payload, DEMO_OTP_MODE
import io
import csv

router = APIRouter()

@router.get("/assets", response_class=HTMLResponse)
@require_roles("hardware_admin", "super_admin", "auditor")
@require_permission("assets", "view")
async def assets_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    assets = db.scalars(select(Asset).order_by(Asset.created_at.desc())).all()
    employees = db.scalars(select(User).where(User.role == "employee")).all()
    categories = db.scalars(select(Category)).all()
    vendors = db.scalars(select(Vendor)).all()
    locations = db.scalars(select(Location)).all()
    return render(
        request,
        "assets.html",
        {"assets": assets, "employees": employees, "categories": categories, "vendors": vendors, "locations": locations},
        current_user,
    )

@router.post("/assets")
@require_roles("hardware_admin", "super_admin")
@require_permission("assets", "create")
async def create_asset(
    request: Request,
    name: str = Form(...),
    model: str = Form(...),
    serial_number: str = Form(...),
    category_id: int = Form(...),
    vendor_id: int = Form(...),
    location_id: int = Form(...),
    specification: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if db.scalar(select(Asset).where(Asset.serial_number == serial_number)):
        return redirect_with_flash("/assets", f"Serial number {serial_number} is already registered.", "error")
    asset = Asset(
        asset_code=generate_asset_code(db),
        name=name,
        model=model,
        serial_number=serial_number,
        category_id=category_id,
        vendor_id=vendor_id,
        location_id=location_id,
        specification=specification,
    )
    db.add(asset)
    db.flush()
    asset.qr_path = save_qr(asset)
    db.commit()
    log_event(db, "asset", "create", current_user.full_name, asset.asset_code, new_value=serial_number)
    return redirect_with_flash("/assets", f"Asset {asset.asset_code} created with QR label.")

@router.post("/assets/bulk-upload")
@require_roles("hardware_admin", "super_admin")
@require_permission("assets", "create")
async def bulk_upload_assets(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    content = (await file.read()).decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    errors = []
    for row in reader:
        if db.scalar(select(Asset).where(Asset.serial_number == row["serial_number"])):
            errors.append(f"Duplicate serial {row['serial_number']}")
            continue
        asset = Asset(
            asset_code=generate_asset_code(db),
            name=row["name"],
            model=row["model"],
            serial_number=row["serial_number"],
            category_id=int(row["category_id"]),
            vendor_id=int(row["vendor_id"]),
            location_id=int(row["location_id"]),
            specification=row.get("specification", ""),
        )
        db.add(asset)
        db.flush()
        asset.qr_path = save_qr(asset)
    db.commit()
    if errors:
        log_event(db, "asset", "bulk_upload_partial", current_user.full_name, file.filename, new_value="; ".join(errors))
        return redirect_with_flash("/assets", f"Bulk upload completed with issues: {'; '.join(errors[:3])}", "warning")
    return redirect_with_flash("/assets", "Bulk upload completed successfully.")

@router.get("/assets/{asset_id}/history", response_class=HTMLResponse)
@require_roles("super_admin", "hardware_admin", "hr_admin", "director", "auditor")
@require_permission("assets", "view")
async def asset_history_page(request: Request, asset_id: int, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404)
    return render(request, "asset_history.html", {"asset": asset, "events": build_asset_history(db, asset)}, current_user)

@router.get("/scanner", response_class=HTMLResponse)
@require_roles("hardware_admin", "auditor", "super_admin")
@require_permission("assets", "scan")
async def scanner_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    found_asset = None
    asset_code = request.query_params.get("asset_code")
    if asset_code:
        found_asset = db.scalar(select(Asset).where(Asset.asset_code == asset_code))
    recent_verifications = db.scalars(select(AuditVerification).order_by(AuditVerification.created_at.desc()).limit(10)).all()
    status_counts = {
        "matched": db.scalar(select(func.count(AuditVerification.id)).where(AuditVerification.result == "matched")) or 0,
        "mismatch": db.scalar(select(func.count(AuditVerification.id)).where(AuditVerification.result == "mismatch")) or 0,
        "missing": db.scalar(select(func.count(AuditVerification.id)).where(AuditVerification.result == "missing")) or 0,
    }
    return render(
        request,
        "scanner.html",
        {"found_asset": found_asset, "recent_verifications": recent_verifications, "status_counts": status_counts},
        current_user,
    )

@router.post("/scanner/verify")
@require_roles("hardware_admin", "auditor", "super_admin")
@require_permission("assets", "scan")
async def scanner_verify(
    request: Request,
    asset_code: str = Form(...),
    result: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    asset = db.scalar(select(Asset).where(Asset.asset_code == asset_code))
    if asset:
        db.add(AuditVerification(asset_id=asset.id, verified_by_id=current_user.id, result=result, notes=notes))
        db.commit()
        log_event(db, "scanner", "verify", current_user.full_name, asset_code, new_value=result)
    return redirect_with_flash(f"/scanner?asset_code={asset_code}", f"Verification recorded for {asset_code}: {result}.")
