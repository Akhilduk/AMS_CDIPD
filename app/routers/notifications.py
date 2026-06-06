from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Notification, User
from app.services.helpers import get_current_user, get_db, redirect_with_flash, render, require_user

router = APIRouter()


@router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    notifications = db.scalars(
        select(Notification).where(Notification.user_id == current_user.id).order_by(Notification.created_at.desc())
    ).all()
    return render(request, "notifications.html", {"notifications": notifications}, current_user)


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    notification = db.get(Notification, notification_id)
    if not notification or notification.user_id != current_user.id:
        raise HTTPException(status_code=404)
    notification.is_read = True
    notification.read_at = datetime.utcnow()
    db.commit()
    target = notification.link_url if notification.link_url and notification.link_url != "#" else "/notifications"
    return redirect_with_flash(target, "Notification marked as read.")
