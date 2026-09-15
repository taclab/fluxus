import pytest

from fluxus.fetcher.http import UnsafeURLError, assert_public_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/health",
        "http://localhost/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "ftp://exemple.fr/",
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data",
    ],
)
def test_private_and_unsupported_urls_rejected(url):
    with pytest.raises(UnsafeURLError):
        assert_public_url(url)


def test_public_url_accepted():
    assert assert_public_url("https://1.1.1.1/") == "https://1.1.1.1/"


async def test_safe_get_blocks_redirect_to_private_network():
    import httpx

    from fluxus.fetcher.http import safe_get

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:5432/"})
        return httpx.Response(200, text="interne")

    # public.example doit résoudre : on remplace la résolution DNS par une IP publique.
    import fluxus.fetcher.http as http_mod

    original = http_mod.socket.getaddrinfo
    http_mod.socket.getaddrinfo = lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))] if host == "public.example" else original(host, *a, **k)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(UnsafeURLError):
                await safe_get(client, "http://public.example/feed")
    finally:
        http_mod.socket.getaddrinfo = original


async def test_safe_get_follows_public_redirects():
    import httpx

    import fluxus.fetcher.http as http_mod
    from fluxus.fetcher.http import safe_get

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, text="ok")

    original = http_mod.socket.getaddrinfo
    http_mod.socket.getaddrinfo = lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            r = await safe_get(client, "http://public.example/old")
            assert r.status_code == 200 and str(r.url).endswith("/new")
    finally:
        http_mod.socket.getaddrinfo = original


def test_rate_limiter_global_cap_defeats_ip_spoofing():
    from fluxus.auth.security import LoginRateLimiter

    limiter = LoginRateLimiter(max_attempts=5, window_seconds=60, global_max=8)
    for i in range(8):
        key = f"10.0.0.{i}"  # une IP différente à chaque essai, comme un X-Forwarded-For forgé
        assert limiter.check(key)
        limiter.record_failure(key)
    assert not limiter.check("10.0.0.99")


async def test_safe_get_gzip_body_is_decoded_once():
    """Constat 2 de l'audit du 15/09 : le corps décodé en flux ne doit pas garder Content-Encoding."""
    import gzip

    import httpx

    import fluxus.fetcher.http as http_mod
    from fluxus.fetcher.http import safe_get

    payload = b"<rss>audit</rss>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=gzip.compress(payload), headers={"content-encoding": "gzip", "content-type": "application/rss+xml"})

    original = http_mod.socket.getaddrinfo
    http_mod.socket.getaddrinfo = lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            r = await safe_get(client, "http://public.example/feed.xml")
            assert r.status_code == 200
            assert r.content == payload
            assert "content-encoding" not in r.headers
            assert r.headers["content-length"] == str(len(payload))
            assert r.headers["content-type"] == "application/rss+xml"
    finally:
        http_mod.socket.getaddrinfo = original
