from passlib.context import CryptContext
from itsdangerous import URLSafeSerializer
from app.core.config import SECRET_KEY

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
serializer = URLSafeSerializer(SECRET_KEY, salt="asset-management")

def verify_password(password: str, password_hash: str) -> bool:
    if password_hash.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            import bcrypt

            return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("utf-8"))
        except Exception:
            return False
    return pwd_context.verify(password, password_hash)
