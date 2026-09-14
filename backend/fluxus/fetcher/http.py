"""Client HTTP partagé, avec garde anti-SSRF."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from ..config import get_settings


class UnsafeURLError(ValueError):
    pass


class ResponseTooLarge(httpx.HTTPError):
    pass


def _is_private_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _check_scheme_and_host(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError("Seuls http et https sont acceptés")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL sans hôte")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeURLError("Hôte local refusé")
    return host


def _check_infos(infos, host: str) -> None:
    if not infos:
        raise UnsafeURLError(f"Hôte introuvable: {host}")
    for info in infos:
        if _is_private_ip(info[4][0]):
            raise UnsafeURLError("Adresse réseau privée refusée")


def assert_public_url(url: str) -> str:
    """Refuse les URLs qui ne sont pas http(s) ou qui pointent vers le réseau local (version synchrone)."""
    host = _check_scheme_and_host(url)
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Hôte introuvable: {host}") from exc
    _check_infos(infos, host)
    return url


async def assert_public_url_async(url: str) -> str:
    """Même contrôle sans bloquer la boucle d'événements pendant la résolution DNS."""
    host = _check_scheme_and_host(url)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Hôte introuvable: {host}") from exc
    _check_infos(infos, host)
    return url


MAX_REDIRECTS = 5


def make_client(**kwargs) -> httpx.AsyncClient:
    """Client sans suivi automatique des redirections : passer par `safe_get`, qui revalide chaque saut."""
    settings = get_settings()
    headers = {"User-Agent": settings.user_agent, "Accept": "*/*"}
    headers.update(kwargs.pop("headers", {}))
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_seconds),
        follow_redirects=False,
        headers=headers,
        **kwargs,
    )


DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_TOTAL_TIMEOUT = 60.0


async def safe_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    total_timeout: float = DEFAULT_TOTAL_TIMEOUT,
    **kwargs,
) -> httpx.Response:
    """GET durci : redirections revalidées à chaque saut, corps lu en flux et borné, délai global.

    - Sans revalidation, un site public pourrait répondre `302 Location: http://postgres:5432/`.
    - Le timeout httpx est par opération de lecture : un serveur au goutte-à-goutte tiendrait une
      connexion des heures ; `total_timeout` borne l'ensemble.
    - Le corps est lu par morceaux et abandonné au-delà de `max_bytes` (au lieu d'être chargé entier).
    """

    async def run() -> httpx.Response:
        current = await assert_public_url_async(url)
        for _ in range(MAX_REDIRECTS + 1):
            request = client.build_request("GET", current, **kwargs)
            response = await client.send(request, stream=True)
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                location = response.headers["location"]
                await response.aclose()
                current = await assert_public_url_async(str(response.url.join(location)))
                continue
            chunks: list[bytes] = []
            total = 0
            try:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ResponseTooLarge(f"Réponse de plus de {max_bytes // (1024 * 1024)} Mo refusée")
                    chunks.append(chunk)
            finally:
                await response.aclose()
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                content=b"".join(chunks),
                request=request,
                default_encoding=response.default_encoding,
            )
        raise httpx.TooManyRedirects(f"Plus de {MAX_REDIRECTS} redirections", request=request)

    try:
        return await asyncio.wait_for(run(), timeout=total_timeout)
    except TimeoutError as exc:
        raise httpx.ReadTimeout(f"Délai global de {int(total_timeout)} s dépassé") from exc
