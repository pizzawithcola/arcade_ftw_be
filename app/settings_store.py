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
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin, require_display
from app.database import get_session
from app.flow import MODULES
from app.models import AppSetting

router = APIRouter(prefix="/settings", tags=["settings"])

MODULES_KEY = "next_modules"


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


class SettingsBody(BaseModel):
    modules: list[str]


@router.get("", summary="The console settings")
async def get_settings(
    _: object = Depends(require_display),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return {"modules": await read_modules(session)}


@router.put("", summary="Replace the console settings")
async def put_settings(
    body: SettingsBody,
    _: object = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Whole-value replacement, so a repeated request lands on the same state.

    Upsert rather than read-then-write: two operators clicking at once would
    otherwise race on a row that may not exist yet.
    """
    modules = clean_modules(body.modules)
    await session.execute(
        pg_insert(AppSetting)
        .values(key=MODULES_KEY, value=json.dumps(modules))
        .on_conflict_do_update(
            index_elements=[AppSetting.key],
            set_={"value": json.dumps(modules)},
        )
    )
    await session.commit()
    return {"modules": modules}
