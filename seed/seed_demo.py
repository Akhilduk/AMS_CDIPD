
import os
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select, func, text
from app.core.database import SessionLocal, engine
from app.models.models import *
from app.services.helpers import (
    POLICY_META,
    POLICY_SECTIONS,
    create_signed_document_record,
    hash_password,
    save_qr,
    sync_role_permissions,
)


def ensure_demo_schema(db: Session) -> None:
    inspector = db.connection()
    user_columns = {row[1] for row in inspector.execute(text("PRAGMA table_info(users)")).fetchall()}
    additions = [
        ("multiple_roles_enabled", "ALTER TABLE users ADD COLUMN multiple_roles_enabled BOOLEAN DEFAULT 0"),
        ("designation", "ALTER TABLE users ADD COLUMN designation VARCHAR(120) DEFAULT ''"),
        ("mobile", "ALTER TABLE users ADD COLUMN mobile VARCHAR(20) DEFAULT ''"),
        ("reporting_manager", "ALTER TABLE users ADD COLUMN reporting_manager VARCHAR(120) DEFAULT ''"),
        ("employee_type", "ALTER TABLE users ADD COLUMN employee_type VARCHAR(40) DEFAULT 'Regular'"),
        ("joining_date", "ALTER TABLE users ADD COLUMN joining_date DATE"),
        ("failed_login_attempts", "ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER DEFAULT 0"),
        ("last_login", "ALTER TABLE users ADD COLUMN last_login DATETIME"),
    ]
    for column_name, sql in additions:
        if column_name not in user_columns:
            inspector.execute(text(sql))
    policy_columns = {row[1] for row in inspector.execute(text("PRAGMA table_info(policies)")).fetchall()}
    policy_additions = [
        ("policy_master_id", "ALTER TABLE policies ADD COLUMN policy_master_id INTEGER"),
        ("status", "ALTER TABLE policies ADD COLUMN status VARCHAR(40) DEFAULT 'draft'"),
        ("effective_date", "ALTER TABLE policies ADD COLUMN effective_date DATE"),
        ("approval_remarks", "ALTER TABLE policies ADD COLUMN approval_remarks TEXT DEFAULT ''"),
    ]
    for column_name, sql in policy_additions:
        if column_name not in policy_columns:
            inspector.execute(text(sql))
    signed_document_columns = {row[1] for row in inspector.execute(text("PRAGMA table_info(signed_documents)")).fetchall()}
    signed_document_additions = [
        ("employee_id", "ALTER TABLE signed_documents ADD COLUMN employee_id INTEGER"),
        ("asset_id", "ALTER TABLE signed_documents ADD COLUMN asset_id INTEGER"),
        ("policy_id", "ALTER TABLE signed_documents ADD COLUMN policy_id INTEGER"),
        ("template_id", "ALTER TABLE signed_documents ADD COLUMN template_id INTEGER"),
        ("template_code", "ALTER TABLE signed_documents ADD COLUMN template_code VARCHAR(40) DEFAULT ''"),
        ("template_name", "ALTER TABLE signed_documents ADD COLUMN template_name VARCHAR(160) DEFAULT ''"),
        ("source_module", "ALTER TABLE signed_documents ADD COLUMN source_module VARCHAR(40) DEFAULT 'allocation'"),
        ("source_reference", "ALTER TABLE signed_documents ADD COLUMN source_reference VARCHAR(80) DEFAULT ''"),
        ("rendered_content", "ALTER TABLE signed_documents ADD COLUMN rendered_content TEXT DEFAULT ''"),
    ]
    for column_name, sql in signed_document_additions:
        if column_name not in signed_document_columns:
            inspector.execute(text(sql))
    db.commit()

def seed_data():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        ensure_demo_schema(db)
        Base.metadata.create_all(engine)
        category_map = {}
        for name in ["Laptop", "Monitor", "Mobile", "Tablet", "Network Device"]:
            item = db.scalar(select(Category).where(Category.name == name))
            if not item:
                item = Category(name=name)
                db.add(item)
                db.flush()
            category_map[name] = item

        vendor_map = {}
        for name in ["Dell", "HP", "Apple", "Lenovo", "Samsung", "Cisco"]:
            item = db.scalar(select(Vendor).where(Vendor.name == name))
            if not item:
                item = Vendor(name=name)
                db.add(item)
                db.flush()
            vendor_map[name] = item

        location_map = {}
        for name in ["Head Office", "Lab", "Remote", "Innovation Wing", "Conference Room"]:
            item = db.scalar(select(Location).where(Location.name == name))
            if not item:
                item = Location(name=name)
                db.add(item)
                db.flush()
            location_map[name] = item

        if not db.get(NumberingSetting, 1):
            db.add(NumberingSetting(id=1))
            db.flush()

        if db.scalar(select(func.count(WorkflowSetting.id))) == 0:
            db.add_all(
                [
                    WorkflowSetting(category_name="Laptop", hr_approval_required=True, signing_required=True, director_approval_required=True, return_check_required=True),
                    WorkflowSetting(category_name="Monitor", hr_approval_required=True, signing_required=True, director_approval_required=False, return_check_required=True),
                    WorkflowSetting(category_name="Mobile", hr_approval_required=True, signing_required=True, director_approval_required=True, return_check_required=True),
                    WorkflowSetting(category_name="Tablet", hr_approval_required=True, signing_required=True, director_approval_required=False, return_check_required=True),
                    WorkflowSetting(category_name="Network Device", hr_approval_required=True, signing_required=False, director_approval_required=False, return_check_required=True),
                ]
            )
            db.flush()

        user_specs = [
            ("Super Admin", "superadmin@cdipd.local", "EMP-001", "super_admin", "Platform Administrator", "9999900001", "Director", "Admin"),
            ("HR Admin", "hr@cdipd.local", "EMP-002", "hr_admin", "HR Operations Lead", "9999900002", "Director", "Regular"),
            ("Hardware Admin", "hardware@cdipd.local", "EMP-003", "hardware_admin", "IT Systems Administrator", "9999900003", "Director", "Regular"),
            ("Anjana Employee", "employee@cdipd.local", "EMP-004", "employee", "Product Associate", "9999900004", "HR Admin", "Regular"),
            ("Director", "director@cdipd.local", "EMP-005", "director", "Director", "9999900005", "Board", "Leadership"),
            ("Auditor", "auditor@cdipd.local", "EMP-006", "auditor", "Internal Auditor", "9999900006", "Director", "Audit"),
            ("Rahul Menon", "rahul.menon@cdipd.local", "EMP-007", "employee", "Research Engineer", "9999900007", "HR Admin", "Regular"),
            ("Nithya Das", "nithya.das@cdipd.local", "EMP-008", "employee", "UX Designer", "9999900008", "HR Admin", "Regular"),
        ]
        role_specs = [
            ("super_admin", "Super Admin", "Full platform administration and configuration"),
            ("hr_admin", "HR Admin", "Allocation approvals, compliance, and exit clearance"),
            ("hardware_admin", "Hardware Admin", "Inventory, maintenance, QR, and return operations"),
            ("employee", "Employee", "Asset consumption, signing, requests, and downloads"),
            ("director", "Director", "Abroad approvals and strategic reporting"),
            ("auditor", "Auditor", "Audit evidence, repositories, and independent checks"),
        ]
        role_map = {}
        for code, name, description in role_specs:
            role = db.scalar(select(Role).where(Role.code == code))
            if not role:
                role = Role(code=code, name=name, description=description, active=True)
                db.add(role)
                db.flush()
            else:
                role.name = name
                role.description = description
                role.active = True
            role_map[code] = role

        policy_master = db.scalar(select(PolicyMaster).where(PolicyMaster.policy_code == "POL-ASSET-USAGE"))
        if not policy_master:
            policy_master = PolicyMaster(
                policy_code="POL-ASSET-USAGE",
                title="Asset Usage Policy",
                description="Core asset usage, acknowledgement, and compliance policy.",
                owner_role="hr_admin",
                active=True,
            )
            db.add(policy_master)
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

        user_map = {}
        for full_name, email, employee_code, role, designation, mobile, reporting_manager, employee_type in user_specs:
            user = db.scalar(select(User).where(User.email == email))
            if not user:
                user = User(
                    full_name=full_name,
                    email=email,
                    employee_code=employee_code,
                    role=role,
                    designation=designation,
                    mobile=mobile,
                    reporting_manager=reporting_manager,
                    employee_type=employee_type,
                    joining_date=date.today() - timedelta(days=365),
                    password_hash=hash_password("Demo@12345"),
                )
                db.add(user)
                db.flush()
            else:
                user.role = role
                user.designation = designation
                user.mobile = mobile
                user.reporting_manager = reporting_manager
                user.employee_type = employee_type
                if not user.joining_date:
                    user.joining_date = date.today() - timedelta(days=365)
            user_map[email] = user
            user_role = db.scalar(select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role_map[role].id))
            if not user_role:
                db.add(UserRole(user_id=user.id, role_id=role_map[role].id, is_primary=True))

        sync_role_permissions(db)

        policy = db.scalar(select(PolicyVersion).where(PolicyVersion.version == "v1.0"))
        if not policy:
            policy = PolicyVersion(
                policy_master_id=policy_master.id,
                title=POLICY_META["title"],
                version="v1.0",
                body="\n\n".join(
                    [section["title"] + "\n" + "\n".join(section.get("paragraphs", []) + section.get("bullets", [])) for section in POLICY_SECTIONS]
                ),
                status="published",
                effective_date=date.today(),
                approval_remarks="Seeded published policy",
                published=True,
            )
            db.add(policy)
            db.flush()
        elif policy.body.strip() != "\n\n".join(
            [section["title"] + "\n" + "\n".join(section.get("paragraphs", []) + section.get("bullets", [])) for section in POLICY_SECTIONS]
        ):
            policy.title = POLICY_META["title"]
            policy.policy_master_id = policy_master.id
            policy.status = "published" if policy.published else "draft"
            if not policy.effective_date:
                policy.effective_date = date.today()
            policy.body = "\n\n".join(
                [section["title"] + "\n" + "\n".join(section.get("paragraphs", []) + section.get("bullets", [])) for section in POLICY_SECTIONS]
            )

        asset_specs = [
            ("AST-0001", "Dell Latitude Laptop", "Latitude 5440", "SN-DL-001", "Laptop", "Dell", "Head Office", "available", "16GB RAM, 512GB SSD, Windows 11 Pro", 90),
            ("AST-0002", "MacBook Pro", "M3 Pro 14", "SN-AP-014", "Laptop", "Apple", "Innovation Wing", "active", "18GB RAM, 1TB SSD, macOS", 180),
            ("AST-0003", "HP EliteDisplay", "E24 G5", "SN-HP-024", "Monitor", "HP", "Lab", "active", "24-inch IPS display", 240),
            ("AST-0004", "Samsung Galaxy Tab", "S9 FE", "SN-SG-101", "Tablet", "Samsung", "Remote", "under_maintenance", "Android tablet for field work", 150),
            ("AST-0005", "Cisco Meraki Appliance", "MX68", "SN-CS-301", "Network Device", "Cisco", "Conference Room", "available", "Security appliance for event network", 320),
        ]
        asset_map = {}
        for asset_code, name, model, serial, category_name, vendor_name, location_name, status, specification, age_days in asset_specs:
            asset = db.scalar(select(Asset).where(Asset.asset_code == asset_code))
            if not asset:
                asset = Asset(
                    asset_code=asset_code,
                    name=name,
                    model=model,
                    serial_number=serial,
                    category_id=category_map[category_name].id,
                    vendor_id=vendor_map[vendor_name].id,
                    location_id=location_map[location_name].id,
                    status=status,
                    specification=specification,
                    purchase_date=date.today() - timedelta(days=age_days),
                    warranty_until=date.today() + timedelta(days=365),
                )
                db.add(asset)
                db.flush()
                asset.qr_path = save_qr(asset)
            asset_map[asset_code] = asset

        if not db.scalar(select(Allocation).where(Allocation.asset_id == asset_map["AST-0001"].id, Allocation.employee_id == user_map["employee@cdipd.local"].id)):
            db.add(
                Allocation(
                    asset_id=asset_map["AST-0001"].id,
                    employee_id=user_map["employee@cdipd.local"].id,
                    policy_id=policy.id,
                    requested_by_id=user_map["hardware@cdipd.local"].id,
                    approved_by_id=user_map["hr@cdipd.local"].id,
                    status="pending_signature",
                    remarks="Primary laptop issued to Anjana; awaiting employee sign-off.",
                )
            )

        signed_allocation = db.scalar(select(Allocation).where(Allocation.asset_id == asset_map["AST-0002"].id))
        if not signed_allocation:
            signed_allocation = Allocation(
                asset_id=asset_map["AST-0002"].id,
                employee_id=user_map["rahul.menon@cdipd.local"].id,
                policy_id=policy.id,
                requested_by_id=user_map["hardware@cdipd.local"].id,
                approved_by_id=user_map["hr@cdipd.local"].id,
                status="signed",
                remarks="Travel laptop already acknowledged by employee.",
            )
            db.add(signed_allocation)
            db.flush()
            asset_map["AST-0002"].status = "active"
            create_signed_document_record(db, signed_allocation, "482901", "Rahul Menon")

        active_monitor_allocation = db.scalar(select(Allocation).where(Allocation.asset_id == asset_map["AST-0003"].id))
        if not active_monitor_allocation:
            active_monitor_allocation = Allocation(
                asset_id=asset_map["AST-0003"].id,
                employee_id=user_map["nithya.das@cdipd.local"].id,
                policy_id=policy.id,
                requested_by_id=user_map["hardware@cdipd.local"].id,
                approved_by_id=user_map["hr@cdipd.local"].id,
                status="signed",
                remarks="Secondary display deployed for design workstation.",
            )
            db.add(active_monitor_allocation)
            db.flush()
            asset_map["AST-0003"].status = "active"
            create_signed_document_record(db, active_monitor_allocation, "773214", "Nithya Das")

        for allocation in db.scalars(select(Allocation)).all():
            if allocation.declaration_text.strip() == "I acknowledge responsibility for this asset.":
                allocation.declaration_text = "I confirm that I have received the listed asset in good order from CDIPD, that I have read and understood the Asset Usage Policy, and that I agree to use the asset only for official purposes, protect it from loss or damage, report issues immediately, and return it when required."

        if not db.scalar(select(MaintenanceTicket).where(MaintenanceTicket.asset_id == asset_map["AST-0004"].id)):
            db.add(
                MaintenanceTicket(
                    asset_id=asset_map["AST-0004"].id,
                    raised_by_id=user_map["employee@cdipd.local"].id,
                    assigned_to_id=user_map["hardware@cdipd.local"].id,
                    issue_type="Battery performance",
                    description="Battery drains within two hours during field visits.",
                    status="in_progress",
                    resolution_notes="Spare battery ordered; evaluating replacement option.",
                )
            )
        if not db.scalar(select(RepairEntry).where(RepairEntry.asset_id == asset_map["AST-0004"].id)):
            db.add(
                RepairEntry(
                    asset_id=asset_map["AST-0004"].id,
                    repair_type="Battery replacement",
                    repair_mode="internal",
                    warranty_claim=False,
                    repair_start_date=date.today() - timedelta(days=3),
                    repair_cost=4200,
                    diagnosis="Battery health degraded below operational threshold.",
                    resolution="Temporary service performed while replacement pack is procured.",
                    final_condition="serviceable",
                    created_by_id=user_map["hardware@cdipd.local"].id,
                )
            )

        if not db.scalar(select(TravelRequest).where(TravelRequest.asset_id == asset_map["AST-0002"].id)):
            db.add(
                TravelRequest(
                    asset_id=asset_map["AST-0002"].id,
                    employee_id=user_map["rahul.menon@cdipd.local"].id,
                    destination_country="Germany",
                    purpose="Research collaboration and product workshop",
                    departure_date=date.today() + timedelta(days=7),
                    return_date=date.today() + timedelta(days=20),
                    status="approved",
                    director_comment="Approved with mandatory return confirmation on re-entry.",
                )
            )

        if not db.scalar(select(ReturnRequest).where(ReturnRequest.asset_id == asset_map["AST-0003"].id)):
            db.add(
                ReturnRequest(
                    asset_id=asset_map["AST-0003"].id,
                    employee_id=user_map["nithya.das@cdipd.local"].id,
                    initiated_by_role="hr_admin",
                    reason="Workstation reassignment after team transfer.",
                    condition_notes="Display checked, adapter included.",
                    qr_verified=True,
                    status="verified",
                    liability_amount=0,
                    exit_clearance_status="completed",
                )
            )
            asset_map["AST-0003"].status = "available"

        if not db.scalar(select(LiabilityRecord).where(LiabilityRecord.employee_id == user_map["nithya.das@cdipd.local"].id)):
            db.add(
                LiabilityRecord(
                    employee_id=user_map["nithya.das@cdipd.local"].id,
                    asset_id=asset_map["AST-0003"].id,
                    liability_type="Missing accessory",
                    amount=1500,
                    recovery_status="pending",
                    hr_approval_status="approved",
                    remarks="Monitor HDMI adapter not returned during reassignment.",
                    source_reference="seed-return-gap",
                )
            )

        if not db.scalar(select(DisposalRequest).where(DisposalRequest.asset_id == asset_map["AST-0004"].id)):
            db.add(
                DisposalRequest(
                    asset_id=asset_map["AST-0004"].id,
                    requested_by_id=user_map["hardware@cdipd.local"].id,
                    reason="Repeated battery failures and rising repair cost.",
                    condition="damaged",
                    estimated_residual_value=3000,
                    recommendation="Move to disposal committee review if one more failure occurs.",
                    status="submitted",
                )
            )

        if not db.scalar(select(ProcurementPlan).where(ProcurementPlan.category_name == "Laptop", ProcurementPlan.forecast_period_months == 6)):
            db.add(
                ProcurementPlan(
                    category_name="Laptop",
                    department="CDIPD",
                    forecast_period_months=6,
                    expected_demand=4,
                    replacement_due=2,
                    available_reusable_stock=1,
                    suggested_procurement=5,
                    estimated_budget=420000,
                    created_by_id=user_map["director@cdipd.local"].id,
                )
            )
        if not db.scalar(select(BackupAllocation).where(BackupAllocation.backup_asset_id == asset_map["AST-0005"].id)):
            db.add(
                BackupAllocation(
                    employee_id=user_map["employee@cdipd.local"].id,
                    primary_asset_id=asset_map["AST-0004"].id,
                    backup_asset_id=asset_map["AST-0005"].id,
                    issue_date=date.today() - timedelta(days=1),
                    expected_return_date=date.today() + timedelta(days=5),
                    reason="Temporary replacement while tablet remains under maintenance.",
                    status="active",
                    created_by_id=user_map["hardware@cdipd.local"].id,
                )
            )
            asset_map["AST-0005"].status = "backup_allocated"
        campaign = db.scalar(select(VerificationCampaign).where(VerificationCampaign.campaign_name == "Quarterly Device Sweep"))
        if not campaign:
            campaign = VerificationCampaign(
                campaign_name="Quarterly Device Sweep",
                asset_category="Laptop",
                department="CDIPD",
                location="Head Office",
                start_date=date.today() - timedelta(days=2),
                end_date=date.today() + timedelta(days=5),
                verification_mode="qr_scan",
                status="active",
                created_by_id=user_map["auditor@cdipd.local"].id,
            )
            db.add(campaign)
            db.flush()
        if not db.scalar(select(VerificationExecution).where(VerificationExecution.campaign_id == campaign.id, VerificationExecution.asset_id == asset_map["AST-0002"].id)):
            db.add(
                VerificationExecution(
                    campaign_id=campaign.id,
                    asset_id=asset_map["AST-0002"].id,
                    verified_by_id=user_map["auditor@cdipd.local"].id,
                    result="verified",
                    condition_confirmed="Good",
                    location_confirmed="Innovation Wing",
                    holder_confirmed="Rahul Menon",
                    deviation_notes="",
                )
            )

        if not db.scalar(select(AuditVerification).where(AuditVerification.asset_id == asset_map["AST-0002"].id)):
            db.add(
                AuditVerification(
                    asset_id=asset_map["AST-0002"].id,
                    verified_by_id=user_map["auditor@cdipd.local"].id,
                    result="matched",
                    notes="Verified during quarterly asset spot-check.",
                )
            )
        if not db.scalar(select(AuditVerification).where(AuditVerification.asset_id == asset_map["AST-0005"].id)):
            db.add(
                AuditVerification(
                    asset_id=asset_map["AST-0005"].id,
                    verified_by_id=user_map["auditor@cdipd.local"].id,
                    result="missing",
                    notes="Appliance temporarily moved for event setup; location update pending.",
                )
            )
        db.commit()
        if db.scalar(select(func.count(AuditLog.id))) == 0:
            pass
    finally:
        db.close()
