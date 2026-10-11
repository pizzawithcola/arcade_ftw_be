"""Login and bearer tokens.

Deliberately small: tokens are HMAC-signed payloads, so there is no session
table to keep in sync and no server state to lose on restart.

Two kinds of identity:
  * players - just a nickname, which is the whole point of walking up to a
    kiosk and getting going;
  * admin - username and password, because the person driving the show is the
    only one who should pause or jump the flow.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.models import Player

router = APIRouter(prefix="/auth", tags=["auth"])

ADMIN_SUBJECT = "admin"
PLAYER_PREFIX = "player:"

_bearer = HTTPBearer(auto_error=False)


# ─── tokens ───────────────────────────────────────────────────────────────────
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def create_token(subject: str) -> str:
    payload = json.dumps(
        {"sub": subject, "exp": int(time.time()) + settings.token_ttl_seconds},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    body = _b64(payload)
    signature = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def decode_token(token: str) -> str | None:
    """Return the subject, or None if the token is forged, malformed or expired."""
    try:
        body, signature = token.split(".", 1)
    except ValueError:
        return None

    expected = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest()
    try:
        provided = _unb64(signature)
    except ValueError:
        return None
    if not hmac.compare_digest(provided, expected):
        return None

    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < time.time():
        return None
    return payload.get("sub")


# ─── dependencies ─────────────────────────────────────────────────────────────
async def current_subject(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str | None:
    if credentials is None:
        return None
    return decode_token(credentials.credentials)


async def require_admin(subject: str | None = Depends(current_subject)) -> str:
    if subject != ADMIN_SUBJECT:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return subject


# ─── endpoints ────────────────────────────────────────────────────────────────
class PlayerLogin(BaseModel):
    nickname: str = Field(min_length=1, max_length=64)


class AdminLogin(BaseModel):
    username: str
    password: str


@router.post("/player", summary="Log in with a nickname")
async def login_player(
    body: PlayerLogin,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Players just need a name; the same nickname always maps to one row."""
    nickname = body.nickname.strip()
    if not nickname:
        raise HTTPException(status_code=422, detail="nickname required")

    player = (
        await session.execute(select(Player).where(Player.nickname == nickname))
    ).scalar_one_or_none()
    if player is None:
        player = Player(nickname=nickname)
        session.add(player)
        await session.commit()
        await session.refresh(player)

    return {
        "token": create_token(f"{PLAYER_PREFIX}{player.id}"),
        "player_id": player.id,
        "nickname": player.nickname,
    }


@router.post("/admin", summary="Log in as the show operator")
async def login_admin(body: AdminLogin) -> dict:
    username_ok = hmac.compare_digest(body.username.encode(), settings.admin_username.encode())
    password_ok = hmac.compare_digest(body.password.encode(), settings.admin_password.encode())
    if not (username_ok and password_ok):
        raise HTTPException(status_code=401, detail="invalid credentials")
    return {"token": create_token(ADMIN_SUBJECT), "subject": ADMIN_SUBJECT}


@router.get("/me", summary="Who is this token?")
async def me(subject: str | None = Depends(current_subject)) -> dict:
    if subject is None:
        raise HTTPException(status_code=401, detail="not signed in")
    return {"subject": subject, "is_admin": subject == ADMIN_SUBJECT}
