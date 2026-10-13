import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import auth, game
from app.config import cors_origin_list, settings
from app.database import engine as db_engine
from app.engine import engine
from app.manager import manager
from app.ws import router as ws_router

logger = logging.getLogger(__name__)


async def broadcast_state() -> None:
    await manager.broadcast({"type": "state", "payload": engine.snapshot()})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Every state change reaches every screen through this one listener.
    engine.subscribe(broadcast_state)
    yield


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    # Under /api like everything else, so the single CloudFront behavior proxies
    # them too. At the root they fall through to the S3 origin instead, where the
    # SPA rewrite turns a missing object into index.html - which is how /docs
    # managed to answer 200 while serving the React app rather than Swagger.
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

origins = cors_origin_list()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=bool(origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Everything the game itself serves lives under /api, so CloudFront needs a
# single behavior to proxy it and a new endpoint never needs an infra change.
# /health stays at the root because the load balancer probes it directly.
api = APIRouter(prefix="/api")
api.include_router(auth.router)
api.include_router(game.router)
api.include_router(ws_router)
app.include_router(api)


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
        async with db_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any failure means the database is unusable
        logger.warning("database health check failed: %s", exc)
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"database": "ok"}
