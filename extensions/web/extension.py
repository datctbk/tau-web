"""tau-web: Web fetching and search tools for tau.

Provides tools for fetching web page content and searching the web,
giving the agent access to external documentation, APIs, and current
information.

Tools registered:
  - web_fetch   : Fetch a URL and extract content as markdown
  - web_search  : Search the web and return results

Slash commands:
  /fetch <url>  : Quick-fetch a URL and display the content
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from tau.core.extension import Extension, ExtensionContext
from tau.core.types import (
    ExtensionManifest,
    SlashCommand,
    ToolDefinition,
    ToolParameter,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CONTENT_LENGTH = 100_000      # chars — truncate fetched content
MAX_SEARCH_RESULTS = 10           # max results to return per search
REQUEST_TIMEOUT = 30              # seconds
USER_AGENT = "tau-web/0.1.0 (compatible; bot)"

# Domains that are always safe to fetch (no confirmation needed)
PREAPPROVED_DOMAINS = {
    "docs.python.org", "pypi.org", "packaging.python.org",
    "docs.rs", "crates.io",
    "developer.mozilla.org", "www.w3.org",
    "github.com", "raw.githubusercontent.com", "gist.github.com",
    "stackoverflow.com", "superuser.com", "serverfault.com",
    "en.wikipedia.org",
    "registry.npmjs.org", "www.npmjs.com",
    "pkg.go.dev", "go.dev",
    "hub.docker.com",
    "learn.microsoft.com", "docs.microsoft.com",
    "cloud.google.com",
    "docs.aws.amazon.com",
    "api.github.com",
}

TRUST_HIGH = PREAPPROVED_DOMAINS
TRUST_MEDIUM = {
    "medium.com", "dev.to", "news.ycombinator.com", "reddit.com", "www.reddit.com",
}
TRACKING_QUERY_PREFIXES = ("utm_", "ref", "fbclid", "gclid", "mc_cid", "mc_eid")


def _normalize_url(url: str) -> str:
    try:
        p = urlparse(url)
        query_items = []
        for k, v in parse_qsl(p.query, keep_blank_values=True):
            kl = k.lower()
            if any(kl.startswith(pref) for pref in TRACKING_QUERY_PREFIXES):
                continue
            query_items.append((k, v))
        normalized = urlunparse((p.scheme, p.netloc.lower(), p.path, "", urlencode(query_items), ""))
        return normalized
    except Exception:
        return url


def _source_trust(domain: str) -> tuple[str, int]:
    d = domain.lower()
    if d in TRUST_HIGH:
        return "high", 3
    if d in TRUST_MEDIUM:
        return "medium", 2
    return "unknown", 1


def _normalize_source_result(item: dict[str, Any]) -> dict[str, Any]:
    url = str(item.get("url", "")).strip()
    normalized_url = _normalize_url(url)
    domain = urlparse(normalized_url).netloc.lower()
    trust_tier, trust_score = _source_trust(domain)
    out = dict(item)
    out["url"] = normalized_url
    out["domain"] = domain
    out["trust_tier"] = trust_tier
    out["trust_score"] = trust_score
    return out


def _normalize_source_results(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [_normalize_source_result(i) for i in items]
    normalized.sort(key=lambda r: (r.get("trust_score", 0), r.get("title", "")), reverse=True)
    return normalized


# ---------------------------------------------------------------------------
# HTML → Markdown converter
# ---------------------------------------------------------------------------

def _html_to_markdown(html: str) -> str:
    """Convert HTML to readable markdown text.

    Uses a lightweight regex-based approach. Falls back to raw text
    if the html2text library isn't available.
    """
    try:
        import html2text
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.ignore_emphasis = False
        h.body_width = 0  # no line wrapping
        h.skip_internal_links = True
        h.inline_links = True
        return h.handle(html).strip()
    except ImportError:
        pass

    # Fallback: simple regex-based stripping
    text = html
    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Convert common tags
    text = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"\n## \1\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<a[^>]*href=[\"']([^\"']*)[\"'][^>]*>(.*?)</a>", r"[\2](\1)", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode entities
    try:
        import html as html_mod
        text = html_mod.unescape(text)
    except ImportError:
        pass
    # Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate_content(text: str, max_length: int = MAX_CONTENT_LENGTH) -> tuple[str, bool]:
    """Truncate content to max_length, returning (text, was_truncated)."""
    if len(text) <= max_length:
        return text, False
    truncated = text[:max_length]
    # Try to cut at a paragraph boundary
    last_break = truncated.rfind("\n\n")
    if last_break > max_length * 0.8:
        truncated = truncated[:last_break]
    return truncated + f"\n\n... (truncated — original was {len(text):,} chars)", True


# ---------------------------------------------------------------------------
# HTTP fetcher
# ---------------------------------------------------------------------------

def _fetch_url(url: str, timeout: int = REQUEST_TIMEOUT) -> dict[str, Any]:
    """Fetch a URL and return structured result.

    Returns dict with keys: content, status_code, content_type, url, elapsed_ms, error
    """
    try:
        import httpx
        client_cls = httpx.Client
    except ImportError:
        # Fallback to urllib
        return _fetch_url_urllib(url, timeout)

    start = time.time()
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = client.get(url)
            elapsed_ms = int((time.time() - start) * 1000)

            content_type = resp.headers.get("content-type", "")
            
            # Check for binary content
            is_binary = any(t in content_type.lower() for t in ["application/pdf", "image/", "application/zip", "audio/", "video/"])
            
            if is_binary:
                from pathlib import Path
                import tempfile
                import mimetypes
                
                ext = mimetypes.guess_extension(content_type.split(';')[0]) or ".bin"
                tmp_dir = Path(".tau/tmp")
                tmp_dir.mkdir(parents=True, exist_ok=True)
                
                with tempfile.NamedTemporaryFile(dir=tmp_dir, suffix=ext, delete=False) as f:
                    f.write(resp.content)
                    persisted_path = f.name
                
                raw_len = len(resp.content)
                content = f"[Binary content ({content_type}, {raw_len:,} bytes) saved to {persisted_path}]"
                was_truncated = False
            else:
                raw = resp.text
                raw_len = len(raw)
                # Convert HTML to markdown
                if "text/html" in content_type:
                    content = _html_to_markdown(raw)
                else:
                    content = raw

                content, was_truncated = _truncate_content(content)

            return {
                "content": content,
                "status_code": resp.status_code,
                "content_type": content_type.split(";")[0].strip(),
                "url": str(resp.url),
                "elapsed_ms": elapsed_ms,
                "bytes": raw_len,
                "was_truncated": was_truncated,
                "error": None,
            }
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        return {
            "content": "",
            "status_code": 0,
            "content_type": "",
            "url": url,
            "elapsed_ms": elapsed_ms,
            "bytes": 0,
            "was_truncated": False,
            "error": str(e),
        }


def _fetch_url_urllib(url: str, timeout: int = REQUEST_TIMEOUT) -> dict[str, Any]:
    """Fallback URL fetcher using stdlib urllib."""
    import urllib.request
    import urllib.error

    start = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_bytes = resp.read()
            elapsed_ms = int((time.time() - start) * 1000)
            content_type = resp.headers.get("content-type", "")
            encoding = "utf-8"
            if "charset=" in content_type:
                encoding = content_type.split("charset=")[-1].split(";")[0].strip()
            raw = raw_bytes.decode(encoding, errors="replace")

            if "text/html" in content_type:
                content = _html_to_markdown(raw)
            else:
                content = raw

            content, was_truncated = _truncate_content(content)

            return {
                "content": content,
                "status_code": resp.status,
                "content_type": content_type.split(";")[0].strip(),
                "url": resp.url or url,
                "elapsed_ms": elapsed_ms,
                "bytes": len(raw_bytes),
                "was_truncated": was_truncated,
                "error": None,
            }
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        return {
            "content": "",
            "status_code": 0,
            "content_type": "",
            "url": url,
            "elapsed_ms": elapsed_ms,
            "bytes": 0,
            "was_truncated": False,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Web search via DuckDuckGo (no API key needed)
# ---------------------------------------------------------------------------

def _web_search(
    query: str,
    max_results: int = MAX_SEARCH_RESULTS,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> dict[str, Any]:
    """Search the web using DuckDuckGo's HTML interface (no API key).

    Returns dict with: query, results, elapsed_ms, error
    """
    start = time.time()
    
    # Build DDG query string with site: operators
    ddg_query = query
    if allowed_domains:
        ddg_query += " " + " OR ".join(f"site:{d}" for d in allowed_domains)
    if blocked_domains:
        ddg_query += " " + " ".join(f"-site:{d}" for d in blocked_domains)

    try:
        # Try duckduckgo_search library first
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(ddg_query, max_results=max_results))
        elapsed_ms = int((time.time() - start) * 1000)

        results = []
        for r in raw_results[:max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", r.get("link", "")),
                "snippet": r.get("body", r.get("snippet", "")),
            })
        results = _normalize_source_results(results)

        return {
            "query": query,
            "results": results,
            "elapsed_ms": elapsed_ms,
            "error": None,
        }
    except ImportError:
        pass

    # Fallback: use DuckDuckGo's lite HTML endpoint
    try:
        from urllib.parse import quote_plus
        search_url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(ddg_query)}"
        result = _fetch_url(search_url, timeout=15)
        elapsed_ms = int((time.time() - start) * 1000)

        if result["error"]:
            return {
                "query": query,
                "results": [],
                "elapsed_ms": elapsed_ms,
                "error": f"Search failed: {result['error']}. Install duckduckgo-search for better results: pip install duckduckgo-search",
            }

        # Extract links from the lite page
        content = result["content"]
        results = []
        # Extract markdown links from the converted content
        link_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
        for match in link_pattern.finditer(content):
            title, url = match.group(1), match.group(2)
            if "duckduckgo.com" not in url and title.strip():
                results.append({"title": title.strip(), "url": url, "snippet": ""})
                if len(results) >= max_results:
                    break
        results = _normalize_source_results(results)

        return {
            "query": query,
            "results": results,
            "elapsed_ms": elapsed_ms,
            "error": None if results else "No results found. Try a different query or install duckduckgo-search: pip install duckduckgo-search",
        }
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        return {
            "query": query,
            "results": [],
            "elapsed_ms": elapsed_ms,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Extension
# ---------------------------------------------------------------------------

class WebExtension(Extension):
    manifest = ExtensionManifest(
        name="web",
        version="0.1.0",
        description="Web tools — fetch URLs and search the web.",
        author="datctbk",
    )

    def __init__(self) -> None:
        self._ext_context: ExtensionContext | None = None

    def on_load(self, context: ExtensionContext) -> None:
        self._ext_context = context

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="web_fetch",
                description=(
                    "Fetch content from a URL and return it as markdown text. "
                    "Use this to read web pages, documentation, API responses, "
                    "and other online resources.\n\n"
                    "IMPORTANT: This WILL FAIL for authenticated/private URLs "
                    "(Google Docs, Confluence, Jira, private GitHub repos). "
                    "Only use for publicly accessible URLs.\n\n"
                    "The content is automatically converted from HTML to readable markdown."
                ),
                parameters={
                    "url": ToolParameter(
                        type="string",
                        description="The URL to fetch content from.",
                    ),
                    "prompt": ToolParameter(
                        type="string",
                        description=(
                            "Optional prompt to run on the fetched content. "
                            "If provided, a sub-agent will read the content and answer "
                            "your prompt directly. E.g. 'Extract the installation instructions' "
                            "or 'What does this API endpoint return?'"
                        ),
                        required=False,
                    ),
                },
                handler=self._handle_web_fetch,
            ),
            ToolDefinition(
                name="web_search",
                description=(
                    "Search the web for current information. Returns titles, URLs, "
                    "and snippets from search results.\n\n"
                    "Use this when you need up-to-date information that isn't in "
                    "the local codebase, such as:\n"
                    "- API documentation or library usage\n"
                    "- Error messages and their solutions\n"
                    "- Current best practices\n"
                    "- Package versions and compatibility\n\n"
                    "IMPORTANT: Always cite sources in your response with markdown links."
                ),
                parameters={
                    "query": ToolParameter(
                        type="string",
                        description="The search query. Be specific for better results.",
                    ),
                    "allowed_domains": ToolParameter(
                        type="array",
                        description="Optional list of domains to restrict search to (e.g. ['docs.python.org']).",
                        required=False,
                    ),
                    "blocked_domains": ToolParameter(
                        type="array",
                        description="Optional list of domains to exclude from search.",
                        required=False,
                    ),
                    "max_results": ToolParameter(
                        type="integer",
                        description=f"Maximum number of results to return (default: 5, max: {MAX_SEARCH_RESULTS}).",
                        required=False,
                    ),
                },
                handler=self._handle_web_search,
            ),
        ]

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    def slash_commands(self) -> list[SlashCommand]:
        return [
            SlashCommand(
                name="fetch",
                description="Quick-fetch a URL and display the content.",
                usage="/fetch <url>",
            ),
        ]

    def handle_slash(self, command: str, args: str, context: ExtensionContext) -> bool:
        if command == "fetch":
            self._handle_fetch_slash(args.strip(), context)
            return True
        return False

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    def _handle_web_fetch(self, url: str, prompt: str | None = None) -> str:
        # Validate URL
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return f"Error: Invalid URL '{url}'. Must include scheme (https://)."
        except Exception:
            return f"Error: Could not parse URL '{url}'."

        result = _fetch_url(url)

        if result["error"]:
            return f"Error fetching {url}: {result['error']}"

        if result["status_code"] >= 400:
            return f"Error: HTTP {result['status_code']} for {url}"

        # Build response
        parts = [
            f"**URL:** {result['url']}",
            f"**Status:** {result['status_code']}",
            f"**Content-Type:** {result['content_type']}",
            f"**Size:** {result['bytes']:,} bytes | **Fetched in:** {result['elapsed_ms']}ms",
        ]
        domain = urlparse(result["url"]).netloc.lower()
        trust_tier, _ = _source_trust(domain)
        parts.append(f"**Source:** {domain} (trust: {trust_tier})")
        if result["was_truncated"]:
            parts.append("**Note:** Content was truncated to fit.")

        parts.append("")
        parts.append("---")
        parts.append("")

        content = result["content"]

        # Run prompt on content over a sub-session if available
        if prompt:
            if self._ext_context is not None and hasattr(self._ext_context, "create_sub_session"):
                parts.append(f"*Running prompt: \"{prompt}\"*\n")
                try:
                    system_msg = (
                        f"You are a helpful extraction agent. You have just fetched the content from {url}. "
                        f"Please read the following content and answer the user's prompt directly.\n\n"
                        f"## Content from {url}\n\n{content}"
                    )
                    sub = self._ext_context.create_sub_session(
                        system_prompt=system_msg,
                        max_turns=3,
                        session_name="web-extract",
                    )
                    with sub:
                        from tau.core.types import TextDelta
                        events = sub.prompt_sync(prompt)
                        ans = "".join(
                            e.text for e in events 
                            if isinstance(e, TextDelta) and not getattr(e, "is_thinking", False)
                        )
                    content = ans
                except Exception as e:
                    content = f"Error running prompt: {e}\n\nFalling back to raw content:\n{content[:2000]}..."
            else:
                parts.append("*Note: prompt passed, but sub-agent capability is not available. Showing raw content instead.*\n")

        parts.append(content)
        return "\n".join(parts)

    def _handle_web_search(
        self,
        query: str,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        max_results: int | None = None,
    ) -> str:
        if not query or len(query) < 2:
            return "Error: Search query must be at least 2 characters."

        n = min(max_results or 5, MAX_SEARCH_RESULTS)
        result = _web_search(
            query,
            max_results=n,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
        )

        if result["error"] and not result["results"]:
            return f"Search error: {result['error']}"

        if not result["results"]:
            return f"No results found for: \"{query}\""

        parts = [
            f"**Search:** \"{result['query']}\"",
            f"**Results:** {len(result['results'])} | **Time:** {result['elapsed_ms']}ms",
            "",
        ]

        for i, r in enumerate(result["results"], 1):
            parts.append(f"{i}. **[{r['title']}]({r['url']})**")
            parts.append(f"   source: {r.get('domain', '')} | trust: {r.get('trust_tier', 'unknown')}")
            if r.get("snippet"):
                parts.append(f"   {r['snippet']}")
            parts.append("")

        if result.get("error"):
            parts.append(f"\n*Note: {result['error']}*")

        parts.append("\n*Remember to cite sources with markdown links in your response.*")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Slash command handler
    # ------------------------------------------------------------------

    def _handle_fetch_slash(self, url: str, context: ExtensionContext) -> None:
        if not url:
            context.print("[dim]Usage: /fetch <url>[/dim]")
            return

        context.print(f"[cyan]Fetching {url}...[/cyan]")
        result = _fetch_url(url)

        if result["error"]:
            context.print(f"[red]Error: {result['error']}[/red]")
            return

        content = result["content"]
        preview = content[:2000] if len(content) > 2000 else content
        lines = [
            f"[bold cyan]{result['url']}[/bold cyan]",
            f"[dim]{result['status_code']} • {result['content_type']} • "
            f"{result['bytes']:,}B • {result['elapsed_ms']}ms[/dim]",
            "",
            preview,
        ]
        if len(content) > 2000:
            lines.append(f"\n[dim]... ({len(content):,} chars total)[/dim]")
        context.print("\n".join(lines))


# Module-level instance
EXTENSION = WebExtension()
