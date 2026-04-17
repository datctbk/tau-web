"""Tests for _fetch_url and _web_search functions."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
TAU_ROOT = ROOT.parent / "tau"
sys.path.insert(0, str(TAU_ROOT))

import importlib.util

_mod_name = "_tau_ext_web_fetch"
_spec = importlib.util.spec_from_file_location(
    _mod_name,
    str(ROOT / "extensions" / "web" / "extension.py"),
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_mod_name] = _mod
_spec.loader.exec_module(_mod)

_fetch_url = _mod._fetch_url
_web_search = _mod._web_search
_fetch_url_urllib = _mod._fetch_url_urllib
PREAPPROVED_DOMAINS = _mod.PREAPPROVED_DOMAINS
_normalize_url = _mod._normalize_url
_normalize_source_results = _mod._normalize_source_results


# ---------------------------------------------------------------------------
# _fetch_url
# ---------------------------------------------------------------------------

class TestFetchUrl:
    def test_returns_required_keys(self):
        """Even on error, must return the full structure."""
        result = _fetch_url("http://127.0.0.1:1/nonexistent", timeout=2)
        assert "content" in result
        assert "status_code" in result
        assert "content_type" in result
        assert "url" in result
        assert "elapsed_ms" in result
        assert "error" in result

    def test_invalid_host_returns_error(self):
        result = _fetch_url("http://this-does-not-exist.invalid/", timeout=2)
        assert result["error"] is not None
        assert result["status_code"] == 0

    def test_elapsed_ms_is_positive(self):
        result = _fetch_url("http://127.0.0.1:1/", timeout=1)
        assert result["elapsed_ms"] >= 0


class TestFetchUrlUrllib:
    def test_returns_required_keys(self):
        result = _fetch_url_urllib("http://127.0.0.1:1/nonexistent", timeout=2)
        assert "content" in result
        assert "status_code" in result
        assert "error" in result

    def test_invalid_host_returns_error(self):
        result = _fetch_url_urllib("http://this-does-not-exist.invalid/", timeout=2)
        assert result["error"] is not None


# ---------------------------------------------------------------------------
# _web_search
# ---------------------------------------------------------------------------

class TestWebSearch:
    def test_returns_required_keys(self):
        result = _web_search("test query", max_results=1)
        assert "query" in result
        assert "results" in result
        assert "elapsed_ms" in result
        assert result["query"] == "test query"

    def test_results_is_list(self):
        result = _web_search("python programming", max_results=3)
        assert isinstance(result["results"], list)


# ---------------------------------------------------------------------------
# Preapproved domains
# ---------------------------------------------------------------------------

class TestPreapprovedDomains:
    def test_docs_python_org(self):
        assert "docs.python.org" in PREAPPROVED_DOMAINS

    def test_github_com(self):
        assert "github.com" in PREAPPROVED_DOMAINS

    def test_stackoverflow(self):
        assert "stackoverflow.com" in PREAPPROVED_DOMAINS

    def test_mdn(self):
        assert "developer.mozilla.org" in PREAPPROVED_DOMAINS

    def test_unknown_domain_not_preapproved(self):
        assert "evil-site.example.com" not in PREAPPROVED_DOMAINS


class TestSourceTrustNormalization:
    def test_normalize_url_strips_tracking_params(self):
        u = _normalize_url("https://example.com/docs?a=1&utm_source=x&ref=abc#frag")
        assert "utm_source" not in u
        assert "ref=" not in u
        assert "#" not in u
        assert "a=1" in u

    def test_normalize_source_results_adds_trust_fields(self):
        rows = _normalize_source_results([
            {"title": "Docs", "url": "https://docs.python.org/3/", "snippet": "..."},
            {"title": "Other", "url": "https://unknown.example.com/page", "snippet": "..."},
        ])
        assert rows[0]["trust_tier"] in {"high", "medium", "unknown"}
        assert "domain" in rows[0]
        assert "trust_score" in rows[0]

    def test_trusted_sources_sorted_first(self):
        rows = _normalize_source_results([
            {"title": "Unknown", "url": "https://unknown.example.com/", "snippet": "..."},
            {"title": "Python Docs", "url": "https://docs.python.org/3/", "snippet": "..."},
        ])
        assert rows[0]["domain"] == "docs.python.org"
