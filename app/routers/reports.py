import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import (
    Allocation,
    Asset,
    AuditLog,
    AuditVerification,
    BackupAllocation,
    DisposalRequest,
    LiabilityRecord,
    MaintenanceTicket,
    NotificationDeliveryLog,
    ComplianceDeviation,
    PolicyVersion,
    ProcurementPlan,
    RepairEntry,
    ReplacementRecord,
    ReturnRequest,
    SignedDocument,
    TravelRequest,
    User,
    VerificationCampaign,
    VerificationExecution,
)
from app.services.helpers import (
    build_management_summary,
    document_asset_code,
    document_employee_name,
    document_policy_version,
    ensure_signed_document_templates,
    ensure_workflow_documents,
    get_current_holder,
    get_current_user,
    get_db,
    render,
    require_permission,
    require_roles,
)

router = APIRouter()


def make_report(title: str, headers: list[str], rows: list[list], export: str, filters: list[dict] | None = None, date_filter: dict | None = None) -> dict:
    return {
        "title": title,
        "headers": headers,
        "rows": rows,
        "export": export,
        "filters": filters or [],
        "date_filter": date_filter,
    }


def apply_report_filters(report: dict, params: dict[str, str]) -> tuple[dict, dict[str, str]]:
    rows = report["rows"]
    active_filters: dict[str, str] = {}
    query = (params.get("q") or "").strip().lower()
    if query:
        rows = [row for row in rows if query in " ".join(str(cell).lower() for cell in row)]
        active_filters["q"] = params.get("q", "")

    for filter_meta in report.get("filters", []):
        value = (params.get(filter_meta["name"]) or "").strip()
        if value:
            rows = [row for row in rows if str(row[filter_meta["index"]]) == value]
            active_filters[filter_meta["name"]] = value

    date_filter = report.get("date_filter")
    if date_filter:
        start_value = (params.get("start_date") or "").strip()
        end_value = (params.get("end_date") or "").strip()
        index = date_filter["index"]
        if start_value:
            rows = [row for row in rows if str(row[index]) >= start_value]
            active_filters["start_date"] = start_value
        if end_value:
            rows = [row for row in rows if str(row[index]) <= end_value]
            active_filters["end_date"] = end_value

    return {**report, "rows": rows}, active_filters


def build_excel_bytes(report: dict) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Report"
    sheet.append(report["headers"])
    for row in report["rows"]:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def build_pdf_bytes(report: dict) -> bytes:
    output = io.BytesIO()
    document = SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    data = [report["headers"]] + report["rows"]
    if len(data) == 1:
        data.append(["No rows available"] + [""] * (len(report["headers"]) - 1))
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0a2540")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e2ec")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fb")]),
            ]
        )
    )
    document.build([table])
    return output.getvalue()


def build_report_catalog(db: Session) -> dict[str, dict]:
    ensure_signed_document_templates(db)
    ensure_workflow_documents(db)
    assets = db.scalars(select(Asset).order_by(Asset.asset_code)).all()
    allocations = db.scalars(select(Allocation).order_by(Allocation.created_at.desc())).all()
    tickets = db.scalars(select(MaintenanceTicket).order_by(MaintenanceTicket.created_at.desc())).all()
    travels = db.scalars(select(TravelRequest).order_by(TravelRequest.created_at.desc())).all()
    returns = db.scalars(select(ReturnRequest).order_by(ReturnRequest.created_at.desc())).all()
    verifications = db.scalars(select(AuditVerification).order_by(AuditVerification.created_at.desc())).all()
    repairs = db.scalars(select(RepairEntry).order_by(RepairEntry.created_at.desc())).all()
    liabilities = db.scalars(select(LiabilityRecord).order_by(LiabilityRecord.created_at.desc())).all()
    disposals = db.scalars(select(DisposalRequest).order_by(DisposalRequest.created_at.desc())).all()
    procurement_plans = db.scalars(select(ProcurementPlan).order_by(ProcurementPlan.created_at.desc())).all()
    documents = db.scalars(select(SignedDocument).order_by(SignedDocument.created_at.desc())).all()
    replacements = db.scalars(select(ReplacementRecord).order_by(ReplacementRecord.created_at.desc())).all()
    backup_allocations = db.scalars(select(BackupAllocation).order_by(BackupAllocation.created_at.desc())).all()
    policies = db.scalars(select(PolicyVersion).order_by(PolicyVersion.created_at.desc())).all()
    users = db.scalars(select(User).order_by(User.full_name)).all()
    logs = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc())).all()
    verification_campaigns = db.scalars(select(VerificationCampaign).order_by(VerificationCampaign.created_at.desc())).all()
    verification_executions = db.scalars(select(VerificationExecution).order_by(VerificationExecution.created_at.desc())).all()
    deviations = db.scalars(select(ComplianceDeviation).order_by(ComplianceDeviation.created_at.desc())).all()
    delivery_logs = db.scalars(select(NotificationDeliveryLog).order_by(NotificationDeliveryLog.created_at.desc())).all()

    category_rows = []
    for category in sorted({asset.category.name for asset in assets}):
        category_assets = [asset for asset in assets if asset.category.name == category]
        category_rows.append(
            [
                category,
                len(category_assets),
                sum(1 for asset in category_assets if asset.status == "available"),
                sum(1 for asset in category_assets if asset.status == "active"),
                sum(1 for asset in category_assets if asset.status == "under_maintenance"),
                sum(1 for asset in category_assets if asset.status in {"damaged", "disposal_candidate"}),
            ]
        )

    department_rows = []
    for department in sorted({user.department for user in users if user.department}):
        department_allocations = [item for item in allocations if item.employee.department == department]
        department_rows.append(
            [
                department,
                len(department_allocations),
                sum(1 for item in department_allocations if item.status == "signed"),
                sum(1 for item in department_allocations if item.status == "pending_signature"),
                sum(1 for item in department_allocations if item.status == "returned"),
            ]
        )

    never_allocated_assets = [
        asset for asset in assets if not any(item.asset_id == asset.id for item in allocations)
    ]
    today = date.today()
    warranty_expired_assets = [asset for asset in assets if asset.warranty_until and asset.warranty_until < today]
    warranty_expiring_assets = [
        asset
        for asset in assets
        if asset.warranty_until and 0 <= (asset.warranty_until - today).days <= 30
    ]

    catalog = {
        "assets_all": make_report(
            "Master Asset Ledger",
            ["Asset", "Status", "Location", "Holder"],
            [[asset.asset_code, asset.status, asset.location.name, get_current_holder(asset, db)] for asset in assets],
            "assets_all",
            filters=[
                {"name": "status", "label": "Status", "index": 1, "options": sorted({asset.status for asset in assets})},
                {"name": "location", "label": "Location", "index": 2, "options": sorted({asset.location.name for asset in assets})},
            ],
        ),
        "assets_active": make_report(
            "Allocated Assets",
            ["Asset", "Status", "Location", "Holder"],
            [[asset.asset_code, asset.status, asset.location.name, get_current_holder(asset, db)] for asset in assets if asset.status == "active"],
            "assets_active",
            filters=[{"name": "location", "label": "Location", "index": 2, "options": sorted({asset.location.name for asset in assets if asset.status == "active"})}],
        ),
        "assets_idle": make_report(
            "Idle Assets",
            ["Asset", "Status", "Location", "Holder"],
            [[asset.asset_code, asset.status, asset.location.name, get_current_holder(asset, db)] for asset in assets if asset.status == "available"],
            "assets_idle",
            filters=[{"name": "location", "label": "Location", "index": 2, "options": sorted({asset.location.name for asset in assets if asset.status == "available"})}],
        ),
        "assets_by_category": make_report(
            "Category-Wise Asset Report",
            ["Category", "Total", "Available", "Allocated", "Under Repair", "Damaged/Disposal"],
            category_rows,
            "assets_by_category",
            filters=[{"name": "category", "label": "Category", "index": 0, "options": sorted({row[0] for row in category_rows})}],
        ),
        "assets_by_department": make_report(
            "Department-Wise Asset Report",
            ["Department", "Allocations", "Signed", "Pending Signature", "Returned"],
            department_rows,
            "assets_by_department",
            filters=[{"name": "department", "label": "Department", "index": 0, "options": sorted({row[0] for row in department_rows})}],
        ),
        "tickets_open": make_report(
            "Open Ticket Report",
            ["Ticket", "Asset", "Status", "Issue Type"],
            [[f"#{item.id}", item.asset.asset_code, item.status, item.issue_type] for item in tickets if item.status != "closed"],
            "tickets_open",
            filters=[
                {"name": "status", "label": "Status", "index": 2, "options": sorted({item.status for item in tickets if item.status != "closed"})},
                {"name": "issue_type", "label": "Issue Type", "index": 3, "options": sorted({item.issue_type for item in tickets if item.status != "closed"})},
            ],
        ),
        "allocations_signed": {
            "title": "Signed Allocation Report",
            "headers": ["Employee", "Asset", "Status", "Requested By"],
            "rows": [[item.employee.full_name, item.asset.asset_code, item.status, item.requested_by.full_name] for item in allocations if item.status == "signed"],
            "export": "allocations_signed",
        },
        "qr_deviations": {
            "title": "QR Verification Deviations",
            "headers": ["Asset", "Result", "Verifier", "Notes"],
            "rows": [[item.asset.asset_code, item.result, item.verified_by.full_name, item.notes or "-"] for item in verifications if item.result != "matched"],
            "export": "qr_deviations",
        },
        "repairs": make_report(
            "Repair History Report",
            ["Asset", "Repair Type", "Mode", "Cost", "Condition"],
            [[item.asset.asset_code, item.repair_type, item.repair_mode, item.repair_cost, item.final_condition] for item in repairs],
            "repairs",
            filters=[
                {"name": "mode", "label": "Mode", "index": 2, "options": sorted({item.repair_mode for item in repairs})},
                {"name": "condition", "label": "Condition", "index": 4, "options": sorted({item.final_condition for item in repairs})},
            ],
        ),
        "replacement_history": make_report(
            "Replacement History Report",
            ["Employee", "Old Asset", "New Asset", "Status", "Reason"],
            [[item.employee.full_name, item.old_asset.asset_code, item.new_asset.asset_code, item.status, item.reason] for item in replacements],
            "replacement_history",
            filters=[{"name": "status", "label": "Status", "index": 3, "options": sorted({item.status for item in replacements})}],
        ),
        "backup_allocations": make_report(
            "Backup Allocation Report",
            ["Employee", "Primary Asset", "Backup Asset", "Issue Date", "Expected Return", "Status"],
            [[item.employee.full_name, item.primary_asset.asset_code, item.backup_asset.asset_code, item.issue_date.isoformat(), item.expected_return_date.isoformat() if item.expected_return_date else "-", item.status] for item in backup_allocations],
            "backup_allocations",
            filters=[{"name": "status", "label": "Status", "index": 5, "options": sorted({item.status for item in backup_allocations})}],
            date_filter={"index": 3, "label": "Issue Date"},
        ),
        "liabilities_open": make_report(
            "Liability Report",
            ["Employee", "Asset", "Type", "Amount", "Recovery"],
            [[item.employee.full_name, item.asset.asset_code if item.asset else "-", item.liability_type, item.amount, item.recovery_status] for item in liabilities if item.recovery_status != "closed"],
            "liabilities_open",
            filters=[
                {"name": "liability_type", "label": "Type", "index": 2, "options": sorted({item.liability_type for item in liabilities if item.recovery_status != "closed"})},
                {"name": "recovery", "label": "Recovery", "index": 4, "options": sorted({item.recovery_status for item in liabilities if item.recovery_status != "closed"})},
            ],
        ),
        "disposal_open": make_report(
            "Disposal Candidate Report",
            ["Asset", "Status", "Condition", "Residual Value", "Recommendation"],
            [[item.asset.asset_code, item.status, item.condition, item.estimated_residual_value, item.recommendation or "-"] for item in disposals if item.status != "closed"],
            "disposal_open",
            filters=[
                {"name": "status", "label": "Status", "index": 1, "options": sorted({item.status for item in disposals if item.status != "closed"})},
                {"name": "condition", "label": "Condition", "index": 2, "options": sorted({item.condition for item in disposals if item.status != "closed"})},
            ],
        ),
        "procurement_plans": make_report(
            "Procurement Planning Report",
            ["Category", "Department", "Period", "Demand", "Replacement Due", "Suggested Procurement", "Budget"],
            [[item.category_name, item.department, f"{item.forecast_period_months} months", item.expected_demand, item.replacement_due, item.suggested_procurement, item.estimated_budget] for item in procurement_plans],
            "procurement_plans",
            filters=[
                {"name": "category", "label": "Category", "index": 0, "options": sorted({item.category_name for item in procurement_plans})},
                {"name": "department", "label": "Department", "index": 1, "options": sorted({item.department for item in procurement_plans})},
            ],
        ),
        "travel_approved": make_report(
            "Abroad Asset Report",
            ["Employee", "Asset", "Country", "Status", "Travel Window"],
            [[item.employee.full_name, item.asset.asset_code, item.destination_country, item.status, f"{item.departure_date} to {item.return_date}"] for item in travels if item.status == "approved"],
            "travel_approved",
            filters=[{"name": "country", "label": "Country", "index": 2, "options": sorted({item.destination_country for item in travels if item.status == "approved"})}],
        ),
        "verification_campaigns": make_report(
            "Verification Campaign Report",
            ["Campaign", "Category", "Department", "Location", "Mode", "Status"],
            [[item.campaign_name, item.asset_category or "All", item.department or "All", item.location or "All", item.verification_mode, item.status] for item in verification_campaigns],
            "verification_campaigns",
            filters=[
                {"name": "mode", "label": "Mode", "index": 4, "options": sorted({item.verification_mode for item in verification_campaigns})},
                {"name": "status", "label": "Status", "index": 5, "options": sorted({item.status for item in verification_campaigns})},
            ],
        ),
        "verification_executions": make_report(
            "Verification Execution Report",
            ["Campaign", "Asset", "Result", "Verifier", "Location", "Holder"],
            [[item.campaign.campaign_name, item.asset.asset_code, item.result, item.verified_by.full_name, item.location_confirmed or "-", item.holder_confirmed or "-"] for item in verification_executions],
            "verification_executions",
            filters=[
                {"name": "result", "label": "Result", "index": 2, "options": sorted({item.result for item in verification_executions})},
                {"name": "verifier", "label": "Verifier", "index": 3, "options": sorted({item.verified_by.full_name for item in verification_executions})},
            ],
        ),
        "returns_verified": {
            "title": "Return Verification Report",
            "headers": ["Employee", "Asset", "Status", "Clearance", "Liability"],
            "rows": [[item.employee.full_name, item.asset.asset_code, item.status, item.exit_clearance_status, item.liability_amount] for item in returns if item.status == "verified"],
            "export": "returns_verified",
        },
        "exit_clearance": make_report(
            "Exit Clearance Report",
            ["Employee", "Asset", "Return Status", "Clearance", "Liability Amount"],
            [[item.employee.full_name, item.asset.asset_code, item.status, item.exit_clearance_status, item.liability_amount] for item in returns],
            "exit_clearance",
            filters=[{"name": "clearance", "label": "Clearance", "index": 3, "options": sorted({item.exit_clearance_status for item in returns})}],
        ),
        "warranty_expired": make_report(
            "Warranty Expired Report",
            ["Asset", "Category", "Warranty End", "Status"],
            [[asset.asset_code, asset.category.name, asset.warranty_until.isoformat(), asset.status] for asset in warranty_expired_assets],
            "warranty_expired",
            filters=[{"name": "category", "label": "Category", "index": 1, "options": sorted({asset.category.name for asset in warranty_expired_assets})}],
            date_filter={"index": 2, "label": "Warranty End"},
        ),
        "warranty_expiring": make_report(
            "Warranty Expiry Report",
            ["Asset", "Category", "Warranty End", "Status"],
            [[asset.asset_code, asset.category.name, asset.warranty_until.isoformat(), asset.status] for asset in warranty_expiring_assets],
            "warranty_expiring",
            filters=[{"name": "category", "label": "Category", "index": 1, "options": sorted({asset.category.name for asset in warranty_expiring_assets})}],
            date_filter={"index": 2, "label": "Warranty End"},
        ),
        "never_allocated": make_report(
            "Never Allocated Asset Report",
            ["Asset", "Category", "Location", "Status"],
            [[asset.asset_code, asset.category.name, asset.location.name, asset.status] for asset in never_allocated_assets],
            "never_allocated",
            filters=[
                {"name": "category", "label": "Category", "index": 1, "options": sorted({asset.category.name for asset in never_allocated_assets})},
                {"name": "location", "label": "Location", "index": 2, "options": sorted({asset.location.name for asset in never_allocated_assets})},
            ],
        ),
        "documents_signed": {
            "title": "Signed Document Report",
            "headers": ["Document", "Employee", "Asset", "Template", "Source", "Policy", "Hash"],
            "rows": [
                [
                    item.document_number,
                    document_employee_name(item),
                    document_asset_code(item),
                    item.template_code or item.template_name or "-",
                    item.source_module,
                    document_policy_version(item),
                    item.hash_sha256,
                ]
                for item in documents
            ],
            "export": "documents_signed",
            "filters": [
                {"name": "template", "label": "Template", "index": 3, "options": sorted({(item.template_code or item.template_name or "-") for item in documents})},
                {"name": "source", "label": "Source", "index": 4, "options": sorted({item.source_module for item in documents})},
                {"name": "policy", "label": "Policy", "index": 5, "options": sorted({document_policy_version(item) for item in documents})},
            ],
        },
        "document_template_usage": make_report(
            "Document Template Usage",
            ["Template", "Documents", "Latest Document", "Sources"],
            [
                [
                    template_name,
                    len([item for item in documents if (item.template_code or item.template_name or "Unmapped") == template_name]),
                    next((item.document_number for item in documents if (item.template_code or item.template_name or "Unmapped") == template_name), "-"),
                    ", ".join(sorted({item.source_module for item in documents if (item.template_code or item.template_name or "Unmapped") == template_name})),
                ]
                for template_name in sorted({item.template_code or item.template_name or "Unmapped" for item in documents})
            ],
            "document_template_usage",
            filters=[{"name": "template", "label": "Template", "index": 0, "options": sorted({item.template_code or item.template_name or "Unmapped" for item in documents})}],
        ),
        "policy_versions": make_report(
            "Policy Version Report",
            ["Title", "Version", "Status", "Created At"],
            [[item.title, item.version, "published" if item.published else "draft", item.created_at.date().isoformat()] for item in policies],
            "policy_versions",
            filters=[{"name": "status", "label": "Status", "index": 2, "options": sorted({"published" if item.published else "draft" for item in policies})}],
            date_filter={"index": 3, "label": "Created At"},
        ),
        "user_activity": make_report(
            "User Activity Report",
            ["User", "Email", "Role", "Last Login", "Failed Login Attempts"],
            [[item.full_name, item.email, item.role, item.last_login.date().isoformat() if item.last_login else "-", item.failed_login_attempts] for item in users],
            "user_activity",
            filters=[{"name": "role", "label": "Role", "index": 2, "options": sorted({item.role for item in users})}],
            date_filter={"index": 3, "label": "Last Login"},
        ),
        "audit_ledger": make_report(
            "Audit Ledger Report",
            ["Module", "Action", "Actor", "Reference", "When"],
            [[item.module, item.action, item.actor_name, item.reference_id, item.created_at.date().isoformat()] for item in logs],
            "audit_ledger",
            filters=[
                {"name": "module", "label": "Module", "index": 0, "options": sorted({item.module for item in logs})},
                {"name": "action", "label": "Action", "index": 1, "options": sorted({item.action for item in logs})},
            ],
            date_filter={"index": 4, "label": "When"},
        ),
    }

    catalog.update({
        "category_utilization": make_report("Category-wise Utilization Report", ["Category", "Total", "Available", "Allocated", "Utilization %"], [[row[0], row[1], row[2], row[3], round((row[3] / row[1] * 100), 2) if row[1] else 0] for row in category_rows], "category_utilization"),
        "department_utilization": make_report("Department-wise Utilization Report", ["Department", "Allocations", "Signed", "Pending", "Returned"], department_rows, "department_utilization"),
        "project_assets": make_report("Project-wise Asset Report", ["Project", "Asset", "Holder", "Status"], [[getattr(asset, "specification", "CDIPD") or "CDIPD", asset.asset_code, get_current_holder(asset, db), asset.status] for asset in assets], "project_assets"),
        "location_room_assets": make_report("Location/Room-wise Asset Report", ["Location", "Asset", "Category", "Status"], [[asset.location.name, asset.asset_code, asset.category.name, asset.status] for asset in assets], "location_room_assets"),
        "asset_value": make_report("Asset Value Report", ["Asset", "Category", "Purchase Date", "Value"], [[asset.asset_code, asset.category.name, asset.purchase_date.isoformat() if asset.purchase_date else "-", getattr(asset, "acquisition_value", 0)] for asset in assets], "asset_value"),
        "asset_ageing": make_report("Asset Ageing Report", ["Asset", "Category", "Purchase Date", "Age Days", "Status"], [[asset.asset_code, asset.category.name, asset.purchase_date.isoformat() if asset.purchase_date else "-", (today - asset.purchase_date).days if asset.purchase_date else 0, asset.status] for asset in assets], "asset_ageing"),
        "idle_asset_ageing": make_report("Idle Asset Ageing Report", ["Asset", "Category", "Idle Days", "Location"], [[asset.asset_code, asset.category.name, (today - asset.created_at.date()).days, asset.location.name] for asset in assets if asset.status == "available"], "idle_asset_ageing"),
        "reallocation_candidates": make_report("Reallocation Candidate Report", ["Asset", "Category", "Location", "Reason"], [[asset.asset_code, asset.category.name, asset.location.name, "Idle stock"] for asset in assets if asset.status == "available"], "reallocation_candidates"),
        "replacement_due": make_report("Replacement Due Report", ["Asset", "Category", "Warranty", "Status"], [[asset.asset_code, asset.category.name, asset.warranty_until.isoformat() if asset.warranty_until else "-", asset.status] for asset in warranty_expired_assets], "replacement_due"),
        "repeated_issue_assets": make_report("Repeated Issue Asset Report", ["Asset", "Ticket Count", "Repair Count"], [[asset.asset_code, sum(1 for t in tickets if t.asset_id == asset.id), sum(1 for r in repairs if r.asset_id == asset.id)] for asset in assets if sum(1 for t in tickets if t.asset_id == asset.id) > 1], "repeated_issue_assets"),
        "repair_cost_analysis": make_report("Repair Cost Analysis Report", ["Asset", "Repair Type", "Cost", "Warranty Claim"], [[item.asset.asset_code, item.repair_type, item.repair_cost, "Yes" if item.warranty_claim else "No"] for item in repairs], "repair_cost_analysis"),
        "warranty_claims": make_report("Warranty Claim Report", ["Asset", "Repair", "Cost", "Created"], [[item.asset.asset_code, item.repair_type, item.repair_cost, item.created_at.date().isoformat()] for item in repairs if item.warranty_claim], "warranty_claims"),
        "backup_overdue": make_report("Backup Overdue Report", ["Employee", "Backup Asset", "Expected Return", "Status"], [[item.employee.full_name, item.backup_asset.asset_code, item.expected_return_date.isoformat() if item.expected_return_date else "-", item.status] for item in backup_allocations if item.status == "active" and item.expected_return_date and item.expected_return_date < today], "backup_overdue"),
        "abroad_overdue_returns": make_report("Abroad Overdue Return Report", ["Employee", "Asset", "Country", "Return Date", "Status"], [[item.employee.full_name, item.asset.asset_code, item.destination_country, item.return_date.isoformat(), item.status] for item in travels if item.status == "approved" and item.return_date < today], "abroad_overdue_returns"),
        "policy_version_comparison": make_report("Policy Version Comparison Report", ["Policy", "Version", "Status", "Signed Count"], [[item.title, item.version, item.status, sum(1 for doc in documents if doc.policy_id == item.id)] for item in policies], "policy_version_comparison"),
        "unsigned_policy_ageing": make_report("Unsigned Policy Ageing Report", ["Employee", "Asset", "Age Days", "Policy"], [[item.employee.full_name, item.asset.asset_code, (today - item.created_at.date()).days, item.policy.version if item.policy else "-"] for item in allocations if item.status == "pending_signature"], "unsigned_policy_ageing"),
        "compliance_deviations": make_report("Compliance Deviation Report", ["Deviation", "Source", "Severity", "Status", "Due"], [[item.deviation_number, item.source_module, item.severity, item.status, item.due_date.isoformat() if item.due_date else "-"] for item in deviations], "compliance_deviations"),
        "corrective_actions": make_report("Corrective Action Report", ["Deviation", "Corrective Action", "Preventive Action", "Status"], [[item.deviation_number, item.corrective_action, item.preventive_action, item.status] for item in deviations], "corrective_actions"),
        "disposal_approval_ageing": make_report("Disposal Approval Ageing Report", ["Request", "Asset", "Age Days", "Status"], [[f"#{item.id}", item.asset.asset_code, (today - item.created_at.date()).days, item.status] for item in disposals if item.status not in {"closed", "rejected"}], "disposal_approval_ageing"),
        "ewaste_disposal_register": make_report("E-waste Disposal Register", ["Request", "Asset", "Vendor", "Method", "Certificate"], [[f"#{item.id}", item.asset.asset_code, item.vendor_agency, item.disposal_method, item.certificate_number] for item in disposals], "ewaste_disposal_register"),
        "disposal_certificate_repository": make_report("Disposal Certificate Repository", ["Certificate", "Asset", "Date", "Path"], [[item.certificate_number or "-", item.asset.asset_code, item.disposal_date.isoformat() if item.disposal_date else "-", getattr(item, "certificate_path", "")] for item in disposals], "disposal_certificate_repository"),
        "procurement_forecast_3m": make_report("Procurement Forecast 3-month Report", ["Category", "Department", "Suggested", "Budget"], [[item.category_name, item.department, item.suggested_procurement, item.estimated_budget] for item in procurement_plans if item.forecast_period_months == 3], "procurement_forecast_3m"),
        "procurement_forecast_6m": make_report("Procurement Forecast 6-month Report", ["Category", "Department", "Suggested", "Budget"], [[item.category_name, item.department, item.suggested_procurement, item.estimated_budget] for item in procurement_plans if item.forecast_period_months == 6], "procurement_forecast_6m"),
        "procurement_forecast_12m": make_report("Procurement Forecast 12-month Report", ["Category", "Department", "Suggested", "Budget"], [[item.category_name, item.department, item.suggested_procurement, item.estimated_budget] for item in procurement_plans if item.forecast_period_months == 12], "procurement_forecast_12m"),
        "shortage_excess_stock": make_report("Shortage/Excess Stock Report", ["Category", "Stock", "Suggested Procurement", "Variance"], [[item.category_name, stock_map if False else item.available_reusable_stock, item.suggested_procurement, item.available_reusable_stock - item.suggested_procurement] for item in procurement_plans], "shortage_excess_stock"),
        "audit_evidence_package": make_report("Audit Evidence Package Report", ["Document", "Source", "Hash", "Created"], [[item.document_number, item.source_module, item.hash_sha256, item.created_at.date().isoformat()] for item in documents], "audit_evidence_package"),
        "failed_login": make_report("Failed Login Report", ["User", "Email", "Failed Attempts", "Last Login"], [[item.full_name, item.email, item.failed_login_attempts, item.last_login.date().isoformat() if item.last_login else "-"] for item in users if item.failed_login_attempts], "failed_login"),
        "notification_delivery": make_report("Notification Delivery Report", ["Event", "Recipient", "Channel", "Status", "When"], [[item.event_code, item.recipient, item.channel, item.status, item.created_at.date().isoformat()] for item in delivery_logs], "notification_delivery"),
    })
    return catalog


@router.get("/reports", response_class=HTMLResponse)
@require_roles("super_admin", "hardware_admin", "hr_admin", "director", "auditor")
@require_permission("reports", "view")
async def reports_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    catalog = build_report_catalog(db)
    asset_rows = catalog["assets_all"]["rows"]
    document_rows = catalog["documents_signed"]["rows"]
    operations_rows = (
        [["Ticket", row[0], row[1], row[3]] for row in catalog["tickets_open"]["rows"]]
        + [["Travel", row[1], row[3], row[2]] for row in catalog["travel_approved"]["rows"]]
        + [["Return", row[1], row[3], str(row[4])] for row in catalog["returns_verified"]["rows"]]
        + [["Backup", row[1], row[5], row[2]] for row in catalog["backup_allocations"]["rows"]]
        + [["Verification", row[0], row[5], row[4]] for row in catalog["verification_campaigns"]["rows"]]
    )
    report_summary = [
        {"label": "Total Assets", "value": len(catalog["assets_all"]["rows"]), "href": "/reports/drilldown/assets_all"},
        {"label": "Active", "value": len(catalog["assets_active"]["rows"]), "href": "/reports/drilldown/assets_active"},
        {"label": "Idle", "value": len(catalog["assets_idle"]["rows"]), "href": "/reports/drilldown/assets_idle"},
        {"label": "Open Tickets", "value": len(catalog["tickets_open"]["rows"]), "href": "/reports/drilldown/tickets_open"},
        {"label": "Repairs", "value": len(catalog["repairs"]["rows"]), "href": "/reports/drilldown/repairs"},
        {"label": "Backup", "value": len(catalog["backup_allocations"]["rows"]), "href": "/reports/drilldown/backup_allocations"},
        {"label": "Verification", "value": len(catalog["verification_campaigns"]["rows"]), "href": "/reports/drilldown/verification_campaigns"},
        {"label": "Liabilities", "value": len(catalog["liabilities_open"]["rows"]), "href": "/reports/drilldown/liabilities_open"},
        {"label": "Disposals", "value": len(catalog["disposal_open"]["rows"]), "href": "/reports/drilldown/disposal_open"},
        {"label": "Procurement", "value": len(catalog["procurement_plans"]["rows"]), "href": "/reports/drilldown/procurement_plans"},
    ]
    return render(
        request,
        "reports.html",
        {
            "report_summary": report_summary,
            "asset_rows": asset_rows,
            "document_rows": document_rows,
            "operations_rows": operations_rows,
            "verification_rows": catalog["qr_deviations"]["rows"],
            "catalog_links": [
                ("Category", "/reports/drilldown/assets_by_category"),
                ("Department", "/reports/drilldown/assets_by_department"),
                ("Warranty Expiry", "/reports/drilldown/warranty_expiring"),
                ("Warranty Expired", "/reports/drilldown/warranty_expired"),
                ("Never Allocated", "/reports/drilldown/never_allocated"),
                ("Replacement", "/reports/drilldown/replacement_history"),
                ("Backup Allocations", "/reports/drilldown/backup_allocations"),
                ("Verification Campaigns", "/reports/drilldown/verification_campaigns"),
                ("Verification Executions", "/reports/drilldown/verification_executions"),
                ("Exit Clearance", "/reports/drilldown/exit_clearance"),
                ("Template Usage", "/reports/drilldown/document_template_usage"),
                ("Policy Versions", "/reports/drilldown/policy_versions"),
                ("User Activity", "/reports/drilldown/user_activity"),
            ],
        },
        current_user,
    )


@router.get("/reports/drilldown/{report_name}", response_class=HTMLResponse)
@require_roles("super_admin", "hardware_admin", "hr_admin", "director", "auditor")
@require_permission("reports", "view")
async def report_drilldown(report_name: str, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    catalog = build_report_catalog(db)
    report = catalog.get(report_name)
    if not report:
        raise HTTPException(status_code=404)
    filtered_report, active_filters = apply_report_filters(report, dict(request.query_params))
    return render(request, "report_drilldown.html", {"report": filtered_report, "report_name": report_name, "active_filters": active_filters}, current_user)


@router.get("/management/reports", response_class=HTMLResponse)
@require_roles("director", "super_admin")
@require_permission("reports", "view")
async def management_reports_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    management = build_management_summary(db)
    summary_cards = [
        {"label": "Total Assets", "value": management["total_assets"], "href": "/reports/drilldown/assets_all"},
        {"label": "In Use", "value": management["utilization"], "href": "/reports/drilldown/assets_active"},
        {"label": "Idle Stock", "value": len(management["idle_assets"]), "href": "/reports/drilldown/assets_idle"},
        {"label": "Repair Risk", "value": len(management["repair_assets"]), "href": "/reports/drilldown/repairs"},
        {"label": "Backup Active", "value": len(management["backup_allocations"]), "href": "/reports/drilldown/backup_allocations"},
        {"label": "Verification", "value": len(management["verification_campaigns"]), "href": "/reports/drilldown/verification_campaigns"},
        {"label": "Disposal Queue", "value": len(management["disposals"]), "href": "/reports/drilldown/disposal_open"},
        {"label": "Procurement", "value": len(management["procurement"]), "href": "/reports/drilldown/procurement_plans"},
    ]
    return render(
        request,
        "management_reports.html",
        {
            "management": management,
            "summary_cards": summary_cards,
            "holder_map": {asset.id: get_current_holder(asset, db) for asset in management["idle_assets"] + management["repair_assets"] + management["risk_assets"]},
        },
        current_user,
    )


@router.get("/reports/export/{report_name}")
@require_roles("super_admin", "hardware_admin", "hr_admin", "director", "auditor")
@require_permission("reports", "export")
async def export_report(report_name: str, request: Request, db: Session = Depends(get_db)):
    catalog = build_report_catalog(db)
    report = catalog.get(report_name)
    if not report:
        raise HTTPException(status_code=404)
    filtered_report, _ = apply_report_filters(report, dict(request.query_params))
    export_format = (request.query_params.get("format") or "csv").strip().lower()
    if export_format == "xlsx":
        payload = build_excel_bytes(filtered_report)
        return StreamingResponse(iter([payload]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename={report_name}.xlsx"})
    if export_format == "pdf":
        payload = build_pdf_bytes(filtered_report)
        return StreamingResponse(iter([payload]), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={report_name}.pdf"})
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(filtered_report["headers"])
    writer.writerows(filtered_report["rows"])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={report_name}.csv"})
