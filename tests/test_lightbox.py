"""Image viewing is delegated to the vendored Lightbox3 library.

The library owns the overlay, prev/next, Escape, focus and touch gestures. What
still belongs to this project is the wiring: the grid must emit links the
library recognises, the caption must carry the metadata and the download link
the library has no equivalent for, and the bundle must actually be loaded.
"""

from __future__ import annotations

import re
from pathlib import Path

VENDOR = Path("static/vendor/lightbox3")
APP_JS = Path("static/js/app.js").read_text()


def test_the_library_is_vendored_rather_than_pulled_from_a_cdn():
    """A CDN outage would take image viewing with it on a self-hosted box."""
    for name in ("lightbox3.css", "lightbox3.min.js", "LICENSE", "README.md"):
        assert (VENDOR / name).is_file(), name
    assert "MIT" in (VENDOR / "LICENSE").read_text()


def test_the_page_loads_the_library_with_cache_busting(client):
    body = client.get("/").text
    for name in ("lightbox3.css", "lightbox3.min.js"):
        pattern = r"/static/vendor/lightbox3/" + name.replace(".", r"\.") + r"\?v=[0-9a-f]{10}"
        assert re.search(pattern, body), name


def test_the_library_is_parsed_before_the_app_script_initialises_it(client):
    body = client.get("/").text
    assert body.index("lightbox3.min.js") < body.index("js/app.js")


def test_the_hand_rolled_dialog_is_gone(client):
    """It was replaced wholesale; leaving it would double up the overlay."""
    body = client.get("/").text
    assert "<dialog" not in body
    assert 'id="lightbox-download"' not in body
    assert 'id="lightbox-prev"' not in body


def test_its_dead_stylesheet_rules_went_too():
    style = Path("static/css/style.css").read_text()
    assert ".lightbox {" not in style
    assert ".lightbox-nav" not in style


def test_results_are_rendered_as_links_the_library_recognises():
    assert 'data-lightbox="frames"' in APP_JS
    # A real href means the grid still works with JavaScript disabled.
    assert "'<a href=\"' + escapeAttr(image.url)" in APP_JS
    assert "data-index" not in APP_JS


def test_the_caption_carries_the_metadata_and_a_download_link():
    """Lightbox3 has no download button, so it rides in the HTML caption."""
    caption = APP_JS[APP_JS.index("function captionFor") :].split("}")[0]
    for part in ("image.frame", "formatTimestamp", "image.width", "image.height",
                 "image.format", "formatBytes", "download="):
        assert part in caption, part


def test_the_correct_umd_global_is_used():
    """The bundle exports Lightbox3.Lightbox; window.Lightbox alone is undefined
    and silently leaves the lightbox dead."""
    assert "window.Lightbox3 && window.Lightbox3.Lightbox" in APP_JS
    bundle = (VENDOR / "lightbox3.min.js").read_text()
    assert ".Lightbox3=" in bundle


def test_initialisation_is_explicit():
    """The library only self-initialises if a [data-lightbox] element exists at
    DOMContentLoaded, and the results grid is empty until a conversion runs."""
    assert re.search(r"Lightbox3\.init\(\s*\{", APP_JS)


def test_caption_links_are_styled_to_look_clickable():
    """The library ships no anchor styles, so the link read as plain text."""
    assert ".lightbox3-caption a" in Path("static/css/style.css").read_text()
