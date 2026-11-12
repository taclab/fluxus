from datetime import UTC, datetime

from fluxus.fetcher.parse import normalize_url, parse_feed

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel>
  <title>Exemple</title>
  <link>https://exemple.fr/</link>
  <item>
    <title>Premier article</title>
    <link>https://exemple.fr/a?utm_source=rss&amp;id=1</link>
    <guid isPermaLink="false">guid-1</guid>
    <pubDate>Mon, 01 Sep 2026 10:00:00 GMT</pubDate>
    <description><![CDATA[<p>Bonjour <script>alert(1)</script><img src="/img.jpg"></p>]]></description>
    <media:thumbnail url="https://exemple.fr/thumb.jpg"/>
  </item>
  <item>
    <title>Sans guid ni date</title>
    <link>/relatif</link>
  </item>
</channel>
</rss>"""

ATOM_YT = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <title>Chaine</title>
  <link rel="alternate" href="https://www.youtube.com/channel/UCxyz"/>
  <entry>
    <id>yt:video:dQw4w9WgXcQ</id>
    <yt:videoId>dQw4w9WgXcQ</yt:videoId>
    <title>Video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=dQw4w9WgXcQ"/>
    <published>2026-09-01T10:00:00+00:00</published>
  </entry>
</feed>"""


def test_normalize_url_strips_tracking_and_resolves_relative():
    assert normalize_url("https://Exemple.fr/a?utm_source=x&id=1&fbclid=y") == "https://exemple.fr/a?id=1"
    assert normalize_url("/rel", "https://exemple.fr/base/") == "https://exemple.fr/rel"
    assert normalize_url("javascript:alert(1)") is None
    assert normalize_url(None) is None


def test_parse_rss_entries():
    parsed = parse_feed(RSS, "https://exemple.fr/rss.xml")
    assert parsed.title == "Exemple"
    assert parsed.site_url == "https://exemple.fr/"
    assert not parsed.is_youtube
    assert len(parsed.entries) == 2

    first = parsed.entries[0]
    assert first.guid == "guid-1"
    assert first.url == "https://exemple.fr/a?id=1"
    assert first.published_at == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    assert "<script" not in (first.summary_html or "")
    assert 'src="https://exemple.fr/img.jpg"' in (first.summary_html or "")
    assert first.meta["cover"] == "https://exemple.fr/thumb.jpg"

    second = parsed.entries[1]
    assert second.url == "https://exemple.fr/relatif"
    assert second.guid == "https://exemple.fr/relatif"
    assert second.hash != first.hash


def test_media_content_without_type_is_a_cover():
    rss = b"""<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>t</title>
    <item><title>a</title><link>https://x.fr/a</link>
      <media:content width="644" height="322" url="https://img.x.fr/photo.jpg"/></item>
    <item><title>b</title><link>https://x.fr/b</link>
      <media:content url="https://x.fr/video.mp4" type="video/mp4"/></item>
    </channel></rss>"""
    parsed = parse_feed(rss, "https://x.fr/rss")
    assert parsed.entries[0].meta["cover"] == "https://img.x.fr/photo.jpg"
    assert "cover" not in parsed.entries[1].meta


def test_parse_youtube_feed_sets_video_id():
    parsed = parse_feed(ATOM_YT, "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz")
    assert parsed.is_youtube
    assert parsed.entries[0].meta["youtube_id"] == "dQw4w9WgXcQ"


def test_parse_garbage_reports_error():
    parsed = parse_feed(b"<html><body>pas un flux</body></html>", "https://exemple.fr/")
    assert parsed.entries == []
    assert parsed.bozo_error


def test_oversized_fields_are_truncated_or_dropped():
    long_author = "A" * 400
    long_url = "https://exemple.fr/" + "x" * 2100
    rss = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>a</title><link>{long_url}</link><author>{long_author}</author></item>
    </channel></rss>""".encode()
    parsed = parse_feed(rss, "https://exemple.fr/rss")
    e = parsed.entries[0]
    assert e.url is None
    assert e.author is not None and len(e.author) == 300
    assert len(e.guid) <= 2048


def test_nul_characters_are_stripped():
    """Audit du 12/11, constat 31 : `&#0;` dans un titre faisait rejeter tout le lot du flux par PostgreSQL."""
    import json

    from fluxus.fetcher.parse import parse_feed

    raw = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Flux&#0;</title><link>https://nul.example/</link>
    <item><title>a&#0;b</title><guid>g&#0;1</guid><link>https://nul.example/1</link><author>x&#0;y</author><description>d&#0;e</description></item>
    <item><title>sain</title><guid>g2</guid><link>https://nul.example/2</link></item></channel></rss>"""
    parsed = parse_feed(raw, "https://nul.example/rss")
    assert len(parsed.entries) == 2
    dumped = json.dumps([parsed.title, [vars(e) for e in parsed.entries]], default=str)
    assert "\x00" not in dumped and "\\u0000" not in dumped


def test_oversized_validators_are_dropped():
    from fluxus.fetcher.service import _validator

    assert _validator('W/"abc"', 500) == 'W/"abc"'
    assert _validator("x" * 501, 500) is None
    assert _validator(None, 500) is None
