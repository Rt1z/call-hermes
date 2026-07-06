from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from uuid import uuid4

import jwt
from fastapi import HTTPException, status

from app.config import Settings


@dataclass(frozen=True)
class SessionIdentity:
    session_id: str
    device_name: str
    user_id: str = "legacy"
    username: str = "admin"
    role: str = "admin"
    device_id: str = "legacy"


def create_session_token(
    settings: Settings,
    device_name: str | None = None,
    *,
    user_id: str = "legacy",
    username: str = "admin",
    role: str = "admin",
    device_id: str = "legacy",
) -> dict[str, str]:
    session_id = str(uuid4())
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.jwt_ttl_seconds)
    token = jwt.encode(
        {
            "sub": session_id,
            "device": (device_name or "Unknown device")[:200],
            "uid": user_id,
            "username": username,
            "role": role,
            "device_id": device_id,
            "type": "access",
            "exp": expires_at,
            "iat": datetime.now(UTC),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    return {
        "session_id": session_id,
        "token": token,
        "expires_at": expires_at.isoformat(),
    }


def verify_bearer_token(settings: Settings, authorization: str | None) -> str:
    return verify_bearer_identity(settings, authorization).session_id


def verify_bearer_identity(settings: Settings, authorization: str | None) -> SessionIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc
    session_id = payload.get("sub")
    if not isinstance(session_id, str) or not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )
    device_name = payload.get("device")
    if payload.get("type", "access") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    return SessionIdentity(
        session_id=session_id,
        device_name=device_name
        if isinstance(device_name, str) and device_name
        else "Unknown device",
        user_id=str(payload.get("uid") or "legacy"),
        username=str(payload.get("username") or "admin"),
        role=str(payload.get("role") or "user"),
        device_id=str(payload.get("device_id") or "legacy"),
    )
