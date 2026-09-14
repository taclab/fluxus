from fluxus.extract.clean import html_to_text, sanitize_html


def test_sanitize_removes_scripts_and_keeps_structure():
    html = '<div onclick="x()"><h2>Titre</h2><script>evil()</script><p style="color:red">Texte <a href="/lien">lien</a></p></div>'
    out = sanitize_html(html, base_url="https://site.fr/article")
    assert "script" not in out
    assert "onclick" not in out
    assert "style=" not in out
    assert '<a href="https://site.fr/lien"' in out
    assert 'rel="noopener noreferrer nofollow"' in out


def test_sanitize_promotes_lazy_images_and_absolutizes():
    html = '<img data-src="/images/a.jpg" srcset="/a-2x.jpg 2x" alt="A">'
    out = sanitize_html(html, base_url="https://site.fr/")
    assert 'src="https://site.fr/images/a.jpg"' in out
    assert "srcset" not in out


def test_sanitize_filters_iframes_by_host():
    good = '<iframe src="https://www.youtube.com/embed/abc"></iframe>'
    bad = '<iframe src="https://evil.example/x"></iframe>'
    assert "youtube.com/embed/abc" in sanitize_html(good)
    assert "iframe" not in sanitize_html(bad)


def test_html_to_text():
    text = html_to_text("<p>Un &amp; deux</p><p>Trois<br>Quatre</p>")
    assert text == "Un & deux\n\nTrois\nQuatre"
    assert html_to_text(None) == ""


def test_first_image_from_extracted_html():
    from fluxus.extract.jobs import first_image

    assert first_image('<p>x</p><img src="https://a.fr/i.jpg" alt=""><img src="https://a.fr/j.jpg">') == "https://a.fr/i.jpg"
    assert first_image('<img src="data:image/png;base64,xx">') is None
    assert first_image(None) is None
