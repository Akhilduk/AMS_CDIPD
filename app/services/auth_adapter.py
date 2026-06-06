from abc import ABC, abstractmethod
from typing import Optional
from itsdangerous import BadSignature
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import AUTH_ADAPTER, SESSION_COOKIE
from app.core.security import serializer, verify_password
from app.models.models import User


class AuthAdapter(ABC):
    @abstractmethod
    def authenticate(self, db: Session, email: str, password: str) -> Optional[User]:
        pass

    @abstractmethod
    def get_current_user(self, request: Request, db: Session) -> Optional[User]:
        pass


class LocalAuthAdapter(AuthAdapter):
    def authenticate(self, db: Session, email: str, password: str) -> Optional[User]:
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    def get_current_user(self, request: Request, db: Session) -> Optional[User]:
        data = getattr(request.state, "session_payload", None)
        if not data:
            raw = request.cookies.get(SESSION_COOKIE)
            if not raw:
                return None
            try:
                data = serializer.loads(raw)
            except BadSignature:
                return None
        return db.get(User, data.get("user_id"))


class HRMSSSOAdapter(AuthAdapter):
    def authenticate(self, db: Session, email: str, password: str) -> Optional[User]:
        raise NotImplementedError("HRMS SSO login is not configured. Set AUTH_ADAPTER=hrms_sso and implement the adapter.")

    def get_current_user(self, request: Request, db: Session) -> Optional[User]:
        data = getattr(request.state, "session_payload", None)
        if not data:
            raw = request.cookies.get(SESSION_COOKIE)
            if not raw:
                return None
            try:
                data = serializer.loads(raw)
            except BadSignature:
                return None
        return db.get(User, data.get("user_id"))


def get_auth_adapter() -> AuthAdapter:
    if AUTH_ADAPTER == "hrms_sso":
        return HRMSSSOAdapter()
    return LocalAuthAdapter()
