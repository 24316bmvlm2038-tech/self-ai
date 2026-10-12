import http.server
import json
import threading

import httpx
import pytest

from selfai.tools import ToolContext, ToolError, web
from selfai.tools.web import (
    check_url,
    download_file,
    fetch_url,
    html_to_text,
    parse_duckduckgo_html,
    parse_duckduckgo_lite,
    web_search,
)

from .conftest import run
from .test_file_tools import make_pdf

DDG_HTML = """
<html><body>
<div class="result results_links result--ad"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a></div>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title"><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage%3Fa%3D1&amp;rut=abc">Example <b>Page</b></a></h2>
  <a class="result__snippet" href="#">An example snippet about things.</a>
</div>
<div class="result results_links web-result">
  <h2 class="result__title"><a class="result__a" href="https://direct.org/">Direct</a></h2>
  <div class="result__snippet">Second snippet</div>
</div>
</body></html>
"""

DDG_LITE = """
<table>
<tr><td>1.</td><td><a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Flite.example%2F" class='result-link'>Lite Result</a></td></tr>
<tr><td></td><td class='result-snippet'>Lite snippet text</td></tr>
</table>
"""

PAGE = """<!doctype html><html><head><title>Test Page</title><style>.x{}</style></head>
<body><nav>Menu Home About</nav>
<main><h1>Big Heading</h1><p>First paragraph with <a href="/about">a link</a>.</p>
<script>var secret = 1;</script><p>Second   paragraph.</p></main>
<footer>Copyright</footer></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    routes = {
        "/page": (200, "text/html; charset=utf-8", PAGE.encode()),
        "/data.json": (200, "application/json", b'{"answer": 42}'),
        "/image.png": (200, "image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100),
        "/doc.pdf": (200, "application/pdf", make_pdf("Hello from a PDF")),
        "/long": (200, "text/plain", ("word " * 5000).encode()),
    }

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
            return
        status, ctype, body = self.routes.get(self.path, (404, "text/plain", b"not found"))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch):
    # The local test server must not be reached through any configured HTTP proxy.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


def test_parse_duckduckgo_html():
    results = parse_duckduckgo_html(DDG_HTML)
    assert results == [
        {"title": "Example Page", "url": "https://example.com/page?a=1", "snippet": "An example snippet about things."},
        {"title": "Direct", "url": "https://direct.org/", "snippet": "Second snippet"},
    ]


def test_parse_duckduckgo_lite():
    assert parse_duckduckgo_lite(DDG_LITE) == [
        {"title": "Lite Result", "url": "https://lite.example/", "snippet": "Lite snippet text"}
    ]


def test_html_to_text_strips_boilerplate():
    title, text = html_to_text(PAGE, "https://site.test/x", include_links=True)
    assert title == "Test Page"
    assert "Big Heading" in text and "Second paragraph." in text
    assert "secret" not in text and "Menu" not in text and "Copyright" not in text
    assert "[a link](https://site.test/about)" in text


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8000/api",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://printer.local/",
    ],
)
def test_private_addresses_blocked(settings, url):
    settings.allow_private_network = False
    with pytest.raises(ToolError, match="blocked"):
        run(check_url(url, settings))


def test_non_http_schemes_blocked(settings):
    with pytest.raises(ToolError, match="http"):
        run(check_url("file:///etc/passwd", settings))


def test_public_ip_allowed(settings):
    settings.allow_private_network = False
    run(check_url("http://93.184.215.14/", settings))


def test_fetch_html_page(settings, server):
    out = run(fetch_url(ToolContext(settings), f"{server}/redirect"))
    assert out.startswith(f"URL: {server}/page\nTitle: Test Page")
    assert "First paragraph with a link." in out


def test_fetch_json_pdf_binary_and_errors(settings, server):
    ctx = ToolContext(settings)
    assert '{"answer": 42}' in run(fetch_url(ctx, f"{server}/data.json"))
    assert "Hello from a PDF" in run(fetch_url(ctx, f"{server}/doc.pdf"))
    assert "download_file" in run(fetch_url(ctx, f"{server}/image.png"))
    with pytest.raises(ToolError, match="HTTP 404"):
        run(fetch_url(ctx, f"{server}/missing"))
    paged = run(fetch_url(ctx, f"{server}/long", max_chars=100))
    assert "call fetch_url with start=100" in paged


def test_fetch_blocks_local_network_by_default(settings, server):
    settings.allow_private_network = False
    with pytest.raises(ToolError, match="blocked"):
        run(fetch_url(ToolContext(settings), f"{server}/page"))


def test_download_file(settings, server):
    ctx = ToolContext(settings)
    out = run(download_file(ctx, f"{server}/image.png", "downloads/pic.png"))
    assert "downloads/pic.png" in out
    assert (settings.workspace_dir / "downloads/pic.png").read_bytes().startswith(b"\x89PNG")

    settings.max_download_mb = 0
    with pytest.raises(ToolError, match="limit"):
        run(download_file(ctx, f"{server}/image.png", "downloads/too-big.png"))
    assert not (settings.workspace_dir / "downloads/too-big.png").exists()
    assert not list((settings.workspace_dir / "downloads").glob("*.part"))


def _mock_client(handler):
    real = web.make_client

    def factory(settings, timeout=30.0):
        client = real(settings, timeout)
        client._transport = httpx.MockTransport(handler)
        return client

    return factory


def test_web_search_duckduckgo_with_lite_fallback(settings, monkeypatch):
    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        if request.url.host == "html.duckduckgo.com":
            return httpx.Response(202, text="anomaly")
        return httpx.Response(200, text=DDG_LITE)

    monkeypatch.setattr(web, "make_client", _mock_client(handler))
    out = run(web_search(ToolContext(settings), "test query"))
    assert "1. Lite Result" in out and "https://lite.example/" in out
    assert seen == [web.DDG_HTML_URL, web.DDG_LITE_URL]


def test_web_search_tavily(settings, monkeypatch):
    settings.tavily_api_key = "tvly-123"

    def handler(request: httpx.Request):
        assert request.url == web.TAVILY_URL
        assert request.headers["Authorization"] == "Bearer tvly-123"
        assert json.loads(request.content)["query"] == "news"
        return httpx.Response(200, json={"results": [{"title": "T", "url": "https://t.example", "content": "C"}]})

    monkeypatch.setattr(web, "make_client", _mock_client(handler))
    out = run(web_search(ToolContext(settings), "news"))
    assert "1. T\n   https://t.example\n   C" in out
