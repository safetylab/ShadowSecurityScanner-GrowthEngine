"""Shared fixtures: an isolated database and a local HTTP server.

Tests never touch the repository database and never reach the network;
feed behaviour is exercised against a server we control so that timeouts
and oversized responses are real rather than mocked.
"""

import http.server
import threading
import time

import pytest

from sge import db, net, paths


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point every agent at a fresh migrated database."""
    target = tmp_path / "test.db"
    monkeypatch.setenv("SGE_DB_PATH", str(target))

    with db.session(target) as connection:
        db.migrate(connection, paths.MIGRATIONS_DIR)

    return target


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves whatever the test registered for a path."""

    routes = {}

    def do_GET(self):
        route = self.routes.get(self.path)

        if route is None:
            self.send_error(404)
            return

        delay = route.get("delay", 0)
        if delay:
            time.sleep(delay)

        body = route["body"]
        self.send_response(route.get("status", 200))
        self.send_header("Content-Type", route.get("content_type",
                                                   "application/xml"))

        if route.get("send_length", True):
            self.send_header("Content-Length", str(len(body)))

        self.end_headers()

        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


@pytest.fixture(autouse=True)
def allow_local_addresses(monkeypatch):
    """Tests serve from 127.0.0.1, which the SSRF guard blocks.

    Opting in here, in one place, keeps the guard on by default
    everywhere else — including in every agent the tests drive.
    """
    monkeypatch.setenv(net.ALLOW_PRIVATE_ENV, "1")


@pytest.fixture
def feed_server():
    """Local HTTP server; register routes with ``serve(path, body, ...)``."""
    routes = {}

    handler = type("TestHandler", (_Handler,), {"routes": routes})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{server.server_address[1]}"

    class Server:
        url = base

        @staticmethod
        def serve(path, body, **options):
            routes[path] = {"body": body, **options}
            return f"{base}{path}"

    try:
        yield Server()
    finally:
        server.shutdown()
        server.server_close()


RSS_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test feed</title>
  <item>
    <title>First advisory about a vulnerability scanner</title>
    <link>https://example.test/a?utm_source=rss</link>
    <pubDate>Mon, 03 Nov 2026 10:00:00 +0000</pubDate>
    <description>A CVE affecting web application security.</description>
  </item>
  <item>
    <title>Second item</title>
    <link>https://example.test/b</link>
    <description>Port scanning notes.</description>
  </item>
</channel></rss>
"""

ATOM_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Releases</title>
  <entry>
    <title>v1.2.0</title>
    <link rel="alternate" href="https://example.test/releases/v1.2.0"/>
    <updated>2026-11-01T08:00:00Z</updated>
    <content type="html">Scanner release with new templates.</content>
  </entry>
</feed>
"""


@pytest.fixture
def rss_feed():
    return RSS_FEED


@pytest.fixture
def atom_feed():
    return ATOM_FEED
