"""Tests for the WebExtension — tools, slash commands, and handlers."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
TAU_ROOT = ROOT.parent / "tau"
sys.path.insert(0, str(TAU_ROOT))

import importlib.util

_mod_name = "_tau_ext_web_ext"
_spec = importlib.util.spec_from_file_location(
    _mod_name,
    str(ROOT / "extensions" / "web" / "extension.py"),
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_mod_name] = _mod
_spec.loader.exec_module(_mod)

WebExtension = _mod.WebExtension


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ext():
    e = WebExtension()
    ctx = MagicMock()
    ctx.print = MagicMock()
    e._ext_context = ctx
    return e


@pytest.fixture
def ctx_mock():
    ctx = MagicMock()
    ctx.print = MagicMock()
    return ctx


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

class TestManifest:
    def test_name(self):
        assert WebExtension.manifest.name == "web"

    def test_version(self):
        assert WebExtension.manifest.version == "0.1.0"


# ---------------------------------------------------------------------------
# Tools registration
# ---------------------------------------------------------------------------

class TestToolsRegistration:
    def test_registers_two_tools(self, ext):
        tools = ext.tools()
        assert len(tools) == 2

    def test_tool_names(self, ext):
        names = {t.name for t in ext.tools()}
        assert names == {"web_fetch", "web_search"}

    def test_web_fetch_has_url_param(self, ext):
        fetch_tool = next(t for t in ext.tools() if t.name == "web_fetch")
        assert "url" in fetch_tool.parameters

    def test_web_fetch_has_extract_param(self, ext):
        fetch_tool = next(t for t in ext.tools() if t.name == "web_fetch")
        assert "extract" in fetch_tool.parameters
        assert fetch_tool.parameters["extract"].required is False

    def test_web_search_has_query_param(self, ext):
        search_tool = next(t for t in ext.tools() if t.name == "web_search")
        assert "query" in search_tool.parameters

    def test_web_search_has_max_results_param(self, ext):
        search_tool = next(t for t in ext.tools() if t.name == "web_search")
        assert "max_results" in search_tool.parameters
        assert search_tool.parameters["max_results"].required is False

    def test_tools_have_handlers(self, ext):
        for tool in ext.tools():
            assert callable(tool.handler), f"Tool {tool.name} has no handler"

    def test_tools_have_descriptions(self, ext):
        for tool in ext.tools():
            assert len(tool.description) > 10, f"Tool {tool.name} has no description"


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

class TestSlashCommands:
    def test_registers_one_command(self, ext):
        assert len(ext.slash_commands()) == 1

    def test_command_name(self, ext):
        names = {c.name for c in ext.slash_commands()}
        assert names == {"fetch"}

    def test_handle_fetch_returns_true(self, ext, ctx_mock):
        assert ext.handle_slash("fetch", "https://example.com", ctx_mock) is True

    def test_handle_unknown_returns_false(self, ext, ctx_mock):
        assert ext.handle_slash("unknown", "", ctx_mock) is False

    def test_fetch_no_url_shows_usage(self, ext, ctx_mock):
        ext.handle_slash("fetch", "", ctx_mock)
        output = ctx_mock.print.call_args[0][0]
        assert "Usage" in output


# ---------------------------------------------------------------------------
# web_fetch handler
# ---------------------------------------------------------------------------

class TestWebFetchHandler:
    def test_invalid_url(self, ext):
        result = ext._handle_web_fetch(url="not-a-url")
        assert "Error" in result
        assert "Invalid" in result

    def test_no_scheme_url(self, ext):
        result = ext._handle_web_fetch(url="example.com/page")
        assert "Error" in result

    @patch.object(_mod, "_fetch_url")
    def test_successful_fetch(self, mock_fetch, ext):
        mock_fetch.return_value = {
            "content": "# Hello World\n\nSome content here.",
            "status_code": 200,
            "content_type": "text/html",
            "url": "https://example.com",
            "elapsed_ms": 150,
            "bytes": 500,
            "was_truncated": False,
            "error": None,
        }
        result = ext._handle_web_fetch(url="https://example.com")
        assert "Hello World" in result
        assert "200" in result
        assert "example.com" in result

    @patch.object(_mod, "_fetch_url")
    def test_fetch_with_error(self, mock_fetch, ext):
        mock_fetch.return_value = {
            "content": "",
            "status_code": 0,
            "content_type": "",
            "url": "https://example.com",
            "elapsed_ms": 50,
            "bytes": 0,
            "was_truncated": False,
            "error": "Connection refused",
        }
        result = ext._handle_web_fetch(url="https://example.com")
        assert "Error" in result
        assert "Connection refused" in result

    @patch.object(_mod, "_fetch_url")
    def test_fetch_404(self, mock_fetch, ext):
        mock_fetch.return_value = {
            "content": "Not Found",
            "status_code": 404,
            "content_type": "text/html",
            "url": "https://example.com/missing",
            "elapsed_ms": 50,
            "bytes": 10,
            "was_truncated": False,
            "error": None,
        }
        result = ext._handle_web_fetch(url="https://example.com/missing")
        assert "404" in result

    @patch.object(_mod, "_fetch_url")
    def test_fetch_with_extract(self, mock_fetch, ext):
        mock_fetch.return_value = {
            "content": "# Intro\n\nGeneral stuff.\n\n# Installation\n\npip install foo\n\n# Usage\n\nimport foo",
            "status_code": 200,
            "content_type": "text/html",
            "url": "https://example.com",
            "elapsed_ms": 100,
            "bytes": 200,
            "was_truncated": False,
            "error": None,
        }
        result = ext._handle_web_fetch(url="https://example.com", extract="Installation")
        assert "pip install foo" in result
        assert "Filtered for" in result

    @patch.object(_mod, "_fetch_url")
    def test_truncation_noted(self, mock_fetch, ext):
        mock_fetch.return_value = {
            "content": "Content",
            "status_code": 200,
            "content_type": "text/html",
            "url": "https://example.com",
            "elapsed_ms": 100,
            "bytes": 500000,
            "was_truncated": True,
            "error": None,
        }
        result = ext._handle_web_fetch(url="https://example.com")
        assert "truncated" in result.lower()


# ---------------------------------------------------------------------------
# web_search handler
# ---------------------------------------------------------------------------

class TestWebSearchHandler:
    def test_empty_query(self, ext):
        result = ext._handle_web_search(query="")
        assert "Error" in result

    def test_short_query(self, ext):
        result = ext._handle_web_search(query="a")
        assert "Error" in result

    @patch.object(_mod, "_web_search")
    def test_successful_search(self, mock_search, ext):
        mock_search.return_value = {
            "query": "python testing",
            "results": [
                {"title": "pytest docs", "url": "https://docs.pytest.org", "snippet": "Full-featured testing"},
                {"title": "unittest", "url": "https://docs.python.org/3/library/unittest.html", "snippet": "Standard lib"},
            ],
            "elapsed_ms": 300,
            "error": None,
        }
        result = ext._handle_web_search(query="python testing")
        assert "pytest docs" in result
        assert "unittest" in result
        assert "docs.pytest.org" in result
        assert "cite sources" in result.lower()

    @patch.object(_mod, "_web_search")
    def test_search_error(self, mock_search, ext):
        mock_search.return_value = {
            "query": "test",
            "results": [],
            "elapsed_ms": 50,
            "error": "Network timeout",
        }
        result = ext._handle_web_search(query="test")
        assert "error" in result.lower()

    @patch.object(_mod, "_web_search")
    def test_no_results(self, mock_search, ext):
        mock_search.return_value = {
            "query": "xyznonexistent123",
            "results": [],
            "elapsed_ms": 200,
            "error": None,
        }
        result = ext._handle_web_search(query="xyznonexistent123")
        assert "No results" in result

    @patch.object(_mod, "_web_search")
    def test_max_results_capped(self, mock_search, ext):
        mock_search.return_value = {
            "query": "test",
            "results": [{"title": f"R{i}", "url": f"https://r{i}.com", "snippet": ""} for i in range(5)],
            "elapsed_ms": 100,
            "error": None,
        }
        ext._handle_web_search(query="test", max_results=50)
        # Should have been capped to MAX_SEARCH_RESULTS
        call_args = mock_search.call_args
        assert call_args[1]["max_results"] <= _mod.MAX_SEARCH_RESULTS


# ---------------------------------------------------------------------------
# on_load
# ---------------------------------------------------------------------------

class TestOnLoad:
    def test_stores_context(self):
        ext = WebExtension()
        ctx = MagicMock()
        ext.on_load(ctx)
        assert ext._ext_context is ctx
