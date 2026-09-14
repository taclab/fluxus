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


def create_access_token(password_version: str = "") -> str:
    """`password_version` : horodatage du dernier changement de mot de passe ; un changement invalide les JWT antérieurs."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": "owner",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.access_token_hours)).timestamp()),
        "typ": "access",
        "pv": password_version,
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
    """Limiteur en mémoire : N tentatives par fenêtre glissante et par IP, plus un plafond global.

    Le plafond global (toutes IP confondues) rend inutile la falsification d'adresse : sur une
    instance mono-utilisateur, personne n'a de raison légitime d'échouer 20 fois par minute.
    """

    GLOBAL_KEY = "*"

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60, global_max: int = 20) -> None:
        self.max_attempts = max_attempts
        self.global_max = global_max
        self.window = window_seconds
        self._attempts: dict[str, list[float]] = defaultdict(list)

    def _recent(self, key: str) -> list[float]:
        now = time.monotonic()
        attempts = [t for t in self._attempts[key] if now - t < self.window]
        self._attempts[key] = attempts
        return attempts

    def check(self, key: str) -> bool:
        return len(self._recent(key)) < self.max_attempts and len(self._recent(self.GLOBAL_KEY)) < self.global_max

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        self._attempts[key].append(now)
        self._attempts[self.GLOBAL_KEY].append(now)

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)


login_limiter = LoginRateLimiter()
