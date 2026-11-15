"""Console settings that outlive the browser.

One row per knob. The only knob today is the module set for the next room.

It lives here rather than in `localStorage` because the picker is an operational
decision, not a personal preference: the operator who sets the Singapore cut on
one laptop expects the console on the venue machine to build the same show, and
a cleared cache must not silently restore the full one.

A room's own set stays where it belongs - in `rooms.modules`, fixed at creation.
This is only what the *next* room will be built from.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin, require_display
from app.database import get_session
from app.events import EVENT_KEYS
from app.flow import MODULES
from app.models import AppSetting

router = APIRouter(prefix="/settings", tags=["settings"])

MODULES_KEY = "next_modules"
EVENT_KEY = "active_event"


def clean_modules(modules: list[str]) -> list[str]:
    """Validate a requested set, in the show's own order.

    The same two refusals as the room endpoint: an unknown key would hide a game
    by accident, and an empty set would build a show with no nodes at all.
    """
    unknown = sorted(set(modules) - set(MODULES))
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown module(s): {', '.join(unknown)}")
    chosen = [module for module in MODULES if module in set(modules)]
    if not chosen:
        raise HTTPException(status_code=422, detail="a room needs at least one module")
    return chosen


def _decode(raw: str | None) -> list[str]:
    """The stored set, or every module.

    A missing row, unreadable JSON or a set that no longer validates all fall
    back to the full show: a corrupt setting must not stop a room being created.
    """
    if raw is None:
        return list(MODULES)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return list(MODULES)
    if not isinstance(parsed, list):
        return list(MODULES)
    chosen = [module for module in MODULES if module in parsed]
    return chosen or list(MODULES)


async def read_modules(session: AsyncSession) -> list[str]:
    raw = await session.scalar(select(AppSetting.value).where(AppSetting.key == MODULES_KEY))
    return _decode(raw)


async def read_active_event(session: AsyncSession) -> str | None:
    """Which event the shared boards rank, or None for every event at once.

    Stored here rather than in the browser because it is a decision about what
    the room is shown, not a preference of one laptop: the wall, the waiting
    room and the public page all read the same value.
    """
    raw = await session.scalar(select(AppSetting.value).where(AppSetting.key == EVENT_KEY))
    if raw is None:
        return None
    key = raw.strip().strip('"')
    # A key that is no longer offered would filter every board down to nothing,
    # so it degrades to "everything" instead.
    return key if key in EVENT_KEYS else None


async def _store(session: AsyncSession, key: str, value: str) -> None:
    await session.execute(
        pg_insert(AppSetting)
        .values(key=key, value=value)
        .on_conflict_do_update(index_elements=[AppSetting.key], set_={"value": value})
    )


class SettingsBody(BaseModel):
    """A partial update: fields left out keep their stored value.

    Two knobs are set from two different pages (the module picker and the event
    switch), so a whole-body replacement would have each one wiping the other.
    """

    modules: list[str] | None = None
    # `None` means "every event"; a key must be one the login screen offers.
    active_event: str | None = None


@router.get("", summary="The console settings")
async def get_settings(
    _: object = Depends(require_display),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return {
        "modules": await read_modules(session),
        "active_event": await read_active_event(session),
    }


@router.put("", summary="Change the console settings")
async def put_settings(
    body: SettingsBody,
    _: object = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Only the fields in the request change.

    Upsert rather than read-then-write: two operators clicking at once would
    otherwise race on a row that may not exist yet.
    """
    given = body.model_fields_set
    if "modules" in given:
        if body.modules is None:
            raise HTTPException(status_code=422, detail="modules cannot be null")
        await _store(session, MODULES_KEY, json.dumps(clean_modules(body.modules)))
    if "active_event" in given:
        if body.active_event is None:
            # An empty value clears the filter rather than meaning "no boards".
            await session.execute(delete(AppSetting).where(AppSetting.key == EVENT_KEY))
        else:
            key = body.active_event.strip()
            if key not in EVENT_KEYS:
                raise HTTPException(status_code=422, detail=f"unknown event: {key}")
            await _store(session, EVENT_KEY, key)
    await session.commit()
    return {
        "modules": await read_modules(session),
        "active_event": await read_active_event(session),
    }
