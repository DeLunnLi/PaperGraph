"""Pure-function tests for app.core.conference_landing_pdf.

These helpers feed the SSRF-protected PDF-URL extraction pipeline. They are
security-relevant (a wrong ``True`` from ``_is_relative_pdf_path`` could cause a
relative path to be mis-classified and joined to a hostile base; a wrong
``_same_site_pdf_preference`` pick could surface a cross-site PDF over a
safe same-site one) yet have zero coverage in test_security_regression —
which only exercises ``is_safe_public_http_url``.

Scope: pure functions only (no network, no DNS, no LLM). The extraction regex
path is exercised via ``fetch_pdf_url_from_html_page`` is NOT covered here
(that needs HTTP stubbing); instead the helpers it delegates to are locked.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class TestIsRelativePdfPath:
    """``_is_relative_pdf_path`` decides whether a scraped href/src can be
    safely urljoin'd to the page base. Wrong ``True`` here = unsafe join."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from app.core.conference_landing_pdf import _is_relative_pdf_path
        self.is_rel = _is_relative_pdf_path

    def test_relative_path_is_true(self):
        assert self.is_rel("papers/foo.pdf")
        assert self.is_rel("/papers/foo.pdf")
        assert self.is_rel("./foo.pdf")

    def test_protocol_relative_rejected(self):
        # //evil.com/foo.pdf is scheme-relative → must NOT be treated as a
        # safe relative path (it would resolve to a foreign host).
        assert not self.is_rel("//evil.com/foo.pdf")

    def test_absolute_http_rejected(self):
        assert not self.is_rel("http://example.com/foo.pdf")
        assert not self.is_rel("https://example.com/foo.pdf")

    def test_dangerous_schemes_rejected(self):
        assert not self.is_rel("javascript:alert(1)")
        assert not self.is_rel("mailto:a@b.com")
        assert not self.is_rel("#anchor")

    def test_empty_rejected(self):
        assert not self.is_rel("")
        assert not self.is_rel("   ")


class TestSameSitePdfPreference:
    """``_same_site_pdf_preference`` picks which candidate PDF URL to download.
    A same-host candidate should win over a cross-host one (avoids traversing to
    an attacker-controlled CDN while still preferring the proceedings' own PDF)."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from app.core.conference_landing_pdf import _same_site_pdf_preference
        self.pref = _same_site_pdf_preference

    def test_empty_returns_none(self):
        assert self.pref("https://proceedings.org/x", []) is None
        assert self.pref("https://proceedings.org/x", None) is None

    def test_same_host_wins_over_cross_host(self):
        base = "https://proceedings.org/2024/papers/1"
        same = "https://proceedings.org/2024/papers/1/main.pdf"
        cross = "https://cdn.attacker.com/1/main.pdf"
        assert self.pref(base, [cross, same]) == same

    def test_www_prefix_normalized(self):
        # www.proceedings.org and proceedings.org should count as same host.
        base = "https://www.proceedings.org/x"
        same = "https://proceedings.org/main.pdf"
        cross = "https://other.com/main.pdf"
        assert self.pref(base, [cross, same]) == same

    def test_no_same_host_returns_first_candidate(self):
        base = "https://proceedings.org/x"
        a = "https://cdn-a.com/main.pdf"
        b = "https://cdn-b.com/main.pdf"
        # No host matches base → fall back to first candidate (preserves
        # current behaviour; the SSRF check on the chosen URL happens later).
        assert self.pref(base, [a, b]) == a

    def test_invalid_base_host_falls_back_to_first(self):
        # If the base URL has no parseable netloc, no same-host match is
        # possible → return first candidate rather than crashing.
        a = "https://cdn.com/main.pdf"
        assert self.pref("not-a-url", [a]) == a


class TestPdfExtractionRegexes:
    """The four regexes that ``fetch_pdf_url_from_html_page`` uses to pull PDF
    links out of a proceedings HTML page. Lock their matching so a refactor
    of the patterns doesn't silently drop a supported format."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from app.core.conference_landing_pdf import (
            _ABS_PDF_HTTP,
            _GENERIC_CONTENT_PDF,
            _HREF_PDF,
            _META_CONTENT_PDF,
            _META_CONTENT_PDF_ALT,
        )
        self.abs_pdf = _ABS_PDF_HTTP
        self.href_pdf = _HREF_PDF
        self.meta_pdf = _META_CONTENT_PDF
        self.meta_pdf_alt = _META_CONTENT_PDF_ALT
        self.generic_pdf = _GENERIC_CONTENT_PDF

    def test_abs_pdf_http_matches_bare_url(self):
        assert self.abs_pdf.search("see https://proceedings.org/x.pdf for paper")
        # case-insensitive
        assert self.abs_pdf.search("HTTP://proceedings.org/X.PDF")

    def test_href_pdf_matches_src_or_data_href(self):
        assert self.href_pdf.search('<a href="main.pdf">')
        assert self.href_pdf.search('<embed src="main.pdf">')
        assert self.href_pdf.search('<a data-href="main.pdf">')

    def test_meta_pdf_matches_citation_pdf_url(self):
        # NeurIPS-style: <meta name="citation_pdf_url" content="...pdf">
        html = '<meta name="citation_pdf_url" content="https://proceedings.org/x.pdf">'
        m = self.meta_pdf.search(html)
        assert m and m.group(1).endswith(".pdf")

    def test_meta_pdf_alt_matches_reversed_attr_order(self):
        # Some sites put content before the property/name attribute.
        html = '<meta content="https://proceedings.org/x.pdf" property="og:pdf_url">'
        m = self.meta_pdf_alt.search(html)
        assert m and m.group(1).endswith(".pdf")

    def test_generic_content_matches_when_meta_fails(self):
        html = '<meta content="https://proceedings.org/x.pdf">'
        m = self.generic_pdf.search(html)
        assert m and m.group(1).endswith(".pdf")
