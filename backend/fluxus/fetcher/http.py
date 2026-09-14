"""Client HTTP partagé, avec garde anti-SSRF."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from ..config import get_settings


class UnsafeURLError(ValueError):
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


def assert_public_url(url: str) -> str:
    """Refuse les URLs qui ne sont pas http(s) ou qui pointent vers le réseau local."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError("Seuls http et https sont acceptés")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL sans hôte")
    if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".local"):
        raise UnsafeURLError("Hôte local refusé")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Hôte introuvable: {host}") from exc
    for info in infos:
        ip = info[4][0]
        if _is_private_ip(ip):
            raise UnsafeURLError("Adresse réseau privée refusée")
    return url


def make_client(**kwargs) -> httpx.AsyncClient:
    settings = get_settings()
    headers = {"User-Agent": settings.user_agent, "Accept": "*/*"}
    headers.update(kwargs.pop("headers", {}))
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_seconds),
        follow_redirects=True,
        max_redirects=5,
        headers=headers,
        **kwargs,
    )
