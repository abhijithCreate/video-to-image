"""Static assets are versioned, so a deploy can never be driven by a stale script."""

from __future__ import annotations

import re

from app.assets import asset_url


def test_urls_carry_a_content_version():
    assert re.fullmatch(r"/static/js/app\.js\?v=[0-9a-f]{10}", asset_url("js/app.js"))
    assert re.fullmatch(
        r"/static/css/style\.css\?v=[0-9a-f]{10}", asset_url("css/style.css")
    )


def test_version_is_stable_for_unchanged_files():
    assert asset_url("js/app.js") == asset_url("js/app.js")


def test_version_changes_when_the_file_changes(tmp_path, monkeypatch):
    from app import assets

    monkeypatch.setattr(assets, "STATIC_DIR", tmp_path)
    target = tmp_path / "app.js"
    target.write_text("one")
    first = assets.asset_url("app.js")

    target.write_text("two")
    # A rewrite within the same mtime tick must still be noticed.
    import os

    stat = target.stat()
    os.utime(target, (stat.st_atime, stat.st_mtime + 1))
    assert assets.asset_url("app.js") != first


def test_missing_asset_falls_back_to_a_plain_url(tmp_path, monkeypatch):
    from app import assets

    monkeypatch.setattr(assets, "STATIC_DIR", tmp_path)
    assert assets.asset_url("absent.js") == "/static/absent.js"


def test_page_references_versioned_assets(client):
    body = client.get("/").text
    assert re.search(r'href="/static/css/style\.css\?v=[0-9a-f]{10}"', body)
    assert re.search(r'src="/static/js/app\.js\?v=[0-9a-f]{10}"', body)
    # No unversioned reference is left behind.
    assert 'src="/static/js/app.js"' not in body


def test_versioned_urls_are_served(client):
    body = client.get("/").text
    url = re.search(r'src="(/static/js/app\.js\?v=[0-9a-f]{10})"', body).group(1)
    response = client.get(url)
    assert response.status_code == 200
    assert "goToStep" in response.text
