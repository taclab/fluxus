import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__
from .auth import router as auth_router
from .backup.router import router as backup_router
from .config import get_settings, validate_runtime_config
from .db import get_engine
from .entries.router import router as entries_router
from .feeds.router import router as feeds_router
from .maintenance import Busy
from .newsletters.router import inbound_router as newsletters_inbound_router
from .newsletters.router import router as newsletters_router
from .settings_router import router as settings_router
from .smartfeeds.router import router as smartfeeds_router
from .summarize.router import router as summarize_router
from .sync.router import router as sync_router

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_config(get_settings())
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

# Politique de sécurité du contenu : scripts et polices locaux uniquement (le script de thème est
# un fichier externe pour cette raison), images et médias distants en https, iframes vidéo connues.
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",  # attributs style= des barres et dégradés React
        # http: pour les favicons de vieux sites (bloqués de toute façon par le navigateur en contenu mixte sur https)
        "img-src 'self' https: http: data: blob:",
        "media-src 'self' https:",
        "font-src 'self' data:",  # Vite inline les très petits sous-ensembles de police en data:
        "connect-src 'self'",
        "frame-src https://www.youtube.com https://www.youtube-nocookie.com https://player.vimeo.com https://www.dailymotion.com",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

# Le service worker obéit à sa propre CSP, celle des en-têtes de sw.js : ses fetch() relèvent de
# connect-src. Workbox y met en cache les images distantes (runtimeCaching de vite.config.ts) ; avec
# le connect-src 'self' de la page, chacune serait refusée et les images disparaîtraient dès que le
# service worker contrôle la page. Il garde des scripts locaux uniquement.
SW_CSP = "default-src 'self'; script-src 'self'; connect-src 'self' https: http:; object-src 'none'; base-uri 'self'"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path == "/sw.js":
            response.headers.setdefault("Content-Security-Policy", SW_CSP)
        elif not request.url.path.startswith("/api/"):
            response.headers.setdefault("Content-Security-Policy", CSP)
        return response


app.add_middleware(SecurityHeadersMiddleware)


@app.exception_handler(Busy)
async def busy_handler(_: Request, exc: Busy) -> JSONResponse:
    # Import de sauvegarde en cours : une mise en file ou une génération viserait un identifiant réattribué.
    return JSONResponse({"detail": str(exc)}, status_code=503)

# En développement le front Vite tourne sur un autre port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        # WebView Capacitor : `androidScheme: https` donne l'origine https://localhost.
        "https://localhost",
        "capacitor://localhost",
        "http://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(backup_router)
app.include_router(feeds_router)
app.include_router(newsletters_router)
app.include_router(newsletters_inbound_router)
app.include_router(entries_router)
app.include_router(settings_router)
app.include_router(smartfeeds_router)
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

    static_root = static_dir.resolve()
    # Coquille de la PWA : jamais servie depuis un cache HTTP sans revalidation, sinon un navigateur ou un
    # reverse proxy peut figer une ancienne version (le service worker ne verrait pas la mise à jour).
    # Les fichiers de /assets portent une empreinte dans leur nom et restent cachables.
    no_cache = {"Cache-Control": "no-cache"}
    shell_files = {"index.html", "sw.js", "registerSW.js", "manifest.webmanifest"}

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str, request: Request):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if full_path:
            # Résolution complète puis vérification d'appartenance : refuse toute traversée (`..`, encodée ou non).
            candidate = (static_root / full_path).resolve()
            if candidate.is_relative_to(static_root) and candidate.is_file():
                headers = no_cache if candidate.parent == static_root and candidate.name in shell_files else None
                return FileResponse(candidate, headers=headers)
        return FileResponse(static_root / "index.html", headers=no_cache)
