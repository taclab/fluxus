"""Vérifie le cloisonnement réseau du renderer et sa reprise après un crash.

Aucune cible réelle : un serveur HTTP local joue l'hôte « public » sur 127.0.0.2 et l'hôte privé sur
127.0.0.1 (refusé). Couvre les constats 1 de l'audit du 15/09/2026 et 2 à 4 de celui du 12/11/2026 :
redirection, sous-ressource, popup, WebSocket, changement de résolution DNS, crash de Chromium.

docker run --rm --network none -v $PWD:/audit:ro fluxus-browser python /audit/scripts/check-browser-redirect.py
"""

import asyncio
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/audit/browser")
import server  # noqa: E402

PUBLIC, PRIVATE, PORT = "127.0.0.2", "127.0.0.1", 18080
hits: list[str] = []

PAGES = {
    "/public": f"<title>PUBLIC OK</title>PUBLIC MARKER <img src='http://{PRIVATE}:{PORT}/private.png'>",
    "/popup": (
        "<title>POPUP</title>POPUP MARKER<script>"
        f"window.open('http://{PRIVATE}:{PORT}/private-popup');"
        f"window.open('http://{PUBLIC}:{PORT}/hop-private');"
        "</script>"
    ),
    "/ws": (
        "<title>WS</title>WS MARKER<script>"
        f"new WebSocket('ws://{PRIVATE}:{PORT}/private-ws');"
        f"new WebSocket('ws://{PUBLIC}:{PORT}/public-ws');"
        "</script>"
    ),
}
REDIRECTS = {
    "/start": f"http://{PRIVATE}:{PORT}/private",
    "/hop-private": f"http://{PRIVATE}:{PORT}/private-after-popup-hop",
    "/hop": f"http://{PUBLIC}:{PORT}/public",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        hits.append(f"{self.headers.get('Host')}{self.path}")
        if self.path in REDIRECTS:
            self.send_response(302)
            self.send_header("Location", REDIRECTS[self.path])
            self.end_headers()
            return
        body = PAGES.get(self.path, "<title>LOCAL AUDIT</title>PRIVATE AUDIT MARKER")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(f"<html><body>{body}</body></html>".encode())

    def log_message(self, *args):
        pass


def private_hits() -> list[str]:
    """Requêtes arrivées sous un nom ou un chemin privé (l'hôte « public » ne sert que les pages de test)."""
    return [h for h in hits if h.startswith(PRIVATE) or "private" in h.split("/", 1)[1] or h.startswith("rebind.test")]


async def render(path: str, host: str = PUBLIC):
    hits.clear()
    try:
        result = await server.render(server.RenderRequest(url=f"http://{host}:{PORT}{path}", wait_until="load"))
        await asyncio.sleep(0.5)  # laisse arriver une requête tardive (popup, WebSocket)
        return result
    except Exception as exc:  # noqa: BLE001
        return exc


async def main():
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    server.UPSTREAM_PROXY = None
    server._is_private = lambda ip: ip != PUBLIC

    # Changement de résolution : public pour le préfiltre, privé quand la passerelle se connecte.
    real_getaddrinfo = server.socket.getaddrinfo
    rebind_answers = [PUBLIC]

    def fake_getaddrinfo(host, *args, **kwargs):
        if host == "rebind.test":
            ip = rebind_answers.pop(0) if rebind_answers else PRIVATE
            return [(2, 1, 6, "", (ip, 0))]
        return real_getaddrinfo(host, *args, **kwargs)

    server.socket.getaddrinfo = fake_getaddrinfo

    checks: dict[str, bool] = {}
    await server.start()
    try:
        r = await render("/start")
        checks["redirection privée refusée"] = not private_hits() and "PRIVATE AUDIT MARKER" not in getattr(r, "html", "")

        r = await render("/hop")
        checks["redirection publique suivie"] = "PUBLIC MARKER" in getattr(r, "html", "") and r.final_url.endswith("/public")
        checks["sous-ressource privée bloquée"] = not private_hits()

        r = await render("/popup")
        checks["page avec popup rendue"] = "POPUP MARKER" in getattr(r, "html", "")
        checks["popup privée bloquée (directe et par redirection)"] = not [h for h in private_hits() if "hop-private" not in h]

        r = await render("/ws")
        checks["page avec WebSocket rendue"] = "WS MARKER" in getattr(r, "html", "")
        checks["WebSocket bloqué (privé et public)"] = not [h for h in hits if h.endswith("-ws")]

        r = await render("/public", host="rebind.test")
        checks["changement de résolution DNS refusé"] = not hits and "PUBLIC MARKER" not in getattr(r, "html", "")

        subprocess.run(["pkill", "-9", "-f", "chrom"], check=False)
        await asyncio.sleep(1)
        checks["navigateur vu comme mort"] = not server._browser.is_connected()
        r = await render("/hop")
        checks["rendu après crash de Chromium"] = "PUBLIC MARKER" in getattr(r, "html", "")
        checks["santé après relance"] = (await server.health())["connected"] is True
    finally:
        await server.stop()
        httpd.shutdown()
    print(json.dumps(checks, indent=1, ensure_ascii=False))
    raise SystemExit(0 if all(checks.values()) else 1)


asyncio.run(main())
