"""Internet tools: web search, reading web pages, downloading files."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString

from ..config import Settings
from . import Tool, ToolContext, ToolError
from .files import clean_lines, human_size, pdf_to_text, relative, resolve_path

MAX_FETCH_BYTES = 8 * 1024 * 1024
DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
TAVILY_URL = "https://api.tavily.com/search"


# --------------------------------------------------------------------------- safety


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def check_url(url: str, settings: Settings) -> None:
    """Only allow http(s) to public hosts, so web content can't steer the agent into
    the local network (router admin pages, cloud metadata endpoints, this server...)."""
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolError(f"Only http(s) URLs are supported, got '{parsed.scheme}'.")
    host = parsed.host
    if not host:
        raise ToolError(f"Invalid URL: {url}")
    if settings.allow_private_network:
        return
    blocked = ToolError(
        f"Access to private/local network address '{host}' is blocked (set SELFAI_ALLOW_PRIVATE_NETWORK=true to allow)."
    )
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not _is_public(ip):
            raise blocked
        return
    name = host.lower().rstrip(".")
    if name == "localhost" or name.endswith((".localhost", ".local", ".internal")):
        raise blocked
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError:
        return  # can't resolve locally (e.g. DNS via a proxy); let the HTTP client try
    for info in infos:
        addr = str(info[4][0]).split("%")[0]
        try:
            if not _is_public(ipaddress.ip_address(addr)):
                raise blocked
        except ValueError:
            continue


def make_client(settings: Settings, timeout: float = 30.0) -> httpx.AsyncClient:
    async def guard(request: httpx.Request) -> None:
        await check_url(str(request.url), settings)

    return httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=10,
        timeout=httpx.Timeout(timeout, connect=15.0),
        headers={
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
        },
        event_hooks={"request": [guard]},
    )


async def _read_limited(resp: httpx.Response, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total >= limit:
            return b"".join(chunks)[:limit], True
    return b"".join(chunks), False


# --------------------------------------------------------------------------- parsing


BLOCK_TAGS = [
    "p", "div", "section", "article", "main", "header", "aside", "ul", "ol", "li", "table", "tr",
    "blockquote", "dl", "dt", "dd", "figure", "figcaption", "hr", "br",
    "h1", "h2", "h3", "h4", "h5", "h6",
]  # fmt: skip


def html_to_text(html: bytes | str, base_url: str = "", include_links: bool = False) -> tuple[str, str]:
    """Return (title, readable text) for an HTML document."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "template", "canvas", "form", "nav", "footer"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    if len(main.get_text(strip=True)) < 200 and soup.body is not None:
        main = soup.body

    # Line breaks in the HTML source are just spaces; real lines come from block tags.
    for node in main.find_all(string=True):
        if type(node) is NavigableString and node.find_parent("pre") is None:
            node.replace_with(re.sub(r"[ \t\r\n]+", " ", str(node)))
    # Keep <pre> blocks verbatim (indentation matters in code) behind placeholders.
    preformatted: list[str] = []
    for pre in main.find_all("pre"):
        preformatted.append(pre.get_text().strip("\n"))
        pre.replace_with(f"\n\n\x00{len(preformatted) - 1}\x00\n\n")
    for tag in main.find_all(BLOCK_TAGS):
        if tag.name in ("li", "tr", "br", "dt", "dd"):
            tag.insert_before("\n")  # one item per line
        else:
            tag.insert_before("\n\n")
            tag.insert_after("\n\n")
        if tag.name.startswith("h") and tag.name[1:].isdigit():
            tag.insert(0, "#" * int(tag.name[1]) + " ")
        elif tag.name == "li":
            tag.insert(0, "- ")
    for cell in main.find_all(["td", "th"]):
        cell.insert_after(" | ")
    text = clean_lines(main.get_text())
    text = re.sub("\x00(\\d+)\x00", lambda m: f"```\n{preformatted[int(m.group(1))]}\n```", text)
    if include_links:
        links: list[str] = []
        seen: set[str] = set()
        for a in main.find_all("a", href=True):
            href = urljoin(base_url, a["href"])
            label = a.get_text(" ", strip=True)
            if not href.startswith(("http://", "https://")) or href in seen or not label:
                continue
            seen.add(href)
            links.append(f"- [{label[:80]}]({href})")
            if len(links) >= 60:
                break
        if links:
            text += "\n\nLinks on this page:\n" + "\n".join(links)
    return title, text


def _unwrap_ddg(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return href


def _is_ad(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith(("/y.js", "/l/"))


def parse_duckduckgo_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for res in soup.select("div.result"):
        if "result--ad" in (res.get("class") or []):
            continue
        a = res.select_one("a.result__a")
        if a is None:
            continue
        url = _unwrap_ddg(a.get("href", ""))
        if not url.startswith("http") or _is_ad(url):
            continue
        snippet = res.select_one(".result__snippet")
        results.append(
            {
                "title": a.get_text(" ", strip=True),
                "url": url,
                "snippet": snippet.get_text(" ", strip=True) if snippet else "",
            }
        )
    return results


def parse_duckduckgo_lite(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for a in soup.select("a.result-link"):
        url = _unwrap_ddg(a.get("href", ""))
        if not url.startswith("http") or _is_ad(url):
            continue
        snippet = ""
        row = a.find_parent("tr")
        nxt = row.find_next_sibling("tr") if row else None
        cell = nxt.select_one("td.result-snippet") if nxt else None
        if cell:
            snippet = cell.get_text(" ", strip=True)
        results.append({"title": a.get_text(" ", strip=True), "url": url, "snippet": snippet})
    return results


# --------------------------------------------------------------------------- tools


async def _search_duckduckgo(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.post(DDG_HTML_URL, data={"q": query, "b": ""})
    results = parse_duckduckgo_html(resp.text) if resp.status_code == 200 else []
    if not results:
        resp = await client.post(DDG_LITE_URL, data={"q": query})
        if resp.status_code == 200:
            results = parse_duckduckgo_lite(resp.text)
    return results


async def _search_tavily(client: httpx.AsyncClient, api_key: str, query: str, max_results: int) -> list[dict]:
    resp = await client.post(
        TAVILY_URL,
        json={"api_key": api_key, "query": query, "max_results": max_results},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if resp.status_code != 200:
        raise ToolError(f"Tavily search failed: HTTP {resp.status_code} {resp.text[:300]}")
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in resp.json().get("results", [])
    ]


async def web_search(ctx: ToolContext, query: str, max_results: int = 6) -> str:
    query = query.strip()
    if not query:
        raise ToolError("query is empty.")
    max_results = max(1, min(int(max_results), 10))
    async with make_client(ctx.settings, timeout=20) as client:
        if ctx.settings.tavily_api_key:
            results = await _search_tavily(client, ctx.settings.tavily_api_key, query, max_results)
        else:
            results = await _search_duckduckgo(client, query)
    if not results:
        return f"No results found for: {query}"
    lines = [f"Search results for: {query}", ""]
    for i, r in enumerate(results[:max_results], 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet']}")
    lines.append("\nUse fetch_url to read any of these pages in full.")
    return "\n".join(lines)


async def fetch_url(
    ctx: ToolContext, url: str, start: int = 0, max_chars: int = 15_000, include_links: bool = False
) -> str:
    try:
        async with make_client(ctx.settings) as client:
            async with client.stream("GET", url) as resp:
                body, truncated = await _read_limited(resp, MAX_FETCH_BYTES)
    except httpx.HTTPError as exc:
        raise ToolError(f"Request to {url} failed: {type(exc).__name__}: {exc}") from exc

    final_url = str(resp.url)
    ctype = resp.headers.get("content-type", "").lower()
    if resp.status_code >= 400:
        detail = body[:500].decode("utf-8", "replace")
        raise ToolError(f"HTTP {resp.status_code} for {final_url}. {detail}")

    title = ""
    head = body[:512].lstrip().lower()
    if "html" in ctype or (not ctype and head.startswith((b"<!doctype html", b"<html"))):
        title, text = html_to_text(body, final_url, include_links)
    elif "pdf" in ctype or head.startswith(b"%pdf"):
        if truncated:
            raise ToolError("PDF is too large to read inline; use download_file instead.")
        text = await asyncio.to_thread(pdf_to_text, body)
    elif ctype.startswith("text/") or any(t in ctype for t in ("json", "xml", "javascript", "csv")) or not ctype:
        text = body.decode(resp.charset_encoding or "utf-8", "replace")
    else:
        return (
            f"{final_url} is a binary file ({ctype or 'unknown type'}, {human_size(len(body))}+). "
            "Use download_file to save it to the workspace."
        )

    start = max(0, int(start))
    max_chars = max(1, min(int(max_chars), ctx.settings.max_tool_result_chars))
    chunk = text[start : start + max_chars]
    header = f"URL: {final_url}\n" + (f"Title: {title}\n" if title else "") + "\n"
    remaining = len(text) - (start + len(chunk))
    if remaining > 0:
        chunk += f"\n\n[{remaining} more characters; call fetch_url with start={start + len(chunk)} to continue]"
    if not chunk.strip():
        chunk = "(no readable text found - the page may require JavaScript)"
    return header + chunk


async def download_file(ctx: ToolContext, url: str, path: str) -> str:
    workspace = ctx.settings.workspace_dir
    target = resolve_path(workspace, path)
    if target == workspace.resolve() or target.is_dir():
        raise ToolError(f"'{path}' is a directory; include a file name.")
    limit = ctx.settings.max_download_mb * 1024 * 1024
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    size = 0
    try:
        async with make_client(ctx.settings, timeout=120) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise ToolError(f"HTTP {resp.status_code} for {url}")
                ctype = resp.headers.get("content-type", "unknown")
                with tmp.open("wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise ToolError(f"Download exceeds the {ctx.settings.max_download_mb} MB limit.")
                        fh.write(chunk)
        tmp.replace(target)
    except httpx.HTTPError as exc:
        raise ToolError(f"Download of {url} failed: {type(exc).__name__}: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
    return f"Saved {url} to {relative(workspace, target)} ({human_size(size)}, {ctype})."


TOOLS = [
    Tool(
        name="web_search",
        description=(
            "Search the internet. Returns titles, URLs and snippets. Use for current events, facts you are "
            "unsure about, documentation, prices, news, or finding pages to read with fetch_url."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "max_results": {"type": "integer", "default": 6, "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
        },
        handler=web_search,
    ),
    Tool(
        name="fetch_url",
        description=(
            "Download a web page (or text/JSON/PDF document) and return its readable text. "
            "Long pages are returned in chunks; call again with 'start' to read further."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL."},
                "start": {"type": "integer", "default": 0, "description": "Character offset for paging."},
                "max_chars": {"type": "integer", "default": 15000},
                "include_links": {
                    "type": "boolean",
                    "default": False,
                    "description": "Also list the page's links (useful for navigating a site).",
                },
            },
            "required": ["url"],
        },
        handler=fetch_url,
    ),
    Tool(
        name="download_file",
        description="Download a file from the internet (image, PDF, dataset, archive...) and save it in the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Destination path in the workspace, e.g. 'downloads/data.csv'.",
                },
            },
            "required": ["url", "path"],
        },
        handler=download_file,
    ),
]
