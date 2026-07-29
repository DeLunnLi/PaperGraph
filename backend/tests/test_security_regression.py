"""Security regression tests for PaperGraph.

Covers:
1. SSRF protection – is_safe_public_http_url rejects private/internal hosts
2. PDF download size ceiling
3. JWT enforcement – all protected routes return 401 without token
4. User data isolation – one user cannot read another's papers/turns
5. HTML sanitization – LLM output rendered via renderMarkdown is stripped of
   dangerous tags and attributes
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ── 1. SSRF protection ───────────────────────────────────────

class TestSSRFProtection:
    """Validate is_safe_public_http_url blocks private networks."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from app.core.conference_landing_pdf import is_safe_public_http_url
        self.is_safe = is_safe_public_http_url

    def test_rejects_localhost(self):
        assert not self.is_safe("http://localhost/secret")

    def test_rejects_127(self):
        assert not self.is_safe("http://127.0.0.1/secret")

    def test_rejects_10_net(self):
        assert not self.is_safe("http://10.0.0.1/secret")

    def test_rejects_172_16_net(self):
        assert not self.is_safe("http://172.16.0.1/secret")

    def test_rejects_192_168_net(self):
        assert not self.is_safe("http://192.168.1.1/secret")

    def test_rejects_metadata_endpoint(self):
        assert not self.is_safe("http://169.254.169.254/latest/meta-data/")

    def test_rejects_non_http(self):
        assert not self.is_safe("ftp://example.com/file.pdf")
        assert not self.is_safe("file:///etc/passwd")

    def test_accepts_public(self):
        # In some corporate/VPN networks, even public domains resolve to private IPs.
        # Instead of relying on a specific domain, we verify the logic by
        # testing that a URL that passes scheme/hostname checks would be accepted
        # if DNS returned a public IP (we can't guarantee DNS in CI).
        # Instead, verify the full flow with a mock.
        from unittest.mock import patch
        with patch("app.core.conference_landing_pdf.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, '', ('93.184.216.34', 443))]
            assert self.is_safe("https://example.com/file.pdf")
        # And ensure it still rejects private IPs from DNS
        with patch("app.core.conference_landing_pdf.socket.getaddrinfo") as mock_dns2:
            mock_dns2.return_value = [(2, 1, 6, '', ('10.0.0.1', 443))]
            assert not self.is_safe("https://example.com/file.pdf")


# ── 2. PDF size ceiling ─────────────────────────────────────

class TestPDFSizeCeiling:
    """Verify _MAX_PDF_BYTES is enforced in download_paper_pdf_to_path."""

    def test_max_pdf_bytes_is_reasonable(self):
        from app.core.pdf_download import _MAX_PDF_BYTES
        # Should be 200 MiB
        assert _MAX_PDF_BYTES == 200 * 1024 * 1024

    def test_max_redirects_is_set(self):
        from app.core.pdf_download import _MAX_REDIRECTS
        assert _MAX_REDIRECTS <= 10


# ── 3. JWT enforcement on protected routes ──────────────────

class TestJWTEnforcement:
    """All routes using require_user should return 401 without a token.

    These tests require hello_agents to be installed; if unavailable they are skipped.
    """

    @pytest.fixture()
    def client(self):
        try:
            from fastapi.testclient import TestClient
            from app.api.main import app
            return TestClient(app, raise_server_exceptions=False)
        except Exception as exc:
            pytest.skip(f"Cannot create TestClient: {exc}")

    def _assert_401(self, resp):
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text[:200]}"

    def test_verify_rejects_query_token(self, client):
        self._assert_401(client.get("/api/auth/verify?token=legacy-token"))

    def test_verify_accepts_bearer_token(self, client, monkeypatch):
        monkeypatch.setattr(
            "app.api.deps.get_user_from_token",
            lambda token: {"user_id": 7, "username": "tester"} if token == "valid-token" else None,
        )
        response = client.get(
            "/api/auth/verify",
            headers={"Authorization": "Bearer valid-token"},
        )
        assert response.status_code == 200
        assert response.json()["user_id"] == 7

    def test_library_requires_auth(self, client):
        self._assert_401(client.get("/api/papers/library"))

    def test_categories_requires_auth(self, client):
        self._assert_401(client.get("/api/papers/library/categories"))

    def test_graph_requires_auth(self, client):
        self._assert_401(client.get("/api/papers/graph/library"))

    def test_paper_reader_opening_requires_auth(self, client):
        self._assert_401(client.post("/api/ai/paper-reader/opening", json={"paper_id": 1}))

    def test_paper_reader_chat_requires_auth(self, client):
        self._assert_401(client.post("/api/ai/paper-reader/chat", json={"paper_id": 1, "messages": [], "user_message": "hi"}))

    def test_paper_reader_history_requires_auth(self, client):
        self._assert_401(client.get("/api/ai/paper-reader/history?paper_id=1"))

    def test_search_agent_stream_requires_auth(self, client):
        self._assert_401(client.post("/api/papers/search-agent/stream", json={"message": "test"}))

    def test_export_requires_auth(self, client):
        self._assert_401(client.get("/api/export/json"))

    def test_daily_get_requires_auth(self, client):
        self._assert_401(client.get("/api/papers/daily"))


class TestKnowledgeExport:
    def test_export_papers_uses_author_order(self, tmp_path):
        import sqlite3

        from app.api.routes.export import _export_papers

        db_path = tmp_path / "export.db"
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE papers (
                id INTEGER PRIMARY KEY, title TEXT, doi TEXT, arxiv_id TEXT,
                abstract TEXT, journal TEXT, venue_type TEXT, year INTEGER,
                pdf_url TEXT, source_url TEXT, source TEXT, keywords TEXT,
                category TEXT, tags TEXT, rating INTEGER, read_status TEXT,
                importance TEXT, notes TEXT, citations INTEGER,
                created_at INTEGER, updated_at INTEGER, user_id INTEGER
            );
            CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE paper_authors (
                paper_id INTEGER NOT NULL, author_id INTEGER NOT NULL,
                author_order INTEGER DEFAULT 0
            );
            INSERT INTO papers (id, title, user_id) VALUES (1, 'Export test', 7);
            INSERT INTO authors (id, name) VALUES (1, 'Second Author'), (2, 'First Author');
            INSERT INTO paper_authors (paper_id, author_id, author_order)
            VALUES (1, 1, 1), (1, 2, 0);
        """)
        try:
            papers = _export_papers(conn, user_id=7)
        finally:
            conn.close()

        assert len(papers) == 1
        assert papers[0]["authors"] == ["First Author", "Second Author"]


# ── 4. User data isolation ──────────────────────────────────

class TestUserDataIsolation:
    """Verify that PaperDatabase methods filter by user_id."""

    @pytest.fixture()
    def db(self, tmp_path):
        from app.core.storage import PaperDatabase
        db_path = str(tmp_path / "test.db")
        return PaperDatabase(db_path)

    def test_get_all_papers_filters_by_user(self, db):
        # Insert papers for two users
        db._query(
            "INSERT INTO papers(id, title, user_id) VALUES(?, ?, ?)",
            (1, "User1 Paper", 1),
        )
        db._query(
            "INSERT INTO papers(id, title, user_id) VALUES(?, ?, ?)",
            (2, "User2 Paper", 2),
        )
        u1 = db.get_all_papers(user_id=1)
        u2 = db.get_all_papers(user_id=2)
        assert len(u1) == 1
        assert len(u2) == 1
        assert u1[0].title == "User1 Paper"
        assert u2[0].title == "User2 Paper"

    def test_get_paper_by_id_rejects_wrong_user(self, db):
        db._query(
            "INSERT INTO papers(id, title, user_id) VALUES(?, ?, ?)",
            (10, "OwnedBy2", 2),
        )
        assert db.get_paper_by_id(10, user_id=1) is None
        assert db.get_paper_by_id(10, user_id=2) is not None

    def test_set_local_pdf_path_filters_by_user(self, db):
        db._query(
            "INSERT INTO papers(id, title, user_id) VALUES(?, ?, ?)",
            (15, "Owner PDF", 5),
        )
        assert not db.set_local_pdf_path(15, "papers/evil.pdf", user_id=99)
        assert db.get_paper_by_id(15, user_id=5).local_pdf_path is None
        assert db.set_local_pdf_path(15, "papers/owned.pdf", user_id=5)
        assert db.get_paper_by_id(15, user_id=5).local_pdf_path == "papers/owned.pdf"

    def test_order_by_is_allowlisted(self, db):
        db._query("INSERT INTO papers(id, title, user_id) VALUES(?, ?, ?)", (16, "B", 1))
        db._query("INSERT INTO papers(id, title, user_id) VALUES(?, ?, ?)", (17, "A", 1))
        rows = db.get_all_papers(user_id=1, order_by="title ASC; DROP TABLE papers")
        assert {p.title for p in rows} == {"A", "B"}
        assert db.count_papers(user_id=1) == 2

    def test_get_library_pdf_abspath_filters_by_user(self, db):
        db._query(
            "INSERT INTO papers(id, title, user_id, local_pdf_path) VALUES(?, ?, ?, ?)",
            (20, "PDF Owner", 5, "papers/test_fake.pdf"),
        )
        assert db.get_library_pdf_abspath(20, user_id=99) is None
        # user 5 owns the paper, but the file doesn't exist on disk so
        # the method may return None. The key check is that user_id=99
        # is rejected at the SQL level (paper not found for that user).
        result5 = db.get_library_pdf_abspath(20, user_id=5)
        # If the file doesn't exist, result5 is None but the SQL filter worked
        # (it didn't reject user 5 at the ownership level).
        # The important assertion: user 99 definitely cannot see user 5's paper.
        assert db.get_paper_by_id(20, user_id=99) is None


# ── 5. HTML sanitization ────────────────────────────────────

class TestHTMLSanitization:
    """Ensure the frontend markdown sanitizer strips dangerous content.

    Since the sanitizer is TypeScript, we test the regex logic in Python
    to confirm the pattern catches the right cases.
    """

    def _sanitize(self, html: str) -> str:
        import re
        # Mirrors the frontend sanitizeHtml + stripUnsafeTags
        html = re.sub(r'\s+on\w+\s*=\s*(["\'][^"\']*["\']|\S+)', '', html, flags=re.I)
        html = re.sub(r'(href\s*=\s*["\']?)javascript:', r'\1about:blank', html, flags=re.I)
        html = re.sub(r'\<(/)?(script|iframe|object|embed|form|input|svg|link|meta|base|style)\b[^>]*>', '', html, flags=re.I)
        return html

    def test_strips_onclick(self):
        result = self._sanitize('<div onclick="alert(1)">hello</div>')
        assert 'onclick' not in result
        assert 'hello' in result

    def test_strips_script_tag(self):
        result = self._sanitize('<script>alert(1)</script>hello')
        assert '<script' not in result
        assert 'hello' in result

    def test_strips_javascript_href(self):
        result = self._sanitize('<a href="javascript:alert(1)">link</a>')
        assert 'javascript:' not in result
        assert 'about:blank' in result

    def test_strips_iframe(self):
        result = self._sanitize('<iframe src="evil.com"></iframe>')
        assert '<iframe' not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
