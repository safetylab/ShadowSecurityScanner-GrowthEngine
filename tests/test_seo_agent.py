"""SEO agent against a local site."""

import importlib

import pytest

from sge import db

seo_agent = importlib.import_module("agents.seo.main")

PAGE = b"""<html><head><title>A title long enough for the check here</title>
<meta name="description" content="%s">
<meta property="og:title" content="t">
<meta property="og:description" content="d">
<meta property="og:type" content="website">
<meta property="og:url" content="u">
<link rel="canonical" href="%s">
</head><body><h1>Heading</h1>
<a href="/second">second</a><a href="https://elsewhere.test/x">out</a>
</body></html>"""


def run(argv):
    return seo_agent.main(argv)


@pytest.fixture
def site(feed_server, monkeypatch, temp_db):
    """A small site: two pages, a sitemap and robots.txt."""
    base = feed_server.url + "/"
    monkeypatch.setattr(seo_agent, "site_url", lambda: base)
    monkeypatch.setattr(seo_agent, "REQUEST_DELAY", 0)

    def page(path, description=b"d" * 100, canonical=None):
        url = f"{base.rstrip('/')}{path}"
        body = PAGE % (description, (canonical or url).encode())
        feed_server.serve(path, body, content_type="text/html")
        return url

    first = page("/")
    second = page("/second")

    feed_server.serve("/sitemap.xml",
                      f"<urlset><url><loc>{first}</loc></url>"
                      f"<url><loc>{second}</loc></url></urlset>".encode())
    feed_server.serve("/robots.txt",
                      f"User-agent: *\nAllow: /\nSitemap: "
                      f"{base}sitemap.xml\n".encode(),
                      content_type="text/plain")

    page.base = base
    page.server = feed_server
    return page


def findings(run_id=None):
    with db.session() as connection:
        query = "SELECT severity, code, message, url FROM seo_findings"
        params = []

        if run_id:
            query += " WHERE run_id = ?"
            params.append(run_id)

        return [dict(row) for row in connection.execute(query, params)]


def test_audit_visits_sitemap_pages(site, capsys):
    assert run(["audit"]) == 0
    capsys.readouterr()

    with db.session() as connection:
        urls = [row["url"] for row in connection.execute(
            "SELECT url FROM seo_pages ORDER BY url"
        )]

    assert len(urls) == 2
    assert all(row["code"] != "sitemap_missing" for row in findings())


def test_a_clean_site_produces_no_findings(site, capsys):
    run(["audit"])
    capsys.readouterr()

    assert findings() == []


def test_missing_sitemap_and_robots_are_reported(feed_server, monkeypatch,
                                                 temp_db, capsys):
    monkeypatch.setattr(seo_agent, "site_url",
                        lambda: feed_server.url + "/")
    monkeypatch.setattr(seo_agent, "REQUEST_DELAY", 0)
    feed_server.serve("/", PAGE % (b"d" * 100, feed_server.url.encode()),
                      content_type="text/html")

    run(["audit"])
    capsys.readouterr()
    codes = {row["code"] for row in findings()}

    assert "sitemap_missing" in codes
    assert "robots_missing" in codes


def test_robots_disallowing_everything_is_critical(site, capsys):
    site.server.serve("/robots.txt", b"User-agent: *\nDisallow: /\n",
                      content_type="text/plain")

    run(["audit"])
    capsys.readouterr()
    blocking = [row for row in findings()
                if row["code"] == "robots_blocks_site"]

    assert blocking and blocking[0]["severity"] == "critical"


def test_an_unreachable_page_in_the_sitemap_is_critical(site, capsys):
    base = site.base.rstrip("/")
    site.server.serve(
        "/sitemap.xml",
        f"<urlset><url><loc>{base}/</loc></url>"
        f"<url><loc>{base}/gone</loc></url></urlset>".encode())

    assert run(["audit"]) == 0
    capsys.readouterr()

    assert any(row["code"] == "unreachable" and row["severity"] == "critical"
               for row in findings())


def test_page_findings_are_recorded_per_url(site, capsys):
    site.server.serve("/second",
                      b"<html><head></head><body></body></html>",
                      content_type="text/html")

    run(["audit"])
    capsys.readouterr()
    second = [row for row in findings() if row["url"].endswith("/second")]

    assert {"title_missing", "description_missing", "canonical_missing",
            "h1_missing"} <= {row["code"] for row in second}


def test_link_check_records_broken_links(site, capsys):
    site.server.serve(
        "/", PAGE % (b"d" * 100, site.base.encode())
        + b'<a href="/missing-page">broken</a>',
        content_type="text/html")

    assert run(["audit", "--check-links"]) == 0
    capsys.readouterr()

    with db.session() as connection:
        broken = connection.execute(
            "SELECT url, error FROM seo_links WHERE error IS NOT NULL"
        ).fetchall()

    assert any("missing-page" in row["url"] for row in broken)
    assert any(row["code"] == "broken_links" for row in findings())


def test_report_needs_an_audit_first(temp_db):
    assert run(["report"]) == 1


def test_report_is_written_worst_first(site, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(seo_agent.paths, "REPORTS_DIR", tmp_path / "r")
    site.server.serve("/second", b"<html><body></body></html>",
                      content_type="text/html")
    run(["audit"])
    capsys.readouterr()

    assert run(["report"]) == 0

    text = next((tmp_path / "r").glob("seo-*.md")).read_text("utf-8")

    assert "does not change the site" in text
    assert text.index("**critical**") < text.index("**high**")


def test_links_command_without_a_check_says_so(site, capsys):
    run(["audit"])
    capsys.readouterr()

    assert run(["links"]) == 0
    assert "No links checked" in capsys.readouterr().out
