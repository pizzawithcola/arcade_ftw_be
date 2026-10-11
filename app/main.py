import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import cors_origin_list, settings
from app.database import engine
from app.ws import router as ws_router

logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)

origins = cors_origin_list()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=bool(origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ws_router)


@app.get("/health")
async def health() -> dict:
    """Liveness only, deliberately with no database access.

    The ALB polls this, so a database blip here would kill and restart the task
    and wipe every in-memory game room. Use /health/db to check the database.
    """
    return {"status": "ok", "app": settings.app_name}


@app.get("/health/db")
async def health_db() -> dict:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any failure means the database is unusable
        logger.warning("database health check failed: %s", exc)
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"database": "ok"}
