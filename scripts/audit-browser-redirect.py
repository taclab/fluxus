"""Test local du contrôle des redirections du renderer ; aucune cible réelle.

À lancer dans fluxus-browser avec le dépôt monté sous /audit.
La validation accepte 127.0.0.2 comme origine publique simulée et refuse 127.0.0.1.
Les deux adresses appartiennent au conteneur jetable : aucune cible externe.
"""

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.async_api import async_playwright

sys.path.insert(0, "/audit/browser")
import server

hits = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        hits.append(self.path)
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:18080/private")
            self.end_headers()
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
    async with async_playwright() as pw:
        server._browser = await pw.chromium.launch(args=[
            "--no-sandbox", "--no-proxy-server", "--host-resolver-rules=MAP public.test 127.0.0.1",
        ])
        try:
            result = await server.render(server.RenderRequest(url="http://127.0.0.2:18080/start", wait_until="load"))
            leaked = "PRIVATE AUDIT MARKER" in result.html
            print(json.dumps({"private_redirect_reached": "/private" in hits,
                "private_content_returned": leaked, "final_url": result.final_url, "requests": hits}))
            assert leaked, "Défaut non reproduit, revoir le constat"
        finally:
            await server._browser.close()
            httpd.shutdown()


asyncio.run(main())
