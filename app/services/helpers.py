
POLICY_META = {
    "title": "CDIPD Asset Usage Policy",
    "version": "1.0",
    "owner": "hr_admin"
}

POLICY_TERMS = [
    "I understand that CDIPD assets remain the property of CDIPD/DUK and must be used only for official purposes.",
    "I agree to comply with the acceptable use guidelines.",
    "I understand that unauthorized software installation is prohibited.",
    "I acknowledge responsibility for the physical security of the asset.",
    "I understand that loss or damage may result in recovery of liability as per organizational policy."
]


import csv
import hashlib
import io
import os
import re
import secrets
from datetime import date, datetime, timedelta
from functools import wraps
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Optional

import qrcode
from pypdf import PdfReader
from itsdangerous import BadSignature
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from app.core.database import Base, SessionLocal, engine
from app.models.models import *
from app.core.config import *
from app.core.security import serializer, verify_password, pwd_context
from app.services.auth_adapter import get_auth_adapter
from app.services.email import send_notification_email


auth_adapter = get_auth_adapter()


templates = Jinja2Templates(directory=str(BASE_DIR / "app/templates"))

MODULE_PERMISSION_ACTIONS = ["view", "create", "edit", "delete", "approve", "reject", "export", "download", "print", "scan", "sign"]
PERMISSION_MODULES = [
    "users",
    "masters",
    "assets",
    "allocation",
    "approval",
    "policies",
    "signing",
    "tickets",
    "repairs",
    "replacement",
    "backup",
    "verification",
    "returns",
    "liability",
    "exit_clearance",
    "abroad_approval",
    "disposal",
    "procurement",
    "reports",
    "audit",
    "settings",
]
DEFAULT_ROLE_PERMISSIONS = {
    "super_admin": {(module, action) for module in PERMISSION_MODULES for action in MODULE_PERMISSION_ACTIONS},
    "hr_admin": {
        ("allocation", "view"),
        ("allocation", "approve"),
        ("allocation", "reject"),
        ("backup", "view"),
        ("backup", "create"),
        ("approval", "view"),
        ("approval", "approve"),
        ("approval", "reject"),
        ("reports", "view"),
        ("reports", "export"),
        ("returns", "view"),
        ("returns", "approve"),
        ("liability", "view"),
        ("liability", "approve"),
        ("exit_clearance", "view"),
        ("exit_clearance", "approve"),
        ("signing", "view"),
        ("policies", "view"),
        ("policies", "create"),
        ("policies", "edit"),
        ("policies", "approve"),
        ("verification", "view"),
        ("verification", "create"),
    },
    "hardware_admin": {
        ("assets", "view"),
        ("assets", "create"),
        ("assets", "edit"),
        ("assets", "scan"),
        ("allocation", "view"),
        ("allocation", "create"),
        ("backup", "view"),
        ("backup", "create"),
        ("tickets", "view"),
        ("tickets", "create"),
        ("tickets", "edit"),
        ("repairs", "view"),
        ("repairs", "create"),
        ("repairs", "edit"),
        ("replacement", "view"),
        ("replacement", "create"),
        ("verification", "view"),
        ("verification", "create"),
        ("reports", "view"),
        ("reports", "export"),
        ("audit", "view"),
        ("returns", "view"),
        ("returns", "create"),
        ("returns", "approve"),
        ("disposal", "view"),
        ("disposal", "create"),
        ("disposal", "approve"),
    },
    "employee": {
        ("assets", "view"),
        ("signing", "view"),
        ("signing", "sign"),
        ("tickets", "view"),
        ("tickets", "create"),
        ("returns", "view"),
        ("returns", "create"),
        ("abroad_approval", "view"),
        ("abroad_approval", "create"),
        ("reports", "download"),
    },
    "director": {
        ("abroad_approval", "view"),
        ("abroad_approval", "approve"),
        ("abroad_approval", "reject"),
        ("reports", "view"),
        ("reports", "export"),
        ("procurement", "view"),
        ("procurement", "create"),
        ("disposal", "view"),
        ("disposal", "approve"),
    },
    "auditor": {
        ("assets", "view"),
        ("audit", "view"),
        ("reports", "view"),
        ("reports", "export"),
        ("signing", "view"),
    },
}



def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def log_event(db: Session, module: str, action: str, actor_name: str, reference_id: str, old_value: str = "", new_value: str = ""):
    db.add(
        AuditLog(
            module=module,
            action=action,
            actor_name=actor_name,
            reference_id=reference_id,
            old_value=old_value,
            new_value=new_value,
        )
    )
    db.commit()


def get_current_user(request: Request, db: Session) -> Optional[User]:
    return auth_adapter.get_current_user(request, db)


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    return auth_adapter.authenticate(db, email.strip().lower(), password)


def get_user_roles(user: Optional[User]) -> list[str]:
    if not user:
        return []
    assigned = [user_role.role.code for user_role in user.user_roles if user_role.role and user_role.role.active]
    if user.role and user.role not in assigned:
        assigned.append(user.role)
    return assigned


def user_has_role(user: Optional[User], *roles: str) -> bool:
    if not user:
        return False
    current_roles = set(get_user_roles(user))
    return any(role in current_roles for role in roles)


def user_has_permission(user: Optional[User], module: str, action: str) -> bool:
    if not user:
        return False
    role_codes = set(get_user_roles(user))
    for user_role in user.user_roles:
        if user_role.role and user_role.role.active and user_role.role.code in role_codes:
            for role_permission in user_role.role.role_permissions:
                permission = role_permission.permission
                if role_permission.granted and permission and permission.module == module and permission.action == action:
                    return True
    if "super_admin" in role_codes:
        return True
    return False


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401)
    if not user.active:
        raise HTTPException(status_code=403, detail="Inactive user")
    return user


def require_roles(*roles: str):
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request: Request = kwargs["request"]
            db: Session = kwargs["db"]
            user = get_current_user(request, db)
            if not user:
                return RedirectResponse("/login", status_code=303)
            if not user.active:
                raise HTTPException(status_code=403, detail="Inactive user")
            if not user_has_role(user, *roles):
                raise HTTPException(status_code=403)
            return await func(*args, **kwargs)

        return wrapper

    return decorator


def require_permission(module: str, action: str):
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request: Request = kwargs["request"]
            db: Session = kwargs["db"]
            user = get_current_user(request, db)
            if not user:
                return RedirectResponse("/login", status_code=303)
            if not user.active:
                raise HTTPException(status_code=403, detail="Inactive user")
            if not user_has_permission(user, module, action):
                raise HTTPException(status_code=403, detail=f"Missing permission: {module}.{action}")
            return await func(*args, **kwargs)

        return wrapper

    return decorator


def render(request: Request, template: str, context: dict, user: Optional[User] = None):
    csrf_token = request.cookies.get(CSRF_COOKIE) or getattr(request.state, "csrf_token", None) or secrets.token_urlsafe(32)
    request.state.csrf_token = csrf_token
    flash = None
    raw_flash = request.cookies.get(FLASH_COOKIE)
    if raw_flash:
        try:
            flash = serializer.loads(raw_flash)
        except BadSignature:
            flash = None
    response = templates.TemplateResponse(
        request,
        template,
        {
            "request": request,
            "current_user": user,
            "menus": ROLE_MENUS.get(user.role, []) if user else [],
            "nav_items": build_nav_items(user),
            "sidebar_groups": build_sidebar_groups(user),
            "active_path": request.url.path,
            "role_switch_roles": get_user_roles(user),
            "unread_notifications": sum(1 for item in user.notifications if not item.is_read) if user else 0,
            "theme_settings_enabled": True,
            "csrf_token": csrf_token,
            "flash": flash,
            "policy_sample_available": POLICY_SAMPLE_FILE.exists(),
            "policy_sample_url": "/policy/current",
            "policy_reader_url": "/policy/reader",
            "brand": {
                "app_name": "CDIPD Asset Management System",
                "org_name": "Centre for Digital Innovation and Product Development",
                "cmmi_logo": "/static/cdipd-cmmi-logo.png",
                "duk_logo": "/static/img/duk_logo_white.png",
            },
            **context,
        },
    )
    if raw_flash:
        response.delete_cookie(FLASH_COOKIE)
    return response


def redirect_with_flash(url: str, message: str, level: str = "success"):
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(FLASH_COOKIE, serializer.dumps({"message": message, "level": level}), httponly=True, samesite="lax", secure=IS_PRODUCTION)
    return response


def generate_asset_code(db: Session) -> str:
    count = db.scalar(select(func.count(Asset.id))) or 0
    setting = db.get(NumberingSetting, 1)
    prefix = setting.asset_prefix if setting else "AST"
    return f"{prefix}-{count + 1:04d}"


def generate_document_number(db: Session) -> str:
    count = db.scalar(select(func.count(SignedDocument.id))) or 0
    setting = db.get(NumberingSetting, 1)
    prefix = setting.document_prefix if setting else "CDIPD-SIGN"
    return f"{prefix}-{count + 1:05d}"


def build_document_template_context(allocation: Allocation, document_number: str) -> dict[str, str]:
    return {
        "policy_title": allocation.policy.title if allocation.policy else "Asset Usage Policy",
        "policy_version": allocation.policy.version if allocation.policy else "N/A",
        "employee_name": allocation.employee.full_name,
        "employee_code": allocation.employee.employee_code,
        "department": allocation.employee.department or "",
        "asset_code": allocation.asset.asset_code,
        "serial_no": allocation.asset.serial_number,
        "document_hash": document_number,
    }


def build_document_subject(
    db: Session,
    *,
    allocation: Optional[Allocation] = None,
    asset_id: int | None = None,
    employee_id: int | None = None,
    policy_id: int | None = None,
) -> dict[str, object]:
    asset = allocation.asset if allocation else (db.get(Asset, asset_id) if asset_id else None)
    employee = allocation.employee if allocation else (db.get(User, employee_id) if employee_id else None)
    policy = allocation.policy if allocation else (db.get(PolicyVersion, policy_id) if policy_id else get_published_policy(db))
    requested_by = allocation.requested_by if allocation else None
    approved_by = allocation.approved_by if allocation else None
    declaration_text = (
        allocation.declaration_text
        if allocation
        else "This document was generated from a workflow event and linked to the applicable asset policy context."
    )
    return {
        "allocation": allocation,
        "asset": asset,
        "employee": employee,
        "policy": policy,
        "requested_by": requested_by,
        "approved_by": approved_by,
        "declaration_text": declaration_text,
    }


def build_document_template_context_from_subject(subject: dict[str, object], document_number: str) -> dict[str, str]:
    asset = subject.get("asset")
    employee = subject.get("employee")
    policy = subject.get("policy")
    return {
        "policy_title": getattr(policy, "title", "Asset Usage Policy"),
        "policy_version": getattr(policy, "version", "N/A"),
        "employee_name": getattr(employee, "full_name", "Unknown Employee"),
        "employee_code": getattr(employee, "employee_code", ""),
        "department": getattr(employee, "department", "") or "",
        "asset_code": getattr(asset, "asset_code", "N/A"),
        "serial_no": getattr(asset, "serial_number", "N/A"),
        "document_hash": document_number,
    }


def render_document_template(raw_html: str, context: dict[str, str]) -> str:
    def substitute(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return str(context.get(key, f"{{{{ {key} }}}}"))

    return re.sub(r"{{\s*([^{}]+)\s*}}", substitute, raw_html)


def resolve_document_template(db: Session, template_code: str = "TPL-ASSET-AGREEMENT") -> Optional[PolicyTemplate]:
    return db.scalar(select(PolicyTemplate).where(PolicyTemplate.template_code == template_code, PolicyTemplate.active == True))


def resolve_allocation_for_document(
    db: Session,
    asset_id: int | None = None,
    employee_id: int | None = None,
    allocation: Optional[Allocation] = None,
) -> Optional[Allocation]:
    if allocation:
        return allocation
    query = select(Allocation).order_by(Allocation.created_at.desc())
    if asset_id is not None:
        query = query.where(Allocation.asset_id == asset_id)
    if employee_id is not None:
        query = query.where(Allocation.employee_id == employee_id)
    return db.scalar(query)


def document_employee_name(document: SignedDocument) -> str:
    if document.employee:
        return document.employee.full_name
    if document.allocation and document.allocation.employee:
        return document.allocation.employee.full_name
    return "-"


def document_asset_code(document: SignedDocument) -> str:
    if document.asset:
        return document.asset.asset_code
    if document.allocation and document.allocation.asset:
        return document.allocation.asset.asset_code
    return "-"


def document_policy_version(document: SignedDocument) -> str:
    if document.policy:
        return document.policy.version
    if document.allocation and document.allocation.policy:
        return document.allocation.policy.version
    return "-"


NAV_ITEMS = {
    "super_admin": [
        {"path": "/dashboard", "label": "Dashboard"},
        {"path": "/users", "label": "Users"},
        {"path": "/masters", "label": "Masters"},
        {"path": "/policies", "label": "Policies"},
        {"path": "/management/reports", "label": "Reports"},
        {"path": "/audit", "label": "Audit Trail"},
    ],
    "hardware_admin": [
        {"path": "/dashboard", "label": "Dashboard"},
        {"path": "/assets", "label": "Inventory"},
        {"path": "/allocations", "label": "Allocations"},
        {"path": "/backup", "label": "Backup Assets"},
        {"path": "/maintenance", "label": "Maintenance"},
        {"path": "/repairs", "label": "Repairs"},
        {"path": "/replacement", "label": "Replacement"},
        {"path": "/verification", "label": "Verification"},
        {"path": "/returns", "label": "Returns"},
        {"path": "/disposal", "label": "Disposal"},
        {"path": "/scanner", "label": "QR Scanner"},
    ],
    "hr_admin": [
        {"path": "/dashboard", "label": "Dashboard"},
        {"path": "/allocations", "label": "Allocations"},
        {"path": "/backup", "label": "Backup Assets"},
        {"path": "/policies", "label": "Policies"},
        {"path": "/verification", "label": "Verification"},
        {"path": "/returns", "label": "Exit Clearance"},
        {"path": "/liabilities", "label": "Liabilities"},
        {"path": "/reports", "label": "Reports"},
    ],
    "employee": [
        {"path": "/dashboard", "label": "Dashboard"},
        {"path": "/dashboard", "label": "My Assets"},
        {"path": "/requests", "label": "Requests"},
        {"path": "/documents", "label": "Downloads"},
    ],
    "director": [
        {"path": "/dashboard", "label": "Dashboard"},
        {"path": "/travel", "label": "Travel Approvals"},
        {"path": "/procurement", "label": "Procurement"},
        {"path": "/management/reports", "label": "Analytics"},
    ],
    "auditor": [
        {"path": "/dashboard", "label": "Dashboard"},
        {"path": "/assets", "label": "Master Ledger"},
        {"path": "/documents", "label": "Document Repository"},
        {"path": "/audit", "label": "Audit Trail"},
    ],
}

SIDEBAR_GROUPS = {
    "super_admin": [
        {
            "label": "Core",
            "items": [
                {"path": "/dashboard", "label": "Dashboard", "icon": "📊"},
                {"path": "/assets", "label": "Inventory", "icon": "🗃️"},
                {"path": "/allocations", "label": "Allocations", "icon": "🧾"},
                {"path": "/backup", "label": "Backup Assets", "icon": "🛡️"},
                {"path": "/returns", "label": "Return Register", "icon": "🔄"},
                {"path": "/maintenance", "label": "Maintenance", "icon": "🛠️"},
            ],
        },
        {
            "label": "Governance",
            "items": [
                {"path": "/policies", "label": "Policies", "icon": "📜"},
                {"path": "/documents", "label": "Documents", "icon": "📝"},
                {"path": "/audit", "label": "Audit Trail", "icon": "🧾"},
                {"path": "/management/reports", "label": "Reports", "icon": "📈"},
                {"path": "/admin/roles", "label": "Roles", "icon": "🛡️"},
                {"path": "/admin/permissions", "label": "Permissions", "icon": "🔐"},
                {"path": "/admin/scheduled-jobs", "label": "Scheduled Jobs", "icon": "⏱️"},
                {"path": "/admin/deleted-records", "label": "Recovery", "icon": "♻️"},
                {"path": "/admin/email-templates", "label": "Email Templates", "icon": "✉️"},
                {"path": "/admin/notification-delivery", "label": "Delivery Log", "icon": "📨"},
                {"path": "/compliance/deviations", "label": "Deviations/CAPA", "icon": "⚠️"},
            ],
        },
    ],
    "hardware_admin": [
        {
            "label": "Hardware Operations",
            "items": [
                {"path": "/dashboard", "label": "Dashboard", "icon": "📊"},
                {"path": "/assets", "label": "Inventory", "icon": "🗃️"},
                {"path": "/allocations", "label": "Allocations", "icon": "🧾"},
                {"path": "/backup", "label": "Backup Assets", "icon": "🛡️"},
                {"path": "/returns", "label": "Return Register", "icon": "🔄"},
                {"path": "/maintenance", "label": "Maintenance", "icon": "🛠️"},
                {"path": "/repairs", "label": "Repairs", "icon": "🔧"},
                {"path": "/replacement", "label": "Replacement", "icon": "🔁"},
                {"path": "/verification", "label": "Verification", "icon": "✅"},
                {"path": "/scanner", "label": "QR Scanner", "icon": "🔍"},
            ],
        },
    ],
    "hr_admin": [
        {
            "label": "Workflows",
            "items": [
                {"path": "/dashboard", "label": "Dashboard", "icon": "📊"},
                {"path": "/allocations", "label": "Allocations", "icon": "🧾"},
                {"path": "/backup", "label": "Backup Assets", "icon": "🛡️"},
                {"path": "/returns", "label": "Exit Clearance", "icon": "📑"},
                {"path": "/verification", "label": "Verification", "icon": "✅"},
                {"path": "/liabilities", "label": "Liabilities", "icon": "💼"},
            ],
        },
        {
            "label": "Compliance",
            "items": [
                {"path": "/policies", "label": "Policies", "icon": "📜"},
                {"path": "/reports", "label": "Reports", "icon": "📈"},
            ],
        },
    ],
    "employee": [
        {
            "label": "My Workspace",
            "items": [
                {"path": "/dashboard", "label": "Dashboard", "icon": "📊"},
                {"path": "/requests", "label": "Requests", "icon": "📝"},
                {"path": "/documents", "label": "Downloads", "icon": "📁"},
            ],
        },
    ],
    "director": [
        {
            "label": "Approvals",
            "items": [
                {"path": "/dashboard", "label": "Dashboard", "icon": "📊"},
                {"path": "/travel", "label": "Travel Approvals", "icon": "🌍"},
                {"path": "/procurement", "label": "Procurement", "icon": "📈"},
                {"path": "/management/reports", "label": "Analytics", "icon": "📈"},
            ],
        },
    ],
    "auditor": [
        {
            "label": "Audit",
            "items": [
                {"path": "/dashboard", "label": "Dashboard", "icon": "📊"},
                {"path": "/assets", "label": "Master Ledger", "icon": "🗃️"},
                {"path": "/documents", "label": "Document Repository", "icon": "📝"},
                {"path": "/audit", "label": "Audit Trail", "icon": "🧾"},
            ],
        },
    ],
}

def build_sidebar_groups(user: Optional[User]) -> list[dict]:
    if not user:
        return []
    groups = list(SIDEBAR_GROUPS.get(user.role, []))
    if "super_admin" in get_user_roles(user) and user.role != "super_admin":
        groups = groups + list(SIDEBAR_GROUPS.get("super_admin", []))
    if user:
        support_group = {
            "label": "Support",
            "items": [
                {"path": "/notifications", "label": "Notifications", "icon": "🔔"},
            ],
        }
        existing_paths = {item["path"] for group in groups for item in group.get("items", [])}
        if "/notifications" not in existing_paths:
            groups.append(support_group)
    return groups


def build_nav_items(user: Optional[User]) -> list[dict]:
    if not user:
        return []
    current = list(NAV_ITEMS.get(user.role, []))
    roles = set(get_user_roles(user))
    if "super_admin" in roles:
        current.extend(
            [
                {"path": "/admin/roles", "label": "Roles"},
                {"path": "/admin/permissions", "label": "Permissions"},
                {"path": "/notifications", "label": "Notifications"},
                {"path": "/admin/scheduled-jobs", "label": "Scheduled Jobs"},
                {"path": "/admin/deleted-records", "label": "Recovery"},
                {"path": "/compliance/deviations", "label": "Deviations"},
            ]
        )
    elif user:
        current.append({"path": "/notifications", "label": "Notifications"})
    seen = set()
    deduped = []
    for item in current:
        if item["path"] in seen:
            continue
        deduped.append(item)
        seen.add(item["path"])
    return deduped


def sync_permissions_catalog(db: Session) -> None:
    existing = {
        (item.module, item.action): item
        for item in db.scalars(select(Permission)).all()
    }
    for module in PERMISSION_MODULES:
        for action in MODULE_PERMISSION_ACTIONS:
            key = (module, action)
            if key not in existing:
                db.add(Permission(module=module, action=action, label=f"{module.replace('_', ' ').title()} {action.title()}"))


def sync_role_permissions(db: Session) -> None:
    sync_permissions_catalog(db)
    db.flush()
    roles = {item.code: item for item in db.scalars(select(Role)).all()}
    permissions = {(item.module, item.action): item for item in db.scalars(select(Permission)).all()}
    for role_code, assignments in DEFAULT_ROLE_PERMISSIONS.items():
        role = roles.get(role_code)
        if not role:
            continue
        existing = {(item.permission.module, item.permission.action) for item in role.role_permissions if item.permission}
        for key in assignments:
            if key not in existing and key in permissions:
                db.add(RolePermission(role_id=role.id, permission_id=permissions[key].id, granted=True))


def create_notification(db: Session, user_id: int, event_code: str, title: str, message: str, link_url: str = "#") -> Notification:
    notification = Notification(
        user_id=user_id,
        event_code=event_code,
        title=title,
        message=message,
        link_url=link_url,
    )
    db.add(notification)
    db.flush()
    user = db.get(User, user_id)
    if user:
        delivered = send_notification_email(db, user, event_code, title, message, link_url, notification.id)
        notification.delivery_status = "email_sent" if delivered else "in_app_fallback"
    return notification


def get_published_policy(db: Session) -> Optional[PolicyVersion]:
    return db.scalar(select(PolicyVersion).where(PolicyVersion.published == True).order_by(PolicyVersion.created_at.desc()))


def get_numbering_setting(db: Session) -> NumberingSetting:
    setting = db.get(NumberingSetting, 1)
    if not setting:
        setting = NumberingSetting(id=1)
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting


def build_policy_status(policy: PolicyVersion) -> str:
    return "published" if policy.published else "draft"


def get_current_holder(asset: Asset, db: Session) -> str:
    allocation = db.scalar(
        select(Allocation)
        .where(Allocation.asset_id == asset.id, Allocation.status.in_(["pending_signature", "signed"]))
        .order_by(Allocation.updated_at.desc(), Allocation.created_at.desc())
    )
    return allocation.employee.full_name if allocation else "-"


def build_role_dashboard(db: Session, user: User) -> dict:
    base = {
        "dashboard_role": user.role,
        "cards": [],
        "primary_rows": [],
        "secondary_rows": [],
        "documents": [],
        "recent_logs": db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(6)).all(),
    }

    if user.role == "employee":
        allocations = db.scalars(select(Allocation).where(Allocation.employee_id == user.id).order_by(Allocation.created_at.desc())).all()
        tickets = db.scalars(select(MaintenanceTicket).where(MaintenanceTicket.raised_by_id == user.id).order_by(MaintenanceTicket.created_at.desc())).all()
        travel_requests = db.scalars(select(TravelRequest).where(TravelRequest.employee_id == user.id).order_by(TravelRequest.created_at.desc())).all()
        returns = db.scalars(select(ReturnRequest).where(ReturnRequest.employee_id == user.id).order_by(ReturnRequest.created_at.desc())).all()
        documents = db.scalars(select(SignedDocument).where(SignedDocument.employee_id == user.id).order_by(SignedDocument.created_at.desc())).all()
        base["cards"] = [
            {"label": "My Assets", "value": sum(1 for item in allocations if item.status == "signed"), "href": "/allocations"},
            {"label": "Pending Sign", "value": sum(1 for item in allocations if item.status == "pending_signature"), "href": "/allocations"},
            {"label": "Tickets", "value": sum(1 for item in tickets if item.status != "closed"), "href": "/maintenance"},
            {"label": "Travel", "value": len(travel_requests), "href": "/travel"},
            {"label": "Documents", "value": len(documents), "href": "/documents"},
        ]
        base["primary_rows"] = allocations[:6]
        base["secondary_rows"] = tickets[:6]
        base["documents"] = documents[:6]
        base["travel_requests"] = travel_requests[:6]
        base["return_requests"] = returns[:6]
        return base

    if user.role == "hardware_admin":
        assets = db.scalars(select(Asset).order_by(Asset.created_at.desc())).all()
        tickets = db.scalars(select(MaintenanceTicket).order_by(MaintenanceTicket.created_at.desc())).all()
        returns = db.scalars(select(ReturnRequest).order_by(ReturnRequest.created_at.desc())).all()
        verifications = db.scalars(select(AuditVerification).order_by(AuditVerification.created_at.desc())).all()
        disposals = db.scalars(select(DisposalRequest).order_by(DisposalRequest.created_at.desc())).all()
        backup_allocations = db.scalars(select(BackupAllocation).order_by(BackupAllocation.created_at.desc())).all()
        verification_campaigns = db.scalars(select(VerificationCampaign).order_by(VerificationCampaign.created_at.desc())).all()
        base["cards"] = [
            {"label": "Available", "value": sum(1 for asset in assets if asset.status == "available"), "href": "/reports/drilldown/assets_idle"},
            {"label": "Allocated", "value": sum(1 for asset in assets if asset.status == "active"), "href": "/reports/drilldown/assets_active"},
            {"label": "Under Repair", "value": sum(1 for asset in assets if asset.status == "under_maintenance"), "href": "/reports/drilldown/repairs"},
            {"label": "Open Tickets", "value": sum(1 for ticket in tickets if ticket.status != "closed"), "href": "/reports/drilldown/tickets_open"},
            {"label": "Pending Returns", "value": sum(1 for item in returns if item.exit_clearance_status != "completed"), "href": "/reports/drilldown/returns_verified"},
            {"label": "QR Mismatch", "value": sum(1 for item in verifications if item.result != "matched"), "href": "/reports/drilldown/qr_deviations"},
            {"label": "Disposal", "value": sum(1 for item in disposals if item.status != "closed"), "href": "/reports/drilldown/disposal_open"},
            {"label": "Backup Active", "value": sum(1 for item in backup_allocations if item.status == "active"), "href": "/backup"},
            {"label": "Verification", "value": sum(1 for item in verification_campaigns if item.status == "active"), "href": "/verification"},
        ]
        base["primary_rows"] = assets[:8]
        base["secondary_rows"] = tickets[:8]
        base["return_requests"] = returns[:6]
        return base

    if user.role == "hr_admin":
        allocations = db.scalars(select(Allocation).order_by(Allocation.created_at.desc())).all()
        returns = db.scalars(select(ReturnRequest).order_by(ReturnRequest.created_at.desc())).all()
        policies = db.scalars(select(PolicyVersion).order_by(PolicyVersion.created_at.desc())).all()
        liabilities = db.scalars(select(LiabilityRecord).order_by(LiabilityRecord.created_at.desc())).all()
        backup_allocations = db.scalars(select(BackupAllocation).order_by(BackupAllocation.created_at.desc())).all()
        base["cards"] = [
            {"label": "Pending Approval", "value": sum(1 for item in allocations if item.status in ["submitted", "sent_back"]), "href": "/allocations"},
            {"label": "Pending Sign", "value": sum(1 for item in allocations if item.status == "pending_signature"), "href": "/reports/drilldown/allocations_signed"},
            {"label": "Published Policy", "value": sum(1 for item in policies if item.published), "href": "/policies"},
            {"label": "Exit Queue", "value": sum(1 for item in returns if item.exit_clearance_status == "pending"), "href": "/returns"},
            {"label": "Liability Amount", "value": sum(item.amount for item in liabilities if item.recovery_status != "closed"), "href": "/reports/drilldown/liabilities_open"},
            {"label": "Backup Active", "value": sum(1 for item in backup_allocations if item.status == "active"), "href": "/backup"},
        ]
        base["primary_rows"] = [item for item in allocations if item.status in ["submitted", "sent_back"]][:8]
        base["secondary_rows"] = returns[:8]
        base["policies"] = policies[:6]
        return base

    if user.role == "director":
        travel_requests = db.scalars(select(TravelRequest).order_by(TravelRequest.created_at.desc())).all()
        assets = db.scalars(select(Asset)).all()
        tickets = db.scalars(select(MaintenanceTicket)).all()
        plans = db.scalars(select(ProcurementPlan).order_by(ProcurementPlan.created_at.desc())).all()
        disposals = db.scalars(select(DisposalRequest).order_by(DisposalRequest.created_at.desc())).all()
        backup_allocations = db.scalars(select(BackupAllocation).order_by(BackupAllocation.created_at.desc())).all()
        verification_campaigns = db.scalars(select(VerificationCampaign).order_by(VerificationCampaign.created_at.desc())).all()
        base["cards"] = [
            {"label": "Abroad Approvals", "value": sum(1 for item in travel_requests if item.status == "submitted"), "href": "/travel"},
            {"label": "Active Abroad", "value": sum(1 for item in travel_requests if item.status == "approved"), "href": "/reports/drilldown/travel_approved"},
            {"label": "High Risk Assets", "value": sum(1 for asset in assets if asset.status in ["under_maintenance", "pending_allocation"]), "href": "/management/reports"},
            {"label": "Open Repairs", "value": sum(1 for item in tickets if item.status != "closed"), "href": "/reports/drilldown/repairs"},
            {"label": "Backup Active", "value": sum(1 for item in backup_allocations if item.status == "active"), "href": "/reports/drilldown/backup_allocations"},
            {"label": "Verification", "value": sum(1 for item in verification_campaigns if item.status == "active"), "href": "/reports/drilldown/verification_campaigns"},
            {"label": "Procurement Plans", "value": len(plans), "href": "/reports/drilldown/procurement_plans"},
            {"label": "Disposal Queue", "value": sum(1 for item in disposals if item.status != "closed"), "href": "/reports/drilldown/disposal_open"},
        ]
        base["primary_rows"] = travel_requests[:8]
        base["secondary_rows"] = [asset for asset in assets if asset.status in ["available", "under_maintenance"]][:8]
        return base

    if user.role == "auditor":
        allocations = db.scalars(select(Allocation).order_by(Allocation.created_at.desc())).all()
        documents = db.scalars(select(SignedDocument).order_by(SignedDocument.created_at.desc())).all()
        verifications = db.scalars(select(AuditVerification).order_by(AuditVerification.created_at.desc())).all()
        verification_campaigns = db.scalars(select(VerificationCampaign).order_by(VerificationCampaign.created_at.desc())).all()
        logs = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(12)).all()
        base["cards"] = [
            {"label": "Unsigned", "value": sum(1 for item in allocations if item.status == "pending_signature"), "href": "/allocations"},
            {"label": "Signed Docs", "value": len(documents), "href": "/reports/drilldown/documents_signed"},
            {"label": "QR Mismatch", "value": sum(1 for item in verifications if item.result != "matched"), "href": "/reports/drilldown/qr_deviations"},
            {"label": "Campaigns", "value": len(verification_campaigns), "href": "/reports/drilldown/verification_campaigns"},
            {"label": "Audit Events", "value": db.scalar(select(func.count(AuditLog.id))) or 0, "href": "/reports/drilldown/audit_ledger"},
        ]
        base["primary_rows"] = documents[:8]
        base["secondary_rows"] = verifications[:8]
        base["recent_logs"] = logs
        return base

    users_count = db.scalar(select(func.count(User.id))) or 0
    assets_count = db.scalar(select(func.count(Asset.id))) or 0
    published_policies = db.scalar(select(func.count(PolicyVersion.id)).where(PolicyVersion.published == True)) or 0
    audit_count = db.scalar(select(func.count(AuditLog.id))) or 0
    failed_logins = sum(user.failed_login_attempts for user in db.scalars(select(User)).all())
    roles_count = db.scalar(select(func.count(Role.id))) or 0
    policies = db.scalars(select(PolicyVersion).order_by(PolicyVersion.created_at.desc())).all()
    base["cards"] = [
        {"label": "Users", "value": users_count, "href": "/users"},
        {"label": "Roles", "value": roles_count, "href": "/admin/roles"},
        {"label": "Assets", "value": assets_count, "href": "/reports/drilldown/assets_all"},
        {"label": "Policies", "value": published_policies, "href": "/policies"},
        {"label": "Audit Events", "value": audit_count, "href": "/reports/drilldown/audit_ledger"},
        {"label": "Failed Logins", "value": failed_logins, "href": "/users"},
    ]
    base["primary_rows"] = db.scalars(select(User).order_by(User.full_name)).all()[:8]
    base["secondary_rows"] = policies[:8]
    return base


def build_asset_history(db: Session, asset: Asset) -> list[dict]:
    events = [
        {"when": asset.created_at, "title": "Asset registered", "detail": f"{asset.asset_code} created in {asset.location.name} with status {asset.status}."}
    ]
    allocations = db.scalars(select(Allocation).where(Allocation.asset_id == asset.id).order_by(Allocation.created_at.asc())).all()
    for item in allocations:
        events.append({"when": item.created_at, "title": f"Allocation {item.status}", "detail": f"{item.employee.full_name} | requested by {item.requested_by.full_name}"})
    tickets = db.scalars(select(MaintenanceTicket).where(MaintenanceTicket.asset_id == asset.id).order_by(MaintenanceTicket.created_at.asc())).all()
    for ticket in tickets:
        events.append({"when": ticket.created_at, "title": f"Maintenance #{ticket.id}", "detail": f"{ticket.issue_type} | {ticket.status}"})
    returns = db.scalars(select(ReturnRequest).where(ReturnRequest.asset_id == asset.id).order_by(ReturnRequest.created_at.asc())).all()
    for item in returns:
        events.append({"when": item.created_at, "title": "Return workflow", "detail": f"{item.employee.full_name} | {item.exit_clearance_status}"})
    verifications = db.scalars(select(AuditVerification).where(AuditVerification.asset_id == asset.id).order_by(AuditVerification.created_at.asc())).all()
    for item in verifications:
        events.append({"when": item.created_at, "title": "QR verification", "detail": f"{item.result} | {item.verified_by.full_name}"})
    events.sort(key=lambda item: item["when"], reverse=True)
    return events


def build_request_history(db: Session, user: User) -> list[dict]:
    rows: list[dict] = []
    for item in db.scalars(select(Allocation).where(Allocation.employee_id == user.id).order_by(Allocation.created_at.desc())).all():
        rows.append(
            {
                "type": "Allocation",
                "reference": item.asset.asset_code,
                "status": item.status,
                "remarks": item.remarks or "-",
                "created_at": item.created_at,
                "link": "/allocations",
            }
        )
    for item in db.scalars(select(MaintenanceTicket).where(MaintenanceTicket.raised_by_id == user.id).order_by(MaintenanceTicket.created_at.desc())).all():
        rows.append(
            {
                "type": "Maintenance",
                "reference": f"#{item.id} - {item.asset.asset_code}",
                "status": item.status,
                "remarks": item.issue_type,
                "created_at": item.created_at,
                "link": "/maintenance",
            }
        )
    for item in db.scalars(select(TravelRequest).where(TravelRequest.employee_id == user.id).order_by(TravelRequest.created_at.desc())).all():
        rows.append(
            {
                "type": "Travel",
                "reference": f"{item.asset.asset_code} - {item.destination_country}",
                "status": item.status,
                "remarks": item.purpose,
                "created_at": item.created_at,
                "link": "/travel",
            }
        )
    for item in db.scalars(select(ReturnRequest).where(ReturnRequest.employee_id == user.id).order_by(ReturnRequest.created_at.desc())).all():
        rows.append(
            {
                "type": "Return",
                "reference": item.asset.asset_code,
                "status": item.exit_clearance_status,
                "remarks": item.reason,
                "created_at": item.created_at,
                "link": "/returns",
            }
        )
    rows.sort(key=lambda item: item["created_at"], reverse=True)
    return rows


def build_management_summary(db: Session) -> dict:
    assets = db.scalars(select(Asset)).all()
    tickets = db.scalars(select(MaintenanceTicket)).all()
    travel_requests = db.scalars(select(TravelRequest)).all()
    returns = db.scalars(select(ReturnRequest)).all()
    disposals = db.scalars(select(DisposalRequest).order_by(DisposalRequest.created_at.desc())).all()
    procurement = db.scalars(select(ProcurementPlan).order_by(ProcurementPlan.created_at.desc())).all()
    backup_allocations = db.scalars(select(BackupAllocation).order_by(BackupAllocation.created_at.desc())).all()
    verification_campaigns = db.scalars(select(VerificationCampaign).order_by(VerificationCampaign.created_at.desc())).all()
    return {
        "total_assets": len(assets),
        "utilization": sum(1 for asset in assets if asset.status == "active"),
        "idle_assets": [asset for asset in assets if asset.status == "available"],
        "repair_assets": [asset for asset in assets if asset.status == "under_maintenance"],
        "risk_assets": [asset for asset in assets if asset.status in ["under_maintenance", "pending_allocation", "liability_hold"]],
        "travel_requests": travel_requests,
        "returns": returns,
        "tickets": tickets,
        "disposals": [item for item in disposals if item.status != "closed"],
        "procurement": procurement,
        "backup_allocations": [item for item in backup_allocations if item.status == "active"],
        "verification_campaigns": [item for item in verification_campaigns if item.status == "active"],
    }


@lru_cache(maxsize=1)
def extract_policy_sections() -> list[dict]:
    if POLICY_SECTIONS:
        return POLICY_SECTIONS
    if not POLICY_SAMPLE_FILE.exists():
        return []
    reader = PdfReader(str(POLICY_SAMPLE_FILE))
    text_chunks = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        if page_text.strip():
            text_chunks.append(page_text)
    full_text = "\n".join(text_chunks)
    lines = [re.sub(r"\s+", " ", line).strip() for line in full_text.splitlines()]
    lines = [line for line in lines if line]
    sections: list[dict] = []
    current_title = "Overview"
    current_lines: list[str] = []
    heading_pattern = re.compile(r"^(\d+(\.\d+)*)[\)\.]?\s+.+")
    uppercase_pattern = re.compile(r"^[A-Z][A-Z0-9 ,/&\-\(\)]+$")
    for line in lines:
        if heading_pattern.match(line) or (uppercase_pattern.match(line) and len(line.split()) <= 10):
            if current_lines:
                sections.append({"title": current_title, "paragraphs": current_lines[:12]})
            current_title = line
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append({"title": current_title, "paragraphs": current_lines[:12]})
    return sections[:16]


def save_qr(asset: Asset) -> str:
    payload = f"asset:{asset.asset_code}"
    image = qrcode.make(payload)
    filename = f"{asset.asset_code}.png"
    path = QR_DIR / filename
    image.save(path)
    return f"qr/{filename}"


def generate_signed_pdf(
    subject: dict[str, object],
    document_number: str,
    otp_code: str,
    template_name: str = "",
    rendered_content: str = "",
) -> tuple[str, str]:
    filename = f"{document_number}.pdf"
    path = PDF_DIR / filename
    pdf = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    y = height - 56
    now_label = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    pdf.setFillColorRGB(0.08, 0.23, 0.32)
    pdf.roundRect(36, height - 110, width - 72, 56, 14, fill=1, stroke=0)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(52, height - 80, "CDIPD Employee Asset Agreement")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(52, height - 96, f"Document {document_number} | Generated {now_label}")

    employee = subject.get("employee")
    asset = subject.get("asset")
    policy = subject.get("policy")
    approved_by = subject.get("approved_by")
    declaration_text = str(subject.get("declaration_text") or "")

    y = height - 136
    pdf.setFillColorRGB(0.12, 0.2, 0.28)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(44, y, "Employee")
    pdf.drawString(300, y, "Issued Asset")
    y -= 16
    pdf.setFont("Helvetica", 10)
    pdf.drawString(44, y, f"{getattr(employee, 'full_name', 'Unknown Employee')} ({getattr(employee, 'employee_code', '-')})")
    pdf.drawString(300, y, f"{getattr(asset, 'asset_code', 'N/A')} - {getattr(asset, 'name', 'N/A')}")
    y -= 14
    pdf.drawString(44, y, getattr(employee, "email", "-"))
    pdf.drawString(300, y, f"{getattr(asset, 'model', 'N/A')} | {getattr(asset, 'serial_number', 'N/A')}")
    y -= 14
    pdf.drawString(44, y, f"Department: {getattr(employee, 'department', '-')}")
    pdf.drawString(300, y, f"Location: {getattr(getattr(asset, 'location', None), 'name', '-')}")

    y -= 28
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(44, y, "Policy and Declaration")
    y -= 16
    pdf.setFont("Helvetica", 10)
    policy_label = f"{getattr(policy, 'title', 'Policy')} | Version {getattr(policy, 'version', 'N/A')}"
    pdf.drawString(44, y, policy_label[:110])
    if template_name:
        y -= 14
        pdf.drawString(44, y, f"Template {template_name}"[:110])
    y -= 16
    for line in re.findall(r".{1,105}(?:\s|$)", declaration_text.strip()):
        if not line.strip():
            continue
        pdf.drawString(44, y, line.strip())
        y -= 14
    if rendered_content:
        y -= 8
        pdf.setFont("Helvetica-Oblique", 9)
        pdf.drawString(44, y, "Rendered template excerpt")
        y -= 14
        pdf.setFont("Helvetica", 8)
        excerpt = re.sub(r"<[^>]+>", " ", rendered_content)
        excerpt = re.sub(r"\s+", " ", excerpt).strip()
        for line in re.findall(r".{1,120}(?:\s|$)", excerpt[:480]):
            if not line.strip():
                continue
            pdf.drawString(44, y, line.strip())
            y -= 12

    y -= 10
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(44, y, "Terms")
    y -= 16
    pdf.setFont("Helvetica", 9)
    for idx, term in enumerate(POLICY_TERMS[:8], start=1):
        for line in re.findall(r".{1,112}(?:\s|$)", f"{idx}. {term}"):
            if not line.strip():
                continue
            pdf.drawString(44, y, line.strip())
            y -= 12
            if y < 120:
                pdf.showPage()
                y = height - 56
                pdf.setFont("Helvetica", 9)

    y -= 8
    pdf.setFillColorRGB(0.91, 0.96, 0.95)
    pdf.roundRect(36, max(44, y - 54), width - 72, 54, 12, fill=1, stroke=0)
    pdf.setFillColorRGB(0.12, 0.2, 0.28)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(48, max(60, y - 18), "Digital Evidence")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(48, max(48, y - 32), f"OTP verified in demo mode: {DEMO_OTP_MODE} | OTP: {otp_code}")
    pdf.drawString(48, max(36, y - 44), f"Approved by: {getattr(approved_by, 'full_name', 'Pending')}")
    pdf.save()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"pdf/{filename}", digest


def create_signed_document_record(
    db: Session,
    allocation: Allocation,
    otp_code: str,
    actor_name: str,
    template_code: str = "TPL-ASSET-AGREEMENT",
    evidence: str | None = None,
    source_module: str = "allocation",
    source_reference: str = "",
    context_overrides: Optional[dict[str, str]] = None,
) -> SignedDocument:
    document_number = generate_document_number(db)
    template = resolve_document_template(db, template_code)
    subject = build_document_subject(db, allocation=allocation)
    template_context = build_document_template_context_from_subject(subject, document_number)
    if context_overrides:
        template_context.update({key: str(value) for key, value in context_overrides.items()})
    rendered_content = render_document_template(template.html_content, template_context) if template else str(subject["declaration_text"])
    file_path, digest = generate_signed_pdf(
        subject,
        document_number,
        otp_code,
        template_name=template.template_name if template else "",
        rendered_content=rendered_content,
    )
    document = SignedDocument(
        allocation_id=allocation.id,
        employee_id=allocation.employee_id,
        asset_id=allocation.asset_id,
        policy_id=allocation.policy_id,
        template_id=template.id if template else None,
        template_code=template.template_code if template else "",
        template_name=template.template_name if template else "",
        document_number=document_number,
        file_path=file_path,
        hash_sha256=digest,
        evidence=evidence or f"OTP validated by {actor_name} in demo mode.",
        source_module=source_module,
        source_reference=source_reference,
        rendered_content=rendered_content,
    )
    db.add(document)
    db.flush()
    return document


def create_generic_document_record(
    db: Session,
    *,
    asset_id: int | None = None,
    employee_id: int | None = None,
    policy_id: int | None = None,
    allocation: Optional[Allocation] = None,
    template_code: str,
    actor_name: str,
    evidence: str,
    source_module: str,
    source_reference: str,
    otp_code: str = "SYSTEM",
    context_overrides: Optional[dict[str, str]] = None,
) -> SignedDocument:
    document_number = generate_document_number(db)
    template = resolve_document_template(db, template_code)
    subject = build_document_subject(
        db,
        allocation=allocation,
        asset_id=asset_id or (allocation.asset_id if allocation else None),
        employee_id=employee_id or (allocation.employee_id if allocation else None),
        policy_id=policy_id or (allocation.policy_id if allocation else None),
    )
    template_context = build_document_template_context_from_subject(subject, document_number)
    if context_overrides:
        template_context.update({key: str(value) for key, value in context_overrides.items()})
    rendered_content = render_document_template(template.html_content, template_context) if template else str(subject["declaration_text"])
    file_path, digest = generate_signed_pdf(
        subject,
        document_number,
        otp_code,
        template_name=template.template_name if template else "",
        rendered_content=rendered_content,
    )
    policy = subject.get("policy")
    document = SignedDocument(
        allocation_id=allocation.id if allocation else None,
        employee_id=getattr(subject.get("employee"), "id", employee_id),
        asset_id=getattr(subject.get("asset"), "id", asset_id),
        policy_id=getattr(policy, "id", policy_id),
        template_id=template.id if template else None,
        template_code=template.template_code if template else "",
        template_name=template.template_name if template else "",
        document_number=document_number,
        file_path=file_path,
        hash_sha256=digest,
        evidence=evidence,
        source_module=source_module,
        source_reference=source_reference,
        rendered_content=rendered_content,
    )
    db.add(document)
    db.flush()
    return document


def create_workflow_document_record(
    db: Session,
    *,
    template_code: str,
    actor_name: str,
    source_module: str,
    source_reference: str,
    evidence: str,
    asset_id: int | None = None,
    employee_id: int | None = None,
    allocation: Optional[Allocation] = None,
    context_overrides: Optional[dict[str, str]] = None,
) -> Optional[SignedDocument]:
    existing = db.scalar(
        select(SignedDocument).where(
            SignedDocument.source_module == source_module,
            SignedDocument.source_reference == source_reference,
        )
    )
    if existing:
        return existing
    resolved_allocation = resolve_allocation_for_document(db, asset_id=asset_id, employee_id=employee_id, allocation=allocation)
    return create_generic_document_record(
        db,
        asset_id=asset_id,
        employee_id=employee_id,
        allocation=resolved_allocation,
        template_code=template_code,
        actor_name=actor_name,
        evidence=evidence,
        source_module=source_module,
        source_reference=source_reference,
        otp_code=source_reference or "SYSTEM",
        context_overrides=context_overrides,
    )


def ensure_signed_document_templates(db: Session) -> None:
    template = resolve_document_template(db)
    changed = False
    for item in db.scalars(select(SignedDocument).order_by(SignedDocument.created_at.asc())).all():
        if item.template_code and item.template_name and item.rendered_content:
            if item.source_module and item.source_reference and item.employee_id and item.asset_id:
                continue
        subject = build_document_subject(
            db,
            allocation=item.allocation,
            asset_id=item.asset_id,
            employee_id=item.employee_id,
            policy_id=item.policy_id,
        )
        item.employee_id = item.employee_id or getattr(subject.get("employee"), "id", None)
        item.asset_id = item.asset_id or getattr(subject.get("asset"), "id", None)
        item.policy_id = item.policy_id or getattr(subject.get("policy"), "id", None)
        template_context = build_document_template_context_from_subject(subject, item.document_number)
        item.template_id = item.template_id or (template.id if template else None)
        item.template_code = item.template_code or (template.template_code if template else "LEGACY-DECLARATION")
        item.template_name = item.template_name or (template.template_name if template else "Legacy Declaration")
        item.rendered_content = item.rendered_content or (
            render_document_template(template.html_content, template_context) if template else str(subject["declaration_text"])
        )
        item.source_module = item.source_module or "allocation"
        item.source_reference = item.source_reference or f"allocation:{item.allocation_id or item.asset_id or item.id}"
        changed = True
    if changed:
        db.commit()


def ensure_workflow_documents(db: Session) -> None:
    approved_travels = db.scalars(select(TravelRequest).where(TravelRequest.status == "approved")).all()
    for item in approved_travels:
        create_workflow_document_record(
            db,
            template_code="TPL-ABROAD-DECL",
            actor_name=item.employee.full_name,
            source_module="travel",
            source_reference=f"travel:{item.id}",
            evidence=f"Travel approval generated for {item.destination_country}.",
            asset_id=item.asset_id,
            employee_id=item.employee_id,
            context_overrides={
                "destination_country": item.destination_country,
                "purpose": item.purpose,
                "departure_date": item.departure_date.isoformat(),
                "return_date": item.return_date.isoformat(),
            },
        )

    verified_returns = db.scalars(select(ReturnRequest).where(ReturnRequest.status == "verified")).all()
    for item in verified_returns:
        allocation = resolve_allocation_for_document(db, asset_id=item.asset_id, employee_id=item.employee_id)
        create_workflow_document_record(
            db,
            template_code="TPL-RETURN-DECL",
            actor_name=item.employee.full_name,
            source_module="return",
            source_reference=f"return:{item.id}",
            evidence="Return verification document backfilled.",
            asset_id=item.asset_id,
            employee_id=item.employee_id,
            allocation=allocation,
            context_overrides={
                "condition_notes": item.condition_notes,
                "exit_clearance_status": item.exit_clearance_status,
                "liability_amount": str(item.liability_amount),
            },
        )
        if item.exit_clearance_status == "completed" and item.liability_amount == 0:
            create_workflow_document_record(
                db,
                template_code="TPL-NO-DUES",
                actor_name=item.employee.full_name,
                source_module="exit_clearance",
                source_reference=f"return:{item.id}:no-dues",
                evidence="No-dues clearance document backfilled.",
                asset_id=item.asset_id,
                employee_id=item.employee_id,
                allocation=allocation,
                context_overrides={
                    "condition_notes": item.condition_notes,
                    "exit_clearance_status": item.exit_clearance_status,
                },
            )

    closed_disposals = db.scalars(select(DisposalRequest).where(DisposalRequest.status == "closed")).all()
    for item in closed_disposals:
        create_workflow_document_record(
            db,
            template_code="TPL-DISPOSAL-CERT",
            actor_name=item.requested_by.full_name,
            source_module="disposal",
            source_reference=f"disposal:{item.id}",
            evidence=f"Disposal certificate {item.certificate_number or '-'} backfilled.",
            asset_id=item.asset_id,
            context_overrides={
                "certificate_number": item.certificate_number or "-",
                "disposal_method": item.disposal_method or "-",
                "vendor_agency": item.vendor_agency or "-",
                "condition_notes": item.condition,
            },
        )
    db.commit()






def dashboard_cards(db: Session, user: User) -> list[dict]:
    total_assets = db.scalar(select(func.count(Asset.id))) or 0
    open_tickets = db.scalar(select(func.count(MaintenanceTicket.id)).where(MaintenanceTicket.status != "closed")) or 0
    pending_allocations = db.scalar(select(func.count(Allocation.id)).where(Allocation.status.in_(["submitted", "sent_back", "pending_signature"]))) or 0
    pending_travel = db.scalar(select(func.count(TravelRequest.id)).where(TravelRequest.status == "submitted")) or 0
    return [
        {"label": "Total Assets", "value": total_assets},
        {"label": "Open Tickets", "value": open_tickets},
        {"label": "Pending Allocations", "value": pending_allocations},
        {"label": "Travel Requests", "value": pending_travel},
    ]


def build_otp_state(db: Session, allocations: list[Allocation]) -> dict[int, dict]:
    state = {}
    for item in allocations:
        challenge = db.scalar(
            select(OTPChallenge)
            .where(OTPChallenge.allocation_id == item.id, OTPChallenge.consumed == False)
            .order_by(OTPChallenge.created_at.desc())
        )
        if challenge and challenge.expires_at >= datetime.utcnow():
            resend_at = challenge.created_at + timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS)
            state[item.id] = {
                "expires_at": challenge.expires_at.strftime("%Y-%m-%d %H:%M UTC"),
                "otp_code": challenge.otp_code if DEMO_OTP_MODE else None,
                "resend_available_at": resend_at.isoformat(),
                "cooldown_remaining": max(0, int((resend_at - datetime.utcnow()).total_seconds())),
            }
    return state


def parse_change_payload(value: str) -> dict:
    if not value:
        return {}
    parsed = {}
    for part in value.split(";"):
        if "=" in part:
            key, raw = part.split("=", 1)
            parsed[key.strip()] = raw.strip()
    return parsed
