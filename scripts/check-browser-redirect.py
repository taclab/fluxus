"""Vérifie que le renderer refuse une redirection vers une adresse privée (correctif du constat 1 de l'audit du 15/09/2026).

Aucune cible réelle : un serveur HTTP local répond sur 127.0.0.2 (« public » simulé) et redirige vers 127.0.0.1 (refusé).

docker run --rm -v $PWD:/audit:ro fluxus-browser python /audit/scripts/check-browser-redirect.py
"""

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.async_api import async_playwright

sys.path.insert(0, "/audit/browser")
import server  # noqa: E402

hits: list[str] = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        hits.append(f"{self.headers.get('Host')}{self.path}")
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:18080/private")
            self.end_headers()
        elif self.path == "/hop":
            # redirection vers une page publique simulée : doit être suivie
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.2:18080/public")
            self.end_headers()
        elif self.path == "/public":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><title>PUBLIC OK</title><body>PUBLIC MARKER <img src='http://127.0.0.1:18080/private.png'></body></html>")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><title>LOCAL AUDIT</title><body>PRIVATE AUDIT MARKER</body></html>")

    def log_message(self, *args):
        pass


async def main():
    httpd = HTTPServer(("0.0.0.0", 18080), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    server.host_is_public = lambda host: host == "127.0.0.2"
    results = {}
    async with async_playwright() as pw:
        server._browser = await pw.chromium.launch(args=["--no-sandbox", "--no-proxy-server"])
        try:
            try:
                result = await server.render(server.RenderRequest(url="http://127.0.0.2:18080/start", wait_until="load"))
                results["private_content_returned"] = "PRIVATE AUDIT MARKER" in result.html
                results["blocked_render_error"] = None
            except Exception as exc:  # noqa: BLE001
                results["private_content_returned"] = False
                results["blocked_render_error"] = str(exc)[:120]
            results["private_target_requested"] = any("127.0.0.1" in h for h in hits)
            hits.clear()
            result = await server.render(server.RenderRequest(url="http://127.0.0.2:18080/hop", wait_until="load"))
            results["public_redirect_followed"] = "PUBLIC MARKER" in result.html
            results["final_url"] = result.final_url
            results["private_subresource_requested"] = any("127.0.0.1" in h for h in hits)
        finally:
            await server._browser.close()
            httpd.shutdown()
    print(json.dumps(results, indent=1))
    ok = (
        not results["private_content_returned"]
        and not results["private_target_requested"]
        and results["public_redirect_followed"]
        and not results["private_subresource_requested"]
        and results["final_url"].endswith("/public")
    )
    raise SystemExit(0 if ok else 1)


asyncio.run(main())
