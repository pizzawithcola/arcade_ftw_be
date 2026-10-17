"""Login and bearer tokens.

Deliberately small: tokens are HMAC-signed payloads, so there is no session
table to keep in sync and no server state to lose on restart.

Two kinds of identity:
  * players - a nickname plus the event they are attending, because a name is
    all it takes to walk up to a kiosk and get going, and the event is what
    keeps the scoreboards apart;
  * admin - username and password, because the person driving the show is the
    only one who should pause or jump the flow.

Admins have two scopes. A `display` token exists so the big screen can be left
logged in all day without handing whoever walks past it the ability to pause the
show: it reads everything and controls nothing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.events import EVENTS, EVENT_KEYS
from app.models import Player

router = APIRouter(prefix="/auth", tags=["auth"])

ADMIN_SUBJECT = "admin"
PLAYER_PREFIX = "player:"

SCOPE_FULL = "full"
SCOPE_DISPLAY = "display"

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Claims:
    subject: str
    scope: str
    expires_at: int


# ─── tokens ───────────────────────────────────────────────────────────────────
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def create_token(subject: str, scope: str = SCOPE_FULL) -> str:
    payload = json.dumps(
        {
            "sub": subject,
            "scope": scope,
            "exp": int(time.time()) + settings.token_ttl_seconds,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    body = _b64(payload)
    signature = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def decode_token(token: str) -> Claims | None:
    """Return the claims, or None if the token is forged, malformed or expired."""
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
    expires_at = int(payload.get("exp", 0))
    if expires_at < time.time():
        return None
    subject = payload.get("sub")
    if not isinstance(subject, str):
        return None
    return Claims(
        subject=subject,
        scope=payload.get("scope", SCOPE_FULL),
        expires_at=expires_at,
    )



# ─── dependencies ─────────────────────────────────────────────────────────────
async def current_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Claims | None:
    if credentials is None:
        return None
    return decode_token(credentials.credentials)


async def current_subject(claims: Claims | None = Depends(current_claims)) -> str | None:
    return claims.subject if claims else None


async def require_display(claims: Claims | None = Depends(current_claims)) -> Claims:
    """Read-only screens: a full or a display-scoped admin token."""
    if claims is None or claims.subject != ADMIN_SUBJECT:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims


async def require_admin(claims: Claims | None = Depends(current_claims)) -> Claims:
    """Controls. A display token is refused even though its subject is admin."""
    if claims is None or claims.subject != ADMIN_SUBJECT or claims.scope != SCOPE_FULL:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims


# ─── endpoints ────────────────────────────────────────────────────────────────
class PlayerLogin(BaseModel):
    nickname: str = Field(min_length=1, max_length=64)
    event: str
    # The caller's existing token, if any. It is how a returning player is told
    # apart from a different person who happens to share the nickname.
    token: str | None = None


class AdminLogin(BaseModel):
    username: str
    password: str
    # "display" issues a read-only token for the big screen.
    scope: str = SCOPE_FULL


@router.get("/events", summary="The events a player can sign in for")
async def list_events() -> dict:
    """Drives the login dropdown, so the page never offers an unsupported value."""
    return {"events": EVENTS}


async def _player_from_token(token: str | None, event: str, session: AsyncSession) -> Player | None:
    """Resolve the token back to a player, but only within the same event."""
    if not token:
        return None
    claims = decode_token(token)
    if claims is None or not claims.subject.startswith(PLAYER_PREFIX):
        return None
    try:
        player_id = int(claims.subject[len(PLAYER_PREFIX) :])
    except ValueError:
        return None
    player = await session.get(Player, player_id)
    if player is None or player.event != event:
        return None
    return player


async def _free_nickname(nickname: str, event: str, session: AsyncSession) -> str:
    """First free `nickname`, `nickname (2)`, `nickname (3)`…

    Players bring their own device now, so two people really can both be "Alex".
    Numbering keeps them apart instead of silently merging two scoreboards.
    """
    candidate = nickname
    suffix = 2
    while (
        await session.execute(
            select(Player.id).where(Player.nickname == candidate, Player.event == event)
        )
    ).scalar_one_or_none() is not None:
        room = f" ({suffix})"
        base = nickname[: 64 - len(room)]
        candidate = f"{base}{room}"
        suffix += 1
    return candidate


@router.post("/player", summary="Log in with a nickname and an event")
async def login_player(
    body: PlayerLogin,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Players just need a name; the same name within one event maps to one row.

    A returning device sends its old token and gets the same player back. Anything
    else with a taken nickname is a different person and is given a number.
    """
    nickname = body.nickname.strip()
    if not nickname:
        raise HTTPException(status_code=422, detail="nickname required")
    if body.event not in EVENT_KEYS:
        # The dropdown is built from the same list, so this only happens to a
        # client that skipped it - which is exactly who needs refusing.
        raise HTTPException(status_code=422, detail=f"unknown event: {body.event}")

    player = await _player_from_token(body.token, body.event, session)
    if player is None:
        player = Player(
            nickname=await _free_nickname(nickname, body.event, session),
            event=body.event,
        )
        session.add(player)
        await session.commit()
        await session.refresh(player)

    return {
        "token": create_token(f"{PLAYER_PREFIX}{player.id}"),
        "player_id": player.id,
        "nickname": player.nickname,
        "event": player.event,
    }


@router.post("/admin", summary="Log in as the show operator")
async def login_admin(body: AdminLogin) -> dict:
    username_ok = hmac.compare_digest(body.username.encode(), settings.admin_username.encode())
    password_ok = hmac.compare_digest(body.password.encode(), settings.admin_password.encode())
    if not (username_ok and password_ok):
        raise HTTPException(status_code=401, detail="invalid credentials")
    scope = SCOPE_DISPLAY if body.scope == SCOPE_DISPLAY else SCOPE_FULL
    return {"token": create_token(ADMIN_SUBJECT, scope), "subject": ADMIN_SUBJECT, "scope": scope}


@router.get("/me", summary="Who is this token?")
async def me(
    claims: Claims | None = Depends(current_claims),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Resolve the token to a person, so a reloaded page knows who it belongs to."""
    if claims is None:
        raise HTTPException(status_code=401, detail="not signed in")

    body: dict = {
        "subject": claims.subject,
        "is_admin": claims.subject == ADMIN_SUBJECT,
        "scope": claims.scope,
        "can_control": claims.subject == ADMIN_SUBJECT and claims.scope == SCOPE_FULL,
    }
    if claims.subject.startswith(PLAYER_PREFIX):
        try:
            player = await session.get(Player, int(claims.subject[len(PLAYER_PREFIX) :]))
        except ValueError:
            player = None
        if player is not None:
            body["player_id"] = player.id
            body["nickname"] = player.nickname
            body["event"] = player.event
    return body
