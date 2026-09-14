"""Service de rendu : ouvre une page dans Chromium et renvoie le HTML après exécution du JS.

Usage interne uniquement (réseau compose). POST /render {"url": "..."} -> {"html": "...", "final_url": "..."}
"""

import asyncio
import os
from contextlib import asynccontextmanager

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
                else:
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
