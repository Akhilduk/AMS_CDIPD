from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.models import AuditLog, Category, Location, NumberingSetting, Permission, Role, RolePermission, SignedDocument, User, UserRole, Vendor, WorkflowSetting
from app.services.helpers import MODULE_PERMISSION_ACTIONS, PERMISSION_MODULES, get_current_user, get_db, get_numbering_setting, hash_password, log_event, redirect_with_flash, render, require_permission, require_roles, sync_role_permissions

router = APIRouter()

@router.get("/users", response_class=HTMLResponse)
@require_roles("super_admin")
@require_permission("users", "view")
async def users_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    users = db.scalars(select(User).order_by(User.full_name)).all()
    roles = db.scalars(select(Role).where(Role.active == True).order_by(Role.name)).all()
    return render(request, "users.html", {"users": users, "roles": roles, "user_role_map": {user.id: [item.role.code for item in user.user_roles if item.role] for user in users}}, current_user)

@router.post("/users")
@require_roles("super_admin")
@require_permission("users", "create")
async def create_user(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    employee_code: str = Form(...),
    department: str = Form(...),
    designation: str = Form(...),
    mobile: str = Form(""),
    reporting_manager: str = Form(""),
    employee_type: str = Form(...),
    role: str = Form(...),
    joining_date: Optional[str] = Form(None),
    active: Optional[str] = Form(None),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if db.scalar(select(User).where(User.email == email)):
        return redirect_with_flash("/users", f"The email {email} already exists.", "error")
    if db.scalar(select(User).where(User.employee_code == employee_code)):
        return redirect_with_flash("/users", f"The employee code {employee_code} already exists.", "error")
    selected_role = db.scalar(select(Role).where(Role.code == role, Role.active == True))
    if not selected_role:
        return redirect_with_flash("/users", f"The role {role} is not active.", "error")
    parsed_joining_date = None
    if joining_date:
        parsed_joining_date = datetime.strptime(joining_date, "%Y-%m-%d").date()
    user = User(
        full_name=full_name,
        email=email.strip().lower(),
        employee_code=employee_code.strip().upper(),
        department=department.strip(),
        designation=designation.strip(),
        mobile=mobile.strip(),
        reporting_manager=reporting_manager.strip(),
        employee_type=employee_type.strip(),
        role=role,
        joining_date=parsed_joining_date,
        active=bool(active),
        password_hash=hash_password(password),
        multiple_roles_enabled=False,
    )
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.id, role_id=selected_role.id, is_primary=True))
    db.commit()
    log_event(db, "users", "create", current_user.full_name, email, new_value=role)
    return redirect_with_flash("/users", f"User {full_name} created successfully.")


@router.post("/users/{user_id}/toggle-active")
@require_roles("super_admin")
@require_permission("users", "edit")
async def toggle_user_active(user_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404)
    user.active = not user.active
    db.commit()
    action = "activate" if user.active else "deactivate"
    log_event(db, "users", action, current_user.full_name, user.email, new_value=f"active={user.active}")
    return redirect_with_flash("/users", f"User {user.full_name} {'activated' if user.active else 'deactivated'}.")


@router.post("/users/{user_id}/reset-password")
@require_roles("super_admin")
@require_permission("users", "edit")
async def admin_reset_password(user_id: int, request: Request, password: str = Form(...), db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404)
    if len(password) < 8:
        return redirect_with_flash("/users", "Password must be at least 8 characters.", "error")
    user.password_hash = hash_password(password)
    user.failed_login_attempts = 0
    db.commit()
    log_event(db, "users", "password_reset", current_user.full_name, user.email, new_value="admin_reset")
    return redirect_with_flash("/users", f"Password reset for {user.full_name}.")


@router.post("/users/{user_id}/assign-roles")
@require_roles("super_admin")
@require_permission("users", "edit")
async def assign_user_roles(user_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404)
    form = await request.form()
    selected_role_codes = form.getlist("roles")
    primary_role = form.get("primary_role")
    if not selected_role_codes:
        return redirect_with_flash("/users", "Select at least one role.", "error")
    if primary_role not in selected_role_codes:
        return redirect_with_flash("/users", "Primary role must be part of the assigned role set.", "error")
    selected_roles = db.scalars(select(Role).where(Role.code.in_(selected_role_codes), Role.active == True)).all()
    if len(selected_roles) != len(set(selected_role_codes)):
        return redirect_with_flash("/users", "One or more selected roles are invalid.", "error")
    for existing in list(user.user_roles):
        db.delete(existing)
    db.flush()
    for role in selected_roles:
        db.add(UserRole(user_id=user.id, role_id=role.id, is_primary=role.code == primary_role))
    user.role = primary_role
    user.multiple_roles_enabled = len(selected_roles) > 1
    db.commit()
    log_event(db, "users", "role_assigned", current_user.full_name, user.email, new_value=",".join(sorted(selected_role_codes)))
    return redirect_with_flash("/users", f"Roles updated for {user.full_name}.")


@router.get("/admin/roles", response_class=HTMLResponse)
@require_roles("super_admin")
@require_permission("users", "view")
async def roles_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    roles = db.scalars(select(Role).order_by(Role.name)).all()
    stats = {}
    for role in roles:
        stats[role.code] = {
            "users": db.scalar(select(func.count(UserRole.id)).where(UserRole.role_id == role.id)) or 0,
            "permissions": db.scalar(select(func.count(RolePermission.id)).where(RolePermission.role_id == role.id, RolePermission.granted == True)) or 0,
        }
    return render(request, "roles.html", {"roles": roles, "stats": stats}, current_user)


@router.post("/admin/roles")
@require_roles("super_admin")
@require_permission("users", "create")
async def create_role(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    normalized_code = code.strip().lower()
    if db.scalar(select(Role).where(Role.code == normalized_code)):
        return redirect_with_flash("/admin/roles", f"Role {normalized_code} already exists.", "error")
    db.add(Role(code=normalized_code, name=name.strip(), description=description.strip(), active=True))
    db.commit()
    log_event(db, "roles", "create", current_user.full_name, normalized_code, new_value=name.strip())
    return redirect_with_flash("/admin/roles", f"Role {name.strip()} created.")


@router.get("/admin/permissions", response_class=HTMLResponse)
@require_roles("super_admin")
@require_permission("settings", "view")
async def permissions_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    sync_role_permissions(db)
    db.commit()
    roles = db.scalars(select(Role).where(Role.active == True).order_by(Role.name)).all()
    permissions = db.scalars(select(Permission).order_by(Permission.module, Permission.action)).all()
    matrix = {(item.role_id, item.permission_id): item.granted for item in db.scalars(select(RolePermission)).all()}
    return render(
        request,
        "permissions.html",
        {"roles": roles, "permissions": permissions, "matrix": matrix, "permission_modules": PERMISSION_MODULES, "permission_actions": MODULE_PERMISSION_ACTIONS},
        current_user,
    )


@router.post("/admin/permissions")
@require_roles("super_admin")
@require_permission("settings", "edit")
async def update_permissions(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    roles = db.scalars(select(Role).where(Role.active == True)).all()
    permissions = db.scalars(select(Permission)).all()
    existing = {(item.role_id, item.permission_id): item for item in db.scalars(select(RolePermission)).all()}
    form = await request.form()
    for role in roles:
        for permission in permissions:
            field_name = f"perm_{role.id}_{permission.id}"
            granted = field_name in form
            key = (role.id, permission.id)
            role_permission = existing.get(key)
            if role_permission:
                role_permission.granted = granted
            elif granted:
                db.add(RolePermission(role_id=role.id, permission_id=permission.id, granted=True))
    db.commit()
    log_event(db, "permissions", "update", current_user.full_name, "matrix", new_value="permission_matrix_saved")
    return redirect_with_flash("/admin/permissions", "Permission matrix updated.")

@router.get("/masters", response_class=HTMLResponse)
@require_roles("super_admin")
@require_permission("masters", "view")
async def masters_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    return render(
        request,
        "masters.html",
        {
            "categories": db.scalars(select(Category)).all(),
            "vendors": db.scalars(select(Vendor)).all(),
            "locations": db.scalars(select(Location)).all(),
        },
        current_user,
    )

@router.post("/masters/{master_type}")
@require_roles("super_admin")
@require_permission("masters", "create")
async def create_master(
    request: Request,
    master_type: str,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if db.scalar(select(model_map := {"categories": Category, "vendors": Vendor, "locations": Location}[master_type]).where(model_map.name == name)):
        return redirect_with_flash("/masters", f"{name} already exists in {master_type}.", "error")
    model = model_map
    db.add(model(name=name))
    db.commit()
    log_event(db, "masters", "create", current_user.full_name, master_type, new_value=name)
    return redirect_with_flash("/masters", f"{name} added to {master_type}.")

@router.get("/workflows", response_class=HTMLResponse)
@require_roles("super_admin")
@require_permission("settings", "view")
async def workflows_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    workflows = db.scalars(select(WorkflowSetting).order_by(WorkflowSetting.category_name)).all()
    return render(request, "workflows.html", {"workflows": workflows}, current_user)

@router.post("/workflows/{workflow_id}")
@require_roles("super_admin")
@require_permission("settings", "edit")
async def update_workflow(
    request: Request,
    workflow_id: int,
    hr_approval_required: Optional[str] = Form(None),
    signing_required: Optional[str] = Form(None),
    director_approval_required: Optional[str] = Form(None),
    return_check_required: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    workflow = db.get(WorkflowSetting, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404)
    workflow.hr_approval_required = bool(hr_approval_required)
    workflow.signing_required = bool(signing_required)
    workflow.director_approval_required = bool(director_approval_required)
    workflow.return_check_required = bool(return_check_required)
    workflow.updated_at = datetime.utcnow()
    db.commit()
    log_event(db, "workflow", "update", current_user.full_name, workflow.category_name, new_value="rules_updated")
    return redirect_with_flash("/workflows", f"Workflow updated for {workflow.category_name}.")

@router.get("/numbering", response_class=HTMLResponse)
@require_roles("super_admin")
@require_permission("settings", "view")
async def numbering_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    return render(request, "numbering.html", {"setting": get_numbering_setting(db)}, current_user)

@router.post("/numbering")
@require_roles("super_admin")
@require_permission("settings", "edit")
async def update_numbering(
    request: Request,
    asset_prefix: str = Form(...),
    document_prefix: str = Form(...),
    ticket_prefix: str = Form(...),
    return_prefix: str = Form(...),
    policy_prefix: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    setting = get_numbering_setting(db)
    setting.asset_prefix = asset_prefix.strip().upper()
    setting.document_prefix = document_prefix.strip().upper()
    setting.ticket_prefix = ticket_prefix.strip().upper()
    setting.return_prefix = return_prefix.strip().upper()
    setting.policy_prefix = policy_prefix.strip().upper()
    setting.updated_at = datetime.utcnow()
    db.commit()
    log_event(db, "numbering", "update", current_user.full_name, "settings", new_value=document_prefix)
    return redirect_with_flash("/numbering", "Numbering settings updated.")

@router.get("/audit", response_class=HTMLResponse)
@require_roles("super_admin", "auditor", "hardware_admin")
@require_permission("audit", "view")
async def audit_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    logs = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(250)).all()
    documents = db.scalars(select(SignedDocument).order_by(SignedDocument.created_at.desc())).all()
    return render(request, "audit.html", {"logs": logs, "documents": documents}, current_user)
