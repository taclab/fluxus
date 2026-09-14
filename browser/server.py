"""Service de rendu : ouvre une page dans Chromium et renvoie le HTML après exécution du JS.

Usage interne uniquement (réseau compose). POST /render {"url": "..."} -> {"html": "...", "final_url": "..."}
"""

import asyncio
import ipaddress
import os
import socket
from contextlib import asynccontextmanager
from functools import lru_cache
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from playwright.async_api import Browser, async_playwright
from pydantic import BaseModel, HttpUrl

RENDER_TIMEOUT_MS = int(os.environ.get("RENDER_TIMEOUT_MS", "25000"))
MAX_CONCURRENCY = int(os.environ.get("RENDER_CONCURRENCY", "2"))

BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "websocket"}
BLOCKED_HOST_FRAGMENTS = (
    "googletagmanager", "google-analytics", "doubleclick", "facebook.net", "hotjar",
    "criteo", "outbrain", "taboola", "adsystem", "adnxs", "scorecardresearch",
)

_browser: Browser | None = None
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

# Noms des services Compose : jamais joignables depuis une page rendue.
INTERNAL_HOSTS = {"localhost", "postgres", "api", "worker", "browser"}


def _is_private(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified


@lru_cache(maxsize=4096)
def host_is_public(host: str) -> bool:
    """Vrai si l'hôte résout uniquement vers des adresses publiques. Mis en cache : appelé pour chaque sous-ressource."""
    host = host.lower().rstrip(".")
    if not host or host in INTERNAL_HOSTS or host.endswith((".local", ".localhost", ".internal")):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return bool(infos) and all(not _is_private(i[4][0]) for i in infos)


def url_is_public(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return parts.scheme in ("data", "blob", "about")  # ressources inline, sans réseau
    return host_is_public(parts.hostname or "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _browser
    pw = await async_playwright().start()
    _browser = await pw.chromium.launch(
        args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu"]
    )
    yield
    await _browser.close()
    await pw.stop()


app = FastAPI(title="Fluxus browser", lifespan=lifespan)


class RenderRequest(BaseModel):
    url: HttpUrl
    wait_until: str = "networkidle"
    timeout_ms: int | None = None


class RenderResponse(BaseModel):
    html: str
    final_url: str
    title: str | None = None


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "connected": _browser is not None and _browser.is_connected()}


@app.post("/render", response_model=RenderResponse)
async def render(req: RenderRequest) -> RenderResponse:
    assert _browser is not None
    if not await asyncio.to_thread(url_is_public, str(req.url)):
        raise HTTPException(status_code=400, detail="Adresse réseau privée ou interne refusée")
    timeout = req.timeout_ms or RENDER_TIMEOUT_MS
    async with _semaphore:
        context = await _browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0 Safari/537.36"
            ),
            locale="fr-FR",
            viewport={"width": 1280, "height": 2000},
            java_script_enabled=True,
        )
        try:
            page = await context.new_page()

            async def route_handler(route):
                request = route.request
                if request.resource_type in BLOCKED_RESOURCE_TYPES or any(
                    frag in request.url for frag in BLOCKED_HOST_FRAGMENTS
                ):
                    await route.abort()
                    return
                # Redirections et sous-ressources : chaque requête sortante est revalidée (anti-SSRF).
                if not await asyncio.to_thread(url_is_public, request.url):
                    await route.abort("blockedbyclient")
                    return
                await route.continue_()

            await page.route("**/*", route_handler)
            try:
                await page.goto(str(req.url), wait_until="domcontentloaded", timeout=timeout)
                try:
                    await page.wait_for_load_state(req.wait_until, timeout=min(timeout, 10000))
                except Exception:  # noqa: BLE001  réseau jamais silencieux : on prend ce qu'on a
                    pass
                # Laisse le temps aux frameworks d'hydrater le DOM.
                await page.wait_for_timeout(500)
                html = await page.content()
                title = await page.title()
                return RenderResponse(html=html, final_url=page.url, title=title)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"Rendu impossible: {exc}") from exc
        finally:
            await context.close()
