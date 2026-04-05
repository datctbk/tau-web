"""Tests for HTML-to-markdown conversion and content truncation."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TAU_ROOT = ROOT.parent / "tau"
sys.path.insert(0, str(TAU_ROOT))

import importlib.util

_mod_name = "_tau_ext_web_conv"
_spec = importlib.util.spec_from_file_location(
    _mod_name,
    str(ROOT / "extensions" / "web" / "extension.py"),
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_mod_name] = _mod
_spec.loader.exec_module(_mod)

_html_to_markdown = _mod._html_to_markdown
_truncate_content = _mod._truncate_content
MAX_CONTENT_LENGTH = _mod.MAX_CONTENT_LENGTH


# ---------------------------------------------------------------------------
# HTML to Markdown
# ---------------------------------------------------------------------------

class TestHtmlToMarkdown:
    def test_strips_script_tags(self):
        html = "<p>Hello</p><script>alert('x')</script><p>World</p>"
        result = _html_to_markdown(html)
        assert "alert" not in result
        assert "Hello" in result
        assert "World" in result

    def test_strips_style_tags(self):
        html = "<style>.x{color:red}</style><p>Content here</p>"
        result = _html_to_markdown(html)
        assert "color" not in result
        assert "Content" in result

    def test_converts_headings(self):
        html = "<h1>Title</h1><h2>Subtitle</h2>"
        result = _html_to_markdown(html)
        assert "Title" in result
        assert "Subtitle" in result

    def test_converts_links(self):
        html = '<a href="https://example.com">Click here</a>'
        result = _html_to_markdown(html)
        assert "example.com" in result
        assert "Click" in result

    def test_converts_list_items(self):
        html = "<ul><li>Item A</li><li>Item B</li></ul>"
        result = _html_to_markdown(html)
        assert "Item A" in result
        assert "Item B" in result

    def test_converts_paragraphs(self):
        html = "<p>First paragraph</p><p>Second paragraph</p>"
        result = _html_to_markdown(html)
        assert "First paragraph" in result
        assert "Second paragraph" in result

    def test_plain_text_passthrough(self):
        text = "Just plain text, no HTML."
        result = _html_to_markdown(text)
        assert "Just plain text" in result

    def test_handles_empty_string(self):
        assert _html_to_markdown("") == ""

    def test_decodes_html_entities(self):
        html = "<p>Hello &amp; World &lt;3&gt;</p>"
        result = _html_to_markdown(html)
        assert "&" in result
        assert "<" in result

    def test_collapses_extra_newlines(self):
        html = "<p>A</p><br><br><br><br><p>B</p>"
        result = _html_to_markdown(html)
        # Should not have more than 2 consecutive newlines
        assert "\n\n\n\n" not in result


# ---------------------------------------------------------------------------
# Content truncation
# ---------------------------------------------------------------------------

class TestTruncateContent:
    def test_short_content_not_truncated(self):
        text = "Short text"
        result, was_truncated = _truncate_content(text)
        assert result == text
        assert was_truncated is False

    def test_long_content_truncated(self):
        text = "A" * (MAX_CONTENT_LENGTH + 1000)
        result, was_truncated = _truncate_content(text)
        assert was_truncated is True
        assert len(result) < len(text)
        assert "truncated" in result

    def test_truncation_includes_original_size(self):
        text = "B" * (MAX_CONTENT_LENGTH * 2)
        result, _ = _truncate_content(text)
        # Should mention original size
        assert str(len(text)) in result.replace(",", "")

    def test_exact_boundary(self):
        text = "C" * MAX_CONTENT_LENGTH
        result, was_truncated = _truncate_content(text)
        assert was_truncated is False

    def test_custom_max_length(self):
        text = "D" * 500
        result, was_truncated = _truncate_content(text, max_length=100)
        assert was_truncated is True
        assert "truncated" in result

    def test_truncation_at_paragraph_boundary(self):
        # Content with clear paragraph breaks
        paragraphs = ["Paragraph " + str(i) + "." + " text" * 50 for i in range(100)]
        text = "\n\n".join(paragraphs)
        result, was_truncated = _truncate_content(text, max_length=2000)
        assert was_truncated is True
        # Should end at a paragraph break if possible, then the truncation notice
