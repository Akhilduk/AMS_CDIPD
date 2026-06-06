from typing import Optional
from datetime import date, datetime
from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.models import User, Category, Vendor, Location, PolicyVersion, Asset, Allocation, OTPChallenge, SignedDocument, MaintenanceTicket, TravelRequest, ReturnRequest, AuditVerification, AuditLog, WorkflowSetting, NumberingSetting, ROLE_MENUS
from app.services.helpers import get_db, render, verify_password, serializer, require_user, require_roles, redirect_with_flash, log_event, hash_password, get_current_user, generate_asset_code, generate_document_number, build_role_dashboard, build_request_history, build_asset_history, extract_policy_sections, build_management_summary, save_qr, get_current_holder, build_policy_status, get_numbering_setting, get_published_policy, generate_signed_pdf, create_signed_document_record, build_otp_state, parse_change_payload, DEMO_OTP_MODE
import io
import csv

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return render(
        request,
        "home.html",
        {
            "hero_stats": [
                {"value": "Assets", "label": "Inventory"},
                {"value": "Policy", "label": "Signing"},
                {"value": "Audit", "label": "Tracking"},
            ],
            "banner_url": "/static/banner.jpeg",
        },
    )

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    dashboard_context = build_role_dashboard(db, current_user)
    return render(
        request,
        "dashboard.html",
        dashboard_context,
        current_user,
    )