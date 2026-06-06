from __future__ import annotations
from typing import Optional
from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(120), unique=True)
    employee_code: Mapped[str] = mapped_column(String(40), unique=True)
    role: Mapped[str] = mapped_column(String(40))
    department: Mapped[str] = mapped_column(String(80), default="CDIPD")
    designation: Mapped[str] = mapped_column(String(120), default="")
    mobile: Mapped[str] = mapped_column(String(20), default="")
    reporting_manager: Mapped[str] = mapped_column(String(120), default="")
    employee_type: Mapped[str] = mapped_column(String(40), default="Regular")
    joining_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    multiple_roles_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user_roles: Mapped[list["UserRole"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user_roles: Mapped[list["UserRole"]] = relationship(back_populates="role", cascade="all, delete-orphan")
    role_permissions: Mapped[list["RolePermission"]] = relationship(back_populates="role", cascade="all, delete-orphan")


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    role_permissions: Mapped[list["RolePermission"]] = relationship(back_populates="permission", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("module", "action", name="uq_permissions_module_action"),)


class UserRole(Base):
    __tablename__ = "user_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="user_roles")
    role: Mapped[Role] = relationship(back_populates="user_roles")

    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id"))
    granted: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    role: Mapped[Role] = relationship(back_populates="role_permissions")
    permission: Mapped[Permission] = relationship(back_populates="role_permissions")

    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_permission"),)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class PolicyMaster(Base):
    __tablename__ = "policy_masters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_code: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_role: Mapped[str] = mapped_column(String(40), default="hr_admin")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    versions: Mapped[list["PolicyVersion"]] = relationship(back_populates="policy_master")


class PolicyTemplate(Base):
    __tablename__ = "policy_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_code: Mapped[str] = mapped_column(String(40), unique=True)
    template_name: Mapped[str] = mapped_column(String(160))
    template_type: Mapped[str] = mapped_column(String(80))
    html_content: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PolicyVersion(Base):
    __tablename__ = "policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_master_id: Mapped[Optional[int]] = mapped_column(ForeignKey("policy_masters.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(160))
    version: Mapped[str] = mapped_column(String(30))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="draft")
    effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    approval_remarks: Mapped[str] = mapped_column(Text, default="")
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    policy_master: Mapped[Optional[PolicyMaster]] = relationship(back_populates="versions")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    model: Mapped[str] = mapped_column(String(120))
    serial_number: Mapped[str] = mapped_column(String(120), unique=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    status: Mapped[str] = mapped_column(String(40), default="available")
    specification: Mapped[str] = mapped_column(Text, default="")
    purchase_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    warranty_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    qr_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    category: Mapped[Category] = relationship()
    vendor: Mapped[Vendor] = relationship()
    location: Mapped[Location] = relationship()


class Allocation(Base):
    __tablename__ = "allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    policy_id: Mapped[Optional[int]] = mapped_column(ForeignKey("policies.id"), nullable=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="submitted")
    remarks: Mapped[str] = mapped_column(Text, default="")
    declaration_text: Mapped[str] = mapped_column(
        Text,
        default="I confirm that I have received the listed asset in good order from CDIPD, that I have read and understood the Asset Usage Policy, and that I agree to use the asset only for official purposes, protect it from loss or damage, report issues immediately, and return it when required.",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    asset: Mapped[Asset] = relationship(foreign_keys=[asset_id])
    employee: Mapped[User] = relationship(foreign_keys=[employee_id])
    requested_by: Mapped[User] = relationship(foreign_keys=[requested_by_id])
    approved_by: Mapped[Optional[User]] = relationship(foreign_keys=[approved_by_id])
    policy: Mapped[Optional[PolicyVersion]] = relationship()


class OTPChallenge(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    allocation_id: Mapped[int] = mapped_column(ForeignKey("allocations.id"))
    otp_code: Mapped[str] = mapped_column(String(12))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SignedDocument(Base):
    __tablename__ = "signed_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    allocation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("allocations.id"), nullable=True)
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assets.id"), nullable=True)
    policy_id: Mapped[Optional[int]] = mapped_column(ForeignKey("policies.id"), nullable=True)
    template_id: Mapped[Optional[int]] = mapped_column(ForeignKey("policy_templates.id"), nullable=True)
    template_code: Mapped[str] = mapped_column(String(40), default="")
    template_name: Mapped[str] = mapped_column(String(160), default="")
    document_number: Mapped[str] = mapped_column(String(50), unique=True)
    file_path: Mapped[str] = mapped_column(String(255))
    hash_sha256: Mapped[str] = mapped_column(String(64))
    evidence: Mapped[str] = mapped_column(Text)
    source_module: Mapped[str] = mapped_column(String(40), default="allocation")
    source_reference: Mapped[str] = mapped_column(String(80), default="")
    rendered_content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    allocation: Mapped[Optional[Allocation]] = relationship()
    employee: Mapped[Optional[User]] = relationship(foreign_keys=[employee_id])
    asset: Mapped[Optional[Asset]] = relationship(foreign_keys=[asset_id])
    policy: Mapped[Optional[PolicyVersion]] = relationship(foreign_keys=[policy_id])
    template: Mapped[Optional[PolicyTemplate]] = relationship()


class MaintenanceTicket(Base):
    __tablename__ = "maintenance_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    raised_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assigned_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    issue_type: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="open")
    resolution_notes: Mapped[str] = mapped_column(Text, default="")
    replacement_asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assets.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    asset: Mapped[Asset] = relationship(foreign_keys=[asset_id])
    raised_by: Mapped[User] = relationship(foreign_keys=[raised_by_id])
    assigned_to: Mapped[Optional[User]] = relationship(foreign_keys=[assigned_to_id])
    replacement_asset: Mapped[Optional[Asset]] = relationship(foreign_keys=[replacement_asset_id])


class RepairEntry(Base):
    __tablename__ = "repair_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("maintenance_tickets.id"), nullable=True)
    repair_type: Mapped[str] = mapped_column(String(80))
    repair_mode: Mapped[str] = mapped_column(String(40), default="internal")
    warranty_claim: Mapped[bool] = mapped_column(Boolean, default=False)
    repair_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    repair_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    repair_cost: Mapped[int] = mapped_column(Integer, default=0)
    diagnosis: Mapped[str] = mapped_column(Text, default="")
    resolution: Mapped[str] = mapped_column(Text, default="")
    final_condition: Mapped[str] = mapped_column(String(40), default="serviceable")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    asset: Mapped[Asset] = relationship()
    ticket: Mapped[Optional[MaintenanceTicket]] = relationship()
    created_by: Mapped[User] = relationship()


class ReplacementRecord(Base):
    __tablename__ = "replacement_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    old_asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    new_asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("maintenance_tickets.id"), nullable=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="initiated")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    old_asset: Mapped[Asset] = relationship(foreign_keys=[old_asset_id])
    new_asset: Mapped[Asset] = relationship(foreign_keys=[new_asset_id])
    ticket: Mapped[Optional[MaintenanceTicket]] = relationship()
    employee: Mapped[User] = relationship(foreign_keys=[employee_id])
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])


class BackupAllocation(Base):
    __tablename__ = "backup_allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    primary_asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    backup_asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    issue_date: Mapped[date] = mapped_column(Date)
    expected_return_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="active")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    employee: Mapped[User] = relationship(foreign_keys=[employee_id])
    primary_asset: Mapped[Asset] = relationship(foreign_keys=[primary_asset_id])
    backup_asset: Mapped[Asset] = relationship(foreign_keys=[backup_asset_id])
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])


class TravelRequest(Base):
    __tablename__ = "travel_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    destination_country: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(Text)
    departure_date: Mapped[date] = mapped_column(Date)
    return_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(40), default="submitted")
    director_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    asset: Mapped[Asset] = relationship()
    employee: Mapped[User] = relationship()


class ReturnRequest(Base):
    __tablename__ = "return_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    initiated_by_role: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    condition_notes: Mapped[str] = mapped_column(Text, default="")
    qr_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(40), default="initiated")
    liability_amount: Mapped[int] = mapped_column(Integer, default=0)
    exit_clearance_status: Mapped[str] = mapped_column(String(40), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    asset: Mapped[Asset] = relationship()
    employee: Mapped[User] = relationship()


class LiabilityRecord(Base):
    __tablename__ = "liability_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assets.id"), nullable=True)
    liability_type: Mapped[str] = mapped_column(String(80))
    amount: Mapped[int] = mapped_column(Integer, default=0)
    recovery_status: Mapped[str] = mapped_column(String(40), default="pending")
    remarks: Mapped[str] = mapped_column(Text, default="")
    hr_approval_status: Mapped[str] = mapped_column(String(40), default="pending")
    source_reference: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    employee: Mapped[User] = relationship()
    asset: Mapped[Optional[Asset]] = relationship()


class DisposalRequest(Base):
    __tablename__ = "disposal_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    condition: Mapped[str] = mapped_column(String(40), default="damaged")
    estimated_residual_value: Mapped[int] = mapped_column(Integer, default=0)
    recommendation: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="draft")
    certificate_number: Mapped[str] = mapped_column(String(80), default="")
    disposal_method: Mapped[str] = mapped_column(String(80), default="")
    disposal_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    vendor_agency: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    asset: Mapped[Asset] = relationship()
    requested_by: Mapped[User] = relationship()


class ProcurementPlan(Base):
    __tablename__ = "procurement_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_name: Mapped[str] = mapped_column(String(80))
    department: Mapped[str] = mapped_column(String(80), default="CDIPD")
    forecast_period_months: Mapped[int] = mapped_column(Integer, default=3)
    expected_demand: Mapped[int] = mapped_column(Integer, default=0)
    replacement_due: Mapped[int] = mapped_column(Integer, default=0)
    available_reusable_stock: Mapped[int] = mapped_column(Integer, default=0)
    suggested_procurement: Mapped[int] = mapped_column(Integer, default=0)
    estimated_budget: Mapped[int] = mapped_column(Integer, default=0)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    created_by: Mapped[User] = relationship()


class AuditVerification(Base):
    __tablename__ = "audit_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    verified_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    result: Mapped[str] = mapped_column(String(40))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    asset: Mapped[Asset] = relationship()
    verified_by: Mapped[User] = relationship()


class VerificationCampaign(Base):
    __tablename__ = "verification_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_name: Mapped[str] = mapped_column(String(160))
    asset_category: Mapped[str] = mapped_column(String(80), default="")
    department: Mapped[str] = mapped_column(String(80), default="")
    location: Mapped[str] = mapped_column(String(80), default="")
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    verification_mode: Mapped[str] = mapped_column(String(40), default="qr_scan")
    status: Mapped[str] = mapped_column(String(40), default="planned")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    created_by: Mapped[User] = relationship()


class VerificationExecution(Base):
    __tablename__ = "verification_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("verification_campaigns.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    verified_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    result: Mapped[str] = mapped_column(String(40), default="verified")
    condition_confirmed: Mapped[str] = mapped_column(String(80), default="")
    location_confirmed: Mapped[str] = mapped_column(String(80), default="")
    holder_confirmed: Mapped[str] = mapped_column(String(120), default="")
    deviation_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    campaign: Mapped[VerificationCampaign] = relationship()
    asset: Mapped[Asset] = relationship()
    verified_by: Mapped[User] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(120))
    actor_name: Mapped[str] = mapped_column(String(120))
    reference_id: Mapped[str] = mapped_column(String(80))
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    event_code: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(Text)
    link_url: Mapped[str] = mapped_column(String(255), default="#")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="notifications")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token: Mapped[str] = mapped_column(String(120), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="reset_tokens")


class WorkflowSetting(Base):
    __tablename__ = "workflow_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_name: Mapped[str] = mapped_column(String(80), unique=True)
    hr_approval_required: Mapped[bool] = mapped_column(Boolean, default=True)
    signing_required: Mapped[bool] = mapped_column(Boolean, default=True)
    director_approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    return_check_required: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NumberingSetting(Base):
    __tablename__ = "numbering_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_prefix: Mapped[str] = mapped_column(String(20), default="AST")
    document_prefix: Mapped[str] = mapped_column(String(30), default="CDIPD-SIGN")
    ticket_prefix: Mapped[str] = mapped_column(String(20), default="TKT")
    return_prefix: Mapped[str] = mapped_column(String(20), default="RET")
    policy_prefix: Mapped[str] = mapped_column(String(20), default="POL")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

ROLE_MENUS = {
    "super_admin": ["dashboard", "users", "masters", "policies", "reports", "audit", "disposal", "procurement"],
    "hardware_admin": ["dashboard", "assets", "allocations", "backup", "maintenance", "repairs", "replacement", "verification", "returns", "scanner", "disposal"],
    "hr_admin": ["dashboard", "allocations", "backup", "policies", "verification", "returns", "liabilities", "reports"],
    "employee": ["dashboard", "assets", "maintenance", "travel", "requests", "returns"],
    "director": ["dashboard", "travel", "procurement", "management/reports"],
    "auditor": ["dashboard", "assets", "documents", "audit"]
}
