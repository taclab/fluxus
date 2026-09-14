from fluxus.feeds.opml import build_opml, parse_opml
from fluxus.models import Category, Feed

OPML = b"""<?xml version="1.0"?>
<opml version="2.0"><head><title>x</title></head>
<body>
  <outline text="Tech">
    <outline type="rss" text="Blog A" xmlUrl="https://a.fr/rss" htmlUrl="https://a.fr"/>
    <outline text="Sous-dossier">
      <outline type="rss" text="Blog B" xmlUrl="https://b.fr/rss"/>
    </outline>
  </outline>
  <outline type="rss" text="Solo" xmlUrl="https://c.fr/feed"/>
</body></opml>"""


def test_parse_opml_flattens_nested_categories():
    feeds = parse_opml(OPML)
    assert [(f.title, f.category) for f in feeds] == [("Blog A", "Tech"), ("Blog B", "Sous-dossier"), ("Solo", None)]
    assert feeds[0].site_url == "https://a.fr"


def test_build_opml_roundtrip():
    cats = [Category(id=1, title="Tech & co", position=0)]
    feeds = [
        Feed(id=1, url="https://a.fr/rss", title='Blog "A"', site_url="https://a.fr", category_id=1),
        Feed(id=2, url="https://c.fr/feed", title="Solo", category_id=None),
    ]
    xml = build_opml(cats, feeds)
    reparsed = parse_opml(xml.encode())
    assert {(f.url, f.category) for f in reparsed} == {("https://a.fr/rss", "Tech & co"), ("https://c.fr/feed", None)}
    assert {f.title for f in reparsed} == {'Blog "A"', "Solo"}
