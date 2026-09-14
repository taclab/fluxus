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
