import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import PasswordResetToken, User
from app.core.config import IS_PRODUCTION, SESSION_COOKIE, SESSION_MAX_AGE_SECONDS
from app.services.helpers import authenticate_user, create_notification, get_current_user, get_db, hash_password, log_event, redirect_with_flash, render, serializer

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = authenticate_user(db, email, password)
    if not user:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user:
            user.failed_login_attempts += 1
            db.commit()
            log_event(db, "auth", "login_failed", user.full_name, user.email, new_value=f"attempts={user.failed_login_attempts}")
        return render(request, "login.html", {"error": "Invalid credentials"})
    if not user.active:
        log_event(db, "auth", "inactive_login_blocked", user.full_name, user.email, new_value="inactive_user")
        return render(request, "login.html", {"error": "User is inactive. Contact the system administrator."})
    user.failed_login_attempts = 0
    user.last_login = datetime.utcnow()
    db.commit()
    log_event(db, "auth", "login_success", user.full_name, user.email, new_value=f"last_login={user.last_login.isoformat()}")
    response = redirect_with_flash("/dashboard", f"Welcome back, {user.full_name}.", "success")
    response.set_cookie(SESSION_COOKIE, serializer.dumps({"user_id": user.id, "issued_at": int(datetime.utcnow().timestamp()), "last_activity": int(datetime.utcnow().timestamp())}), max_age=SESSION_MAX_AGE_SECONDS, expires=SESSION_MAX_AGE_SECONDS, httponly=True, samesite="lax", secure=IS_PRODUCTION)
    return response


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "forgot_password.html", {"error": None, "reset_link": None})


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    reset_link = None
    if user and user.active:
        token = secrets.token_urlsafe(32)
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token=token,
                expires_at=datetime.utcnow() + timedelta(hours=1),
            )
        )
        db.flush()
        reset_link = f"/reset-password/{token}"
        create_notification(
            db,
            user.id,
            "password_reset_requested",
            "Password reset requested",
            f"A password reset request was generated for {user.email}. Use the reset link to continue.",
            reset_link,
        )
        db.commit()
        log_event(db, "auth", "password_reset_requested", user.full_name, user.email, new_value=reset_link)
    message = "If the email exists and is active, a reset link has been generated."
    return render(request, "forgot_password.html", {"error": None, "reset_link": reset_link, "message": message})


@router.get("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_page(token: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    reset_token = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token == token))
    if not reset_token or reset_token.used_at or reset_token.expires_at < datetime.utcnow():
        return render(request, "reset_password.html", {"error": "Reset link is invalid or expired.", "token": None})
    return render(request, "reset_password.html", {"error": None, "token": token})


@router.post("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password(
    token: str,
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    reset_token = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token == token))
    if not reset_token or reset_token.used_at or reset_token.expires_at < datetime.utcnow():
        return render(request, "reset_password.html", {"error": "Reset link is invalid or expired.", "token": None})
    if len(new_password) < 8:
        return render(request, "reset_password.html", {"error": "Password must be at least 8 characters.", "token": token})
    if new_password != confirm_password:
        return render(request, "reset_password.html", {"error": "Password confirmation does not match.", "token": token})
    user = reset_token.user
    user.password_hash = hash_password(new_password)
    user.failed_login_attempts = 0
    reset_token.used_at = datetime.utcnow()
    db.commit()
    log_event(db, "auth", "password_reset_completed", user.full_name, user.email, new_value=f"token_used_at={reset_token.used_at.isoformat()}")
    return redirect_with_flash("/login", "Password reset completed. Sign in with the new password.")


@router.get("/logout")
async def logout():
    response = redirect_with_flash("/login", "You have been signed out.", "success")
    response.delete_cookie(SESSION_COOKIE)
    return response
