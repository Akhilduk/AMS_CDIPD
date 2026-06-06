from passlib.context import CryptContext
from itsdangerous import URLSafeSerializer
from app.core.config import SECRET_KEY

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeSerializer(SECRET_KEY, salt="asset-management")

def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)
