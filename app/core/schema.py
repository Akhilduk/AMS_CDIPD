from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

REQUIRED_COLUMNS: dict[str, dict[str, str]] = {
    "users": {"is_deleted": "BOOLEAN DEFAULT false", "deleted_at": "DATETIME", "deleted_by": "VARCHAR(120) DEFAULT ''", "delete_reason": "TEXT DEFAULT ''"},
    "roles": {"is_deleted": "BOOLEAN DEFAULT false", "deleted_at": "DATETIME", "deleted_by": "VARCHAR(120) DEFAULT ''", "delete_reason": "TEXT DEFAULT ''"},
    "categories": {"is_deleted": "BOOLEAN DEFAULT false", "deleted_at": "DATETIME", "deleted_by": "VARCHAR(120) DEFAULT ''", "delete_reason": "TEXT DEFAULT ''"},
    "vendors": {"is_deleted": "BOOLEAN DEFAULT false", "deleted_at": "DATETIME", "deleted_by": "VARCHAR(120) DEFAULT ''", "delete_reason": "TEXT DEFAULT ''"},
    "locations": {"is_deleted": "BOOLEAN DEFAULT false", "deleted_at": "DATETIME", "deleted_by": "VARCHAR(120) DEFAULT ''", "delete_reason": "TEXT DEFAULT ''"},
    "assets": {"acquisition_value": "INTEGER DEFAULT 0", "is_deleted": "BOOLEAN DEFAULT false", "deleted_at": "DATETIME", "deleted_by": "VARCHAR(120) DEFAULT ''", "delete_reason": "TEXT DEFAULT ''"},
    "policy_masters": {"is_deleted": "BOOLEAN DEFAULT false", "deleted_at": "DATETIME", "deleted_by": "VARCHAR(120) DEFAULT ''", "delete_reason": "TEXT DEFAULT ''"},
    "policy_templates": {"is_deleted": "BOOLEAN DEFAULT false", "deleted_at": "DATETIME", "deleted_by": "VARCHAR(120) DEFAULT ''", "delete_reason": "TEXT DEFAULT ''"},
    "policies": {"is_deleted": "BOOLEAN DEFAULT false", "deleted_at": "DATETIME", "deleted_by": "VARCHAR(120) DEFAULT ''", "delete_reason": "TEXT DEFAULT ''"},
    "workflow_settings": {"is_deleted": "BOOLEAN DEFAULT false", "deleted_at": "DATETIME", "deleted_by": "VARCHAR(120) DEFAULT ''", "delete_reason": "TEXT DEFAULT ''"},
    "disposal_requests": {"committee_remarks": "TEXT DEFAULT ''", "approval_date": "DATE", "ewaste_vendor_details": "TEXT DEFAULT ''", "certificate_path": "VARCHAR(255) DEFAULT ''"},
    "procurement_plans": {"workflow_status": "VARCHAR(60) DEFAULT 'draft_forecast'", "approved_quantity": "INTEGER DEFAULT 0", "approval_reason": "TEXT DEFAULT ''", "priority": "VARCHAR(40) DEFAULT 'medium'", "financial_year": "VARCHAR(20) DEFAULT ''", "director_remarks": "TEXT DEFAULT ''"},
    "audit_logs": {"severity": "VARCHAR(40) DEFAULT 'info'", "entity_type": "VARCHAR(80) DEFAULT ''"},
    "notifications": {"delivery_status": "VARCHAR(40) DEFAULT 'in_app'"},
}


def apply_compatibility_migrations(engine: Engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in REQUIRED_COLUMNS.items():
            if table not in existing_tables:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table)}
            for column_name, ddl in columns.items():
                if column_name not in existing_columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_name} {ddl}"))
