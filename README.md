# tau-web

Web tools extension for [tau](https://github.com/datctbk/tau).

Gives tau the ability to fetch web pages, read documentation, and search the web for current information.

## Install

```bash
tau install git:github.com/datctbk/tau-web
```

## Tools

| Tool | Description |
|------|-------------|
| `web_fetch` | Fetch content from a URL and automatically convert HTML to markdown. Supports an `extract` parameter to filter large pages to relevant sections. |
| `web_search` | Search the web for current information (defaults to DuckDuckGo, no API key required). |

## Slash Commands

| Command | Description |
|---------|-------------|
| `/fetch <url>` | Quick-fetch a URL and display the content as markdown |

## Features

- **Safe HTML parsing**: Automatically strips scripts, styles, and converts HTML to clean markdown for the LLM.
- **Content truncation**: Prevents context overflow by automatically truncating massive pages (capped at 100k chars).
- **Zero dependencies**: Falls back to Python's standard library (`urllib`, regex) if external packages like `httpx` and `html2text` aren't installed.
- **Pre-approved domains**: Safe domains like `docs.python.org` and `github.com` bypass confirmation checks in interactive mode.

## Testing

```bash
cd tau-web && python -m pytest tests/ -v
```
