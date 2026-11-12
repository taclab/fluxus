"""Service de rendu : ouvre une page dans Chromium et renvoie le HTML après exécution du JS.

Usage interne uniquement (réseau compose). POST /render {"url": "..."} -> {"html": "...", "final_url": "..."}

Deux barrières réseau, de la plus forte à la plus fine :
- la passerelle de sortie (`_gateway_client`) : Chromium n'a pas d'autre chemin vers le réseau. Elle
  résout le nom, refuse toute adresse non publique et se connecte à l'adresse qu'elle vient de
  valider. Popups, WebSocket, service workers et changement de résolution DNS y passent tous ;
- le filtre de requêtes (`route_handler`) : types de ressources, traceurs, suivi des redirections.
"""

import asyncio
import base64
import ipaddress
import logging
import os
import socket
import time
from contextlib import asynccontextmanager, suppress
from urllib.parse import unquote, urljoin, urlsplit

from fastapi import FastAPI, HTTPException
from playwright.async_api import Browser, Playwright, async_playwright
from pydantic import BaseModel, HttpUrl

log = logging.getLogger("fluxus.browser")

RENDER_TIMEOUT_MS = int(os.environ.get("RENDER_TIMEOUT_MS", "25000"))
MAX_CONCURRENCY = int(os.environ.get("RENDER_CONCURRENCY", "2"))

BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "websocket"}
BLOCKED_HOST_FRAGMENTS = (
    "googletagmanager", "google-analytics", "doubleclick", "facebook.net", "hotjar",
    "criteo", "outbrain", "taboola", "adsystem", "adnxs", "scorecardresearch",
)

_pw: Playwright | None = None
_browser: Browser | None = None
_gateway: asyncio.Server | None = None
_launch_lock = asyncio.Lock()
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

# Sauts de redirection suivis à la main pour une même requête (chaque destination est revalidée).
MAX_REDIRECT_HOPS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}

# Noms des services Compose : jamais joignables depuis une page rendue.
INTERNAL_HOSTS = {"localhost", "postgres", "api", "worker", "browser"}

# Proxy sortant imposé par l'environnement (proxy d'entreprise injecté par Docker) : la passerelle
# valide l'adresse puis lui confie la connexion. Lu à chaque connexion, modifiable par les tests.
UPSTREAM_PROXY: str | None = (
    os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or None
)

GATEWAY_HEAD_TIMEOUT = 15
GATEWAY_CONNECT_TIMEOUT = 15
# Durée de vie du préfiltre : il évite une résolution par sous-ressource, la passerelle revalide.
PREFILTER_TTL = 60
_prefilter: dict[str, tuple[float, bool]] = {}


def _is_private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return True
    # `not is_global` couvre ce que les autres drapeaux laissent passer (100.64.0.0/10 : CGNAT, Tailscale).
    return (
        a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
        or a.is_reserved or a.is_unspecified or not a.is_global
    )


def resolve_public(host: str) -> str | None:
    """Adresse à joindre si l'hôte ne résout que vers des adresses publiques, sinon None."""
    host = host.lower().rstrip(".")
    if not host or host in INTERNAL_HOSTS or host.endswith((".local", ".localhost", ".internal")):
        return None
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return None
    ips = [i[4][0] for i in infos]
    if not ips or any(_is_private(ip) for ip in ips):
        return None
    return ips[0]


def host_is_public(host: str) -> bool:
    """Préfiltre du `route_handler`, mémorisé une minute. La décision qui compte est celle de la passerelle."""
    now = time.monotonic()
    cached = _prefilter.get(host)
    if cached and now - cached[0] < PREFILTER_TTL:
        return cached[1]
    if len(_prefilter) > 4096:
        _prefilter.clear()
    public = resolve_public(host) is not None
    _prefilter[host] = (now, public)
    return public


def url_is_public(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return parts.scheme in ("data", "blob", "about")  # ressources inline, sans réseau
    return host_is_public(parts.hostname or "")


# --- Passerelle de sortie -----------------------------------------------------------------------


def _split_hostport(target: str, default_port: int) -> tuple[str, int]:
    host, sep, port = target.rpartition(":")
    if not sep or "]" in port:
        host, port = target, str(default_port)
    return host.strip("[]"), int(port)


def _rewrite_head(method: str, target: str, version: str, header_lines: list[str], *, origin_form: bool) -> bytes:
    """En-têtes d'une requête HTTP en clair : une requête par connexion, pour que chacune soit validée."""
    if origin_form:
        parts = urlsplit(target)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    kept = [
        line for line in header_lines
        if line.split(":", 1)[0].strip().lower() not in ("connection", "proxy-connection", "proxy-authorization", "keep-alive")
    ]
    return "\r\n".join([f"{method} {target} {version}", *kept, "Connection: close", "", ""]).encode("latin-1")


def _upstream_auth(proxy) -> list[str]:
    if not proxy.username:
        return []
    token = base64.b64encode(f"{unquote(proxy.username)}:{unquote(proxy.password or '')}".encode()).decode()
    return [f"Proxy-Authorization: Basic {token}"]


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        with suppress(Exception):
            writer.close()


async def _refuse(writer: asyncio.StreamWriter, status: str) -> None:
    with suppress(Exception):
        writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()


async def _gateway_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Proxy HTTP minimal (CONNECT et requêtes en clair) : seul chemin réseau de Chromium."""
    up_writer: asyncio.StreamWriter | None = None
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), GATEWAY_HEAD_TIMEOUT)
        lines = head.decode("latin-1").split("\r\n")
        method, target, version = lines[0].split(" ", 2)
        header_lines = [line for line in lines[1:] if line]
        if method == "CONNECT":
            host, port = _split_hostport(target, 443)
        else:
            parts = urlsplit(target)
            if parts.scheme != "http" or not parts.hostname:
                await _refuse(writer, "400 Bad Request")
                return
            host, port = parts.hostname, parts.port or 80

        ip = await asyncio.to_thread(resolve_public, host)
        if ip is None:
            await _refuse(writer, "403 Forbidden")
            return

        proxy = urlsplit(UPSTREAM_PROXY) if UPSTREAM_PROXY else None
        if proxy and proxy.hostname:
            # Derrière un proxy imposé, c'est lui qui joint la destination : on lui transmet la demande.
            connect_to = (proxy.hostname, proxy.port or 3128)
            auth = _upstream_auth(proxy)
            if method == "CONNECT":
                first = "\r\n".join([lines[0], *header_lines, *auth, "", ""]).encode("latin-1")
            else:
                first = _rewrite_head(method, target, version, [*header_lines, *auth], origin_form=False)
        else:
            connect_to = (ip, port)  # l'adresse validée, pas une nouvelle résolution du nom
            first = b"" if method == "CONNECT" else _rewrite_head(method, target, version, header_lines, origin_form=True)

        try:
            up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(*connect_to), GATEWAY_CONNECT_TIMEOUT)
        except (OSError, TimeoutError):
            await _refuse(writer, "502 Bad Gateway")
            return
        if first:
            up_writer.write(first)
            await up_writer.drain()
        else:
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, ValueError, ConnectionError, OSError):
        pass
    finally:
        for w in (up_writer, writer):
            if w is not None:
                with suppress(Exception):
                    w.close()


# --- Cycle de vie du navigateur ------------------------------------------------------------------


async def _launch() -> Browser:
    assert _pw is not None and _gateway is not None
    port = _gateway.sockets[0].getsockname()[1]
    return await _pw.chromium.launch(
        # `<-loopback>` : même 127.0.0.1 passe par la passerelle, qui le refuse.
        proxy={"server": f"http://127.0.0.1:{port}", "bypass": "<-loopback>"},
        args=[
            "--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu",
            # Rien ne doit sortir en UDP à côté de la passerelle.
            "--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        ],
    )


async def ensure_browser() -> Browser:
    """Navigateur prêt à servir ; relancé s'il est mort (OOM, crash de Chromium)."""
    global _browser
    async with _launch_lock:
        if _browser is None or not _browser.is_connected():
            if _browser is not None:
                log.warning("Chromium déconnecté : relance")
            _browser = await _launch()
        return _browser


async def start() -> None:
    global _pw, _gateway
    _gateway = await asyncio.start_server(_gateway_client, "127.0.0.1", 0)
    _pw = await async_playwright().start()
    await ensure_browser()


async def stop() -> None:
    global _pw, _browser, _gateway
    if _browser is not None:
        with suppress(Exception):
            await _browser.close()
    if _pw is not None:
        await _pw.stop()
    if _gateway is not None:
        _gateway.close()
    _pw = _browser = _gateway = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start()
    yield
    await stop()


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
    """503 si Chromium est mort et ne se relance pas : le HEALTHCHECK doit le voir."""
    try:
        await ensure_browser()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Navigateur indisponible: {exc}") from exc
    return {"status": "ok", "connected": True}


async def _close_websocket(ws) -> None:
    await ws.close()


@app.post("/render", response_model=RenderResponse)
async def render(req: RenderRequest) -> RenderResponse:
    if not await asyncio.to_thread(url_is_public, str(req.url)):
        raise HTTPException(status_code=400, detail="Adresse réseau privée ou interne refusée")
    timeout = req.timeout_ms or RENDER_TIMEOUT_MS
    async with _semaphore:
        try:
            browser = await ensure_browser()
            major = browser.version.split(".", 1)[0]
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    f"Chrome/{major}.0.0.0 Safari/537.36"
                ),
                locale="fr-FR",
                viewport={"width": 1280, "height": 2000},
                java_script_enabled=True,
                service_workers="block",
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Rendu impossible: {exc}") from exc
        try:
            page = await context.new_page()

            final_url = {"value": str(req.url)}

            async def route_handler(route):
                """Toute requête de la page passe ici. Chromium suit les redirections en interne sans
                repasser par cette fonction : on les suit donc à la main avec `route.fetch`
                (sans redirection automatique) et on revalide chaque destination avant de la joindre.
                La page reçoit la réponse finale sous l'URL d'origine."""
                request = route.request
                if request.resource_type in BLOCKED_RESOURCE_TYPES or any(
                    frag in request.url for frag in BLOCKED_HOST_FRAGMENTS
                ):
                    await route.abort()
                    return
                url = request.url
                method = request.method
                post_data = request.post_data_buffer
                for _ in range(MAX_REDIRECT_HOPS + 1):
                    if not await asyncio.to_thread(url_is_public, url):
                        await route.abort("blockedbyclient")
                        return
                    try:
                        response = await route.fetch(url=url, method=method, post_data=post_data, max_redirects=0)
                    except Exception:  # noqa: BLE001  réseau : la ressource échoue comme si elle était injoignable
                        await route.abort("failed")
                        return
                    location = response.headers.get("location")
                    if response.status in REDIRECT_STATUSES and location:
                        url = urljoin(url, location)
                        if response.status in (301, 302, 303) and method != "GET":
                            method, post_data = "GET", None
                        await response.dispose()
                        continue
                    try:
                        is_main = request.is_navigation_request() and request.frame == page.main_frame
                    except Exception:  # noqa: BLE001  requête sans cadre (worker)
                        is_main = False
                    if is_main:
                        final_url["value"] = url
                    await route.fulfill(response=response)
                    return
                await route.abort("failed")

            # Sur le contexte, pas sur la page : une popup ouverte par la page est filtrée elle aussi,
            # puis fermée (elle n'apporte rien au rendu).
            await context.route("**/*", route_handler)
            await context.route_web_socket("**/*", _close_websocket)
            context.on("page", lambda popup: asyncio.ensure_future(popup.close()))
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
                return RenderResponse(html=html, final_url=final_url["value"], title=title)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"Rendu impossible: {exc}") from exc
        finally:
            await context.close()
