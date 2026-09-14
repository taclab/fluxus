import hashlib
import secrets
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from ..config import get_settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def create_access_token() -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": "owner",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.access_token_hours)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


DEVICE_TOKEN_PREFIX = "flx_"


def generate_device_token() -> str:
    return DEVICE_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginRateLimiter:
    """Limiteur en mémoire : N tentatives par fenêtre glissante et par IP."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60) -> None:
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._attempts: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        attempts = [t for t in self._attempts[key] if now - t < self.window]
        self._attempts[key] = attempts
        return len(attempts) < self.max_attempts

    def record_failure(self, key: str) -> None:
        self._attempts[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)


login_limiter = LoginRateLimiter()
