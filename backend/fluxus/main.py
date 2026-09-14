import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .auth import router as auth_router
from .config import get_settings
from .db import get_engine
from .entries.router import router as entries_router
from .feeds.router import router as feeds_router
from .settings_router import router as settings_router
from .summarize.router import router as summarize_router
from .sync.router import router as sync_router

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = get_engine()
    yield
    await engine.dispose()


app = FastAPI(
    title="Fluxus",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)

# En développement le front Vite tourne sur un autre port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "capacitor://localhost", "http://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(feeds_router)
app.include_router(entries_router)
app.include_router(settings_router)
app.include_router(summarize_router)
app.include_router(sync_router)


@app.get("/api/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "version": __version__}


# --- Front statique (build Vite) avec repli SPA -----------------------------
static_dir = Path(get_settings().static_dir)
if static_dir.is_dir():
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str, request: Request):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = static_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")
