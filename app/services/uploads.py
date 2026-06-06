from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.core.config import UPLOAD_ALLOWED_EXTENSIONS, UPLOAD_ALLOWED_MIME_TYPES, UPLOAD_DIR, UPLOAD_MAX_BYTES


def virus_scan_placeholder(path: Path) -> bool:
    """Hook for ClamAV/vendor scanning. Return False to reject infected files."""
    return True


async def store_secure_upload(upload: UploadFile, subdir: str = "general") -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in UPLOAD_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="File extension is not allowed.")
    if (upload.content_type or "").lower() not in UPLOAD_ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail="File type is not allowed.")
    target_dir = UPLOAD_DIR / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{secrets.token_urlsafe(24)}{suffix}"
    size = 0
    with target.open("wb") as out:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > UPLOAD_MAX_BYTES:
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="File exceeds the configured upload size limit.")
            out.write(chunk)
    if not virus_scan_placeholder(target):
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="File failed security scan.")
    return target
