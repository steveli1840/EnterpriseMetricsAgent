import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.settings import get_settings


DEMO_USERS = {
    "analyst": {"password": "analyst-demo", "role": "analyst", "user_id": "analyst-1"},
    "admin": {"password": "admin-demo", "role": "admin", "user_id": "admin-1"},
}
bearer = HTTPBearer(auto_error=False)


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_token(username: str) -> str:
    settings = get_settings()
    user = DEMO_USERS[username]
    header = _encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _encode(
        json.dumps(
            {
                "sub": user["user_id"],
                "username": username,
                "role": user["role"],
                "exp": int((datetime.now(UTC) + timedelta(minutes=settings.jwt_ttl_minutes)).timestamp()),
            }
        ).encode()
    )
    message = f"{header}.{payload}".encode()
    signature = _encode(
        hmac.new(settings.jwt_secret.get_secret_value().encode(), message, hashlib.sha256).digest()
    )
    return f"{header}.{payload}.{signature}"


def verify_token(token: str) -> dict:
    try:
        header, payload, signature = token.split(".")
        message = f"{header}.{payload}".encode()
        expected = _encode(
            hmac.new(
                get_settings().jwt_secret.get_secret_value().encode(), message, hashlib.sha256
            ).digest()
        )
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        claims = json.loads(_decode(payload))
        if claims["exp"] < datetime.now(UTC).timestamp():
            raise ValueError("expired")
        return claims
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return verify_token(credentials.credentials)

