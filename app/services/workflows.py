from __future__ import annotations

from collections.abc import Mapping


class TransitionError(ValueError):
    pass


ASSET_TRANSITIONS: dict[str, set[str]] = {
    "available": {"pending_allocation", "under_maintenance"},
    "pending_allocation": {"pending_signature", "available"},
    "pending_signature": {"active", "available"},
    "active": {"under_maintenance", "return_pending", "active_abroad", "retired"},
    "backup_allocated": {"available", "under_maintenance"},
    "under_maintenance": {"active", "retired"},
    "return_pending": {"available", "liability_hold", "damaged", "lost", "retired"},
    "damaged": {"retired", "liability_hold", "disposal_candidate"},
    "active_abroad": {"active", "return_pending"},
    "liability_hold": {"available", "retired", "disposal_candidate"},
    "retired": {"disposal_candidate"},
    "disposal_candidate": {"scrapped"},
    "scrapped": set(),
    "lost": set(),
}

ALLOCATION_TRANSITIONS: dict[str, set[str]] = {
    "submitted": {"pending_signature", "sent_back", "rejected", "cancelled"},
    "sent_back": {"pending_signature", "rejected", "cancelled"},
    "pending_signature": {"signed", "cancelled"},
    "signed": {"returned"},
    "rejected": set(),
    "cancelled": set(),
    "returned": set(),
}

TICKET_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress", "closed", "replaced", "rejected"},
    "in_progress": {"closed", "replaced", "rejected"},
    "replaced": set(),
    "closed": set(),
    "rejected": set(),
}

RETURN_TRANSITIONS: dict[str, set[str]] = {
    "initiated": {"verified"},
    "verified": set(),
}

EXIT_CLEARANCE_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"completed", "liability_open"},
    "liability_open": {"completed"},
    "completed": set(),
}

TRAVEL_TRANSITIONS: dict[str, set[str]] = {
    "submitted": {"approved", "rejected"},
    "approved": set(),
    "rejected": set(),
}


def ensure_transition(current: str | None, target: str, allowed_map: Mapping[str, set[str]], label: str) -> None:
    source = (current or "").strip().lower()
    destination = target.strip().lower()
    if source == destination:
        return
    allowed = allowed_map.get(source)
    if allowed is None:
        raise TransitionError(f"Unknown {label} status '{current}'.")
    if destination not in allowed:
        raise TransitionError(f"Invalid {label} transition: {current} -> {target}.")


def apply_transition(entity, attr_name: str, target: str, allowed_map: Mapping[str, set[str]], label: str) -> str:
    current = getattr(entity, attr_name)
    ensure_transition(current, target, allowed_map, label)
    normalized = target.strip().lower()
    setattr(entity, attr_name, normalized)
    return normalized
