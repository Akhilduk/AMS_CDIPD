import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Allocation, PolicyMaster, PolicyTemplate, PolicyVersion
from app.services.helpers import (
    POLICY_META,
    POLICY_TERMS,
    extract_policy_sections,
    get_current_user,
    get_db,
    get_published_policy,
    log_event,
    redirect_with_flash,
    render,
    require_permission,
    require_roles,
    user_has_role,
)

router = APIRouter()
POLICY_SAMPLE_FILE = Path(__file__).parent.parent / "storage" / "policy" / "sample_policy.pdf"


def ensure_policy_catalog(db: Session) -> None:
    master = db.scalar(select(PolicyMaster).where(PolicyMaster.policy_code == "POL-ASSET-USAGE"))
    if not master:
        master = PolicyMaster(
            policy_code="POL-ASSET-USAGE",
            title="Asset Usage Policy",
            description="Core asset usage, acknowledgement, and compliance policy.",
            owner_role="hr_admin",
            active=True,
        )
        db.add(master)
        db.flush()

    template_specs = [
        ("TPL-ASSET-USAGE", "Asset Usage Policy", "policy", "<h1>{{policy_title}}</h1><p>{{employee_name}} acknowledges the asset usage policy version {{policy_version}}.</p>"),
        ("TPL-ASSET-AGREEMENT", "CDIPD Asset Agreement", "agreement", "<h1>Asset Agreement</h1><p>Employee {{employee_name}} accepts asset {{asset_code}} / {{serial_no}}.</p>"),
        ("TPL-RETURN-DECL", "Return Declaration", "return", "<h1>Return Declaration</h1><p>{{employee_name}} returns asset {{asset_code}} from {{department}}.</p>"),
        ("TPL-ABROAD-DECL", "Abroad Asset Declaration", "abroad", "<h1>Abroad Declaration</h1><p>{{employee_name}} carries {{asset_code}} abroad under approval.</p>"),
        ("TPL-NO-DUES", "No-Dues Certificate", "exit_clearance", "<h1>No-Dues Certificate</h1><p>{{employee_name}} has completed exit clearance.</p>"),
        ("TPL-DISPOSAL-CERT", "Disposal Certificate", "disposal", "<h1>Disposal Certificate</h1><p>Asset {{asset_code}} disposed with certificate {{document_hash}}.</p>"),
    ]
    for template_code, template_name, template_type, html_content in template_specs:
        template = db.scalar(select(PolicyTemplate).where(PolicyTemplate.template_code == template_code))
        if not template:
            db.add(
                PolicyTemplate(
                    template_code=template_code,
                    template_name=template_name,
                    template_type=template_type,
                    html_content=html_content,
                    active=True,
                )
            )

    for item in db.scalars(select(PolicyVersion)).all():
        if not item.policy_master_id:
            item.policy_master_id = master.id
        if not item.status:
            item.status = "published" if item.published else "draft"
        if item.published and item.status != "published":
            item.status = "published"
        if item.published and not item.effective_date:
            item.effective_date = date.today()
    db.commit()


def render_policy_template_html(raw_html: str, context: dict[str, str]) -> str:
    def substitute(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return str(context.get(key, f"{{{{ {key} }}}}"))

    return re.sub(r"{{\s*([^{}]+)\s*}}", substitute, raw_html)


def build_template_preview_context() -> dict[str, str]:
    return {
        "policy_title": "Asset Usage Policy",
        "policy_version": "v1.0",
        "employee_name": "Anjana Employee",
        "employee_code": "EMP-004",
        "department": "Product",
        "asset_code": "AST-0002",
        "serial_no": "DL-548291",
        "document_hash": "d0c9f46e7b7f-preview",
    }


@router.get("/policies", response_class=HTMLResponse)
@require_roles("super_admin", "hr_admin")
@require_permission("policies", "view")
async def policies_page(request: Request, db: Session = Depends(get_db)):
    ensure_policy_catalog(db)
    current_user = get_current_user(request, db)
    masters = db.scalars(select(PolicyMaster).order_by(PolicyMaster.created_at.desc())).all()
    versions = db.scalars(select(PolicyVersion).order_by(PolicyVersion.created_at.desc())).all()
    templates = db.scalars(select(PolicyTemplate).order_by(PolicyTemplate.template_name)).all()
    return render(
        request,
        "policies.html",
        {
            "masters": masters,
            "versions": versions,
            "templates": templates,
            "active_policy": get_published_policy(db),
        },
        current_user,
    )


@router.post("/policies/masters")
@require_roles("super_admin", "hr_admin")
@require_permission("policies", "create")
async def create_policy_master(
    request: Request,
    policy_code: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    owner_role: str = Form(...),
    active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    normalized_code = policy_code.strip().upper()
    if db.scalar(select(PolicyMaster).where(PolicyMaster.policy_code == normalized_code)):
        return redirect_with_flash("/policies", f"Policy code {normalized_code} already exists.", "error")
    master = PolicyMaster(
        policy_code=normalized_code,
        title=title.strip(),
        description=description.strip(),
        owner_role=owner_role.strip(),
        active=bool(active),
    )
    db.add(master)
    db.commit()
    log_event(db, "policy_master", "create", current_user.full_name, normalized_code, new_value=title.strip())
    return redirect_with_flash("/policies", f"Policy master {normalized_code} created.")


@router.post("/policies/versions")
@require_roles("super_admin", "hr_admin")
@require_permission("policies", "create")
async def create_policy_version(
    request: Request,
    policy_master_id: int = Form(...),
    title: str = Form(...),
    version: str = Form(...),
    body: str = Form(...),
    effective_date: date | None = Form(None),
    approval_remarks: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    item = PolicyVersion(
        policy_master_id=policy_master_id,
        title=title.strip(),
        version=version.strip(),
        body=body,
        status="draft",
        effective_date=effective_date,
        approval_remarks=approval_remarks.strip(),
        published=False,
    )
    db.add(item)
    db.commit()
    log_event(db, "policy_version", "create", current_user.full_name, item.version, new_value=item.title)
    return redirect_with_flash("/policies", f"Policy version {item.version} created as draft.")


@router.post("/policies/templates")
@require_roles("super_admin", "hr_admin")
@require_permission("policies", "create")
async def create_policy_template(
    request: Request,
    template_code: str = Form(...),
    template_name: str = Form(...),
    template_type: str = Form(...),
    html_content: str = Form(...),
    active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    normalized_code = template_code.strip().upper()
    if db.scalar(select(PolicyTemplate).where(PolicyTemplate.template_code == normalized_code)):
        return redirect_with_flash("/policies", f"Template code {normalized_code} already exists.", "error")
    item = PolicyTemplate(
        template_code=normalized_code,
        template_name=template_name.strip(),
        template_type=template_type.strip(),
        html_content=html_content.strip(),
        active=bool(active),
    )
    db.add(item)
    db.commit()
    log_event(db, "policy_template", "create", current_user.full_name, normalized_code, new_value=item.template_name)
    return redirect_with_flash("/policies", f"Template {normalized_code} created.")


@router.post("/policies/templates/{template_id}/toggle")
@require_roles("super_admin", "hr_admin")
@require_permission("policies", "update")
async def toggle_policy_template(template_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    item = db.get(PolicyTemplate, template_id)
    if not item:
        raise HTTPException(status_code=404)
    item.active = not item.active
    db.commit()
    log_event(
        db,
        "policy_template",
        "toggle",
        current_user.full_name,
        item.template_code,
        new_value="active" if item.active else "inactive",
    )
    return redirect_with_flash("/policies", f"Template {item.template_code} marked {'active' if item.active else 'inactive'}.")


@router.post("/policies/{policy_id}/submit-review")
@require_roles("super_admin", "hr_admin")
@require_permission("policies", "edit")
async def submit_policy_for_review(policy_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    item = db.get(PolicyVersion, policy_id)
    if not item:
        raise HTTPException(status_code=404)
    item.status = "under_review"
    db.commit()
    log_event(db, "policy_version", "submit_review", current_user.full_name, item.version, new_value=item.title)
    return redirect_with_flash("/policies", f"Policy version {item.version} submitted for review.")


@router.post("/policies/{policy_id}/approve")
@require_roles("super_admin", "hr_admin")
@require_permission("policies", "approve")
async def approve_policy_version(policy_id: int, request: Request, remarks: str = Form(""), db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    item = db.get(PolicyVersion, policy_id)
    if not item:
        raise HTTPException(status_code=404)
    item.status = "approved"
    item.approval_remarks = remarks.strip()
    db.commit()
    log_event(db, "policy_version", "approve", current_user.full_name, item.version, new_value=remarks.strip() or item.title)
    return redirect_with_flash("/policies", f"Policy version {item.version} approved.")


@router.post("/policies/{policy_id}/publish")
@require_roles("super_admin", "hr_admin")
@require_permission("policies", "approve")
async def publish_policy(request: Request, policy_id: int, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    policy = db.get(PolicyVersion, policy_id)
    if not policy:
        raise HTTPException(status_code=404)
    if not policy.effective_date:
        return redirect_with_flash("/policies", "Effective date is required before publishing.", "error")
    for item in db.scalars(select(PolicyVersion).where(PolicyVersion.published == True, PolicyVersion.policy_master_id == policy.policy_master_id)).all():
        item.published = False
        if item.status == "published":
            item.status = "archived"
    policy.published = True
    policy.status = "published"
    db.commit()
    log_event(db, "policy_version", "publish", current_user.full_name, policy.version, new_value=policy.title)
    return redirect_with_flash("/policies", f"Policy version {policy.version} is now active.")


@router.post("/policies/{policy_id}/archive")
@require_roles("super_admin", "hr_admin")
@require_permission("policies", "edit")
async def archive_policy(policy_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    item = db.get(PolicyVersion, policy_id)
    if not item:
        raise HTTPException(status_code=404)
    item.published = False
    item.status = "archived"
    db.commit()
    log_event(db, "policy_version", "archive", current_user.full_name, item.version, new_value=item.title)
    return redirect_with_flash("/policies", f"Policy version {item.version} archived.")


@router.post("/policies/{policy_id}/clone")
@require_roles("super_admin", "hr_admin")
@require_permission("policies", "edit")
async def clone_policy(request: Request, policy_id: int, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    policy = db.get(PolicyVersion, policy_id)
    if not policy:
        raise HTTPException(status_code=404)
    new_policy = PolicyVersion(
        policy_master_id=policy.policy_master_id,
        title=policy.title,
        version=f"{policy.version}-draft",
        body=policy.body,
        status="draft",
        effective_date=None,
        approval_remarks="Cloned draft",
        published=False,
    )
    db.add(new_policy)
    db.commit()
    log_event(db, "policy_version", "clone", current_user.full_name, policy.version, new_value=new_policy.version)
    return redirect_with_flash("/policies", f"Draft cloned from {policy.version}.")


@router.get("/policy/current")
async def current_policy_file():
    if not POLICY_SAMPLE_FILE.exists():
        raise HTTPException(status_code=404, detail="Policy PDF not available.")
    response = FileResponse(POLICY_SAMPLE_FILE, media_type="application/pdf")
    response.headers["Content-Disposition"] = f'inline; filename="{POLICY_SAMPLE_FILE.name}"'
    return response


@router.get("/policy/reader", response_class=HTMLResponse)
async def current_policy_reader(request: Request, db: Session = Depends(get_db)):
    ensure_policy_catalog(db)
    user = get_current_user(request, db)
    allocation_id = request.query_params.get("allocation_id")
    policy_id = request.query_params.get("policy_id")
    allocation = None
    if allocation_id and allocation_id.isdigit():
        candidate = db.get(Allocation, int(allocation_id))
        if candidate and (not user or not user_has_role(user, "employee") or candidate.employee_id == user.id):
            allocation = candidate
    policy = allocation.policy if allocation and allocation.policy else None
    if not policy and policy_id and policy_id.isdigit() and user and user_has_role(user, "super_admin", "hr_admin"):
        policy = db.get(PolicyVersion, int(policy_id))
    if not policy:
        policy = get_published_policy(db)
    templates = db.scalars(select(PolicyTemplate).where(PolicyTemplate.active == True).order_by(PolicyTemplate.template_name)).all()
    back_href = "/allocations" if allocation else (request.headers.get("referer") or "/dashboard")
    return render(
        request,
        "policy_reader.html",
        {
            "sections": extract_policy_sections(),
            "is_public_reader": user is None,
            "policy": policy,
            "allocation": allocation,
            "policy_meta": POLICY_META,
            "policy_terms": POLICY_TERMS,
            "templates": templates,
            "back_href": back_href,
        },
        user,
    )


@router.get("/policies/templates/{template_id}/preview", response_class=HTMLResponse)
@require_roles("super_admin", "hr_admin")
@require_permission("policies", "view")
async def preview_policy_template(template_id: int, request: Request, db: Session = Depends(get_db)):
    ensure_policy_catalog(db)
    current_user = get_current_user(request, db)
    item = db.get(PolicyTemplate, template_id)
    if not item:
        raise HTTPException(status_code=404)
    preview_context = build_template_preview_context()
    rendered_html = render_policy_template_html(item.html_content, preview_context)
    return render(
        request,
        "policy_template_preview.html",
        {
            "template_item": item,
            "preview_context": preview_context,
            "rendered_html": rendered_html,
        },
        current_user,
    )
