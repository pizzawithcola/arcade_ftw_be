from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import cors_origin_list, settings
from app.ws import router as ws_router

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
    return {"status": "ok", "app": settings.app_name}
