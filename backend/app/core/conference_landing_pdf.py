
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import requests

_ABS_PDF_HTTP = re.compile(r"https?://[^'\"<>\s]+?\.pdf", re.I)

_HREF_PDF = re.compile(
    r'(?:href|src|data-href)\s*=\s*(["\'])([^"\']+?\.pdf)\1',
    re.I,
)

_META_CONTENT_PDF = re.compile(
    r'<meta[^>]+(?:property|name)\s*=\s*["\']?(?:pdf_url|citation_pdf_url|og:pdf_url)["\']?[^>]+content\s*=\s*["\']([^"\']+?\.pdf)["\']',
    re.I | re.S,
)
_META_CONTENT_PDF_ALT = re.compile(
    r'<meta[^>]+content\s*=\s*["\']([^"\']+?\.pdf)["\'][^>]+(?:property|name)\s*=\s*["\']?(?:pdf_url|citation_pdf_url|og:pdf_url)["\']?',
    re.I | re.S,
)
_GENERIC_CONTENT_PDF = re.compile(
    r'content\s*=\s*["\']([^"\']+?\.pdf)["\']',
    re.I,
)

_MAX_REDIRECTS = 5


def is_safe_public_http_url(url: str) -> bool:
    """Return whether a URL targets a non-private, non-reserved HTTP(S) host.

    Blocks RFC-1918, loopback, link-local, and IPv6 unique-local addresses.
    DNS resolution is checked to prevent DNS-rebinding SSRF; if resolution fails
    the URL is rejected.
    """
    try:
        parsed = urlparse((url or "").strip())
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            return False
        hostname = parsed.hostname
        # Quick-reject known private/loopback hostnames
        if hostname.lower() in ("localhost", "localhost.localdomain"):
            return False
        # Try DNS resolution
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        try:
            addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return False
        if not addresses:
            return False
        for entry in addresses:
            ip = ipaddress.ip_address(entry[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
            # Block IPv6 unique-local (fc00::/7) – is_private covers this in Python 3.x
            if isinstance(ip, ipaddress.IPv6Address) and not ip.is_global:
                return False
        return True
    except (OSError, ValueError):
        return False


def safe_public_get(url: str, **kwargs) -> requests.Response:
    """GET a public URL while validating every redirect target."""
    current = (url or "").strip()
    for redirect_count in range(_MAX_REDIRECTS + 1):
        if not is_safe_public_http_url(current):
            raise requests.RequestException("unsafe outbound URL")
        response = requests.get(current, allow_redirects=False, **kwargs)
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location or redirect_count >= _MAX_REDIRECTS:
                raise requests.TooManyRedirects("redirect limit exceeded")
            current = urljoin(current, location)
            continue
        if not is_safe_public_http_url(response.url):
            response.close()
            raise requests.RequestException("unsafe final URL")
        return response
    raise requests.TooManyRedirects("redirect limit exceeded")


_OJS_VIEW_OR_DL = re.compile(
    r'(?:href|data-href)\s*=\s*(["\'])([^"\']*?/article/(?:view|download)/\d+/\d+[^"\']*)\1',
    re.I,
)

def _is_relative_pdf_path(rel: str) -> bool:
    s = (rel or "").strip()
    if not s or s.startswith("//"):
        return False
    low = s.lower()
    return not low.startswith(("http://", "https://", "javascript:", "mailto:", "#"))

def _same_site_pdf_preference(base_url: str, candidates: list[str]) -> str | None:
    if not candidates:
        return None
    try:
        base_host = (urlparse(base_url).netloc or "").lower().removeprefix("www.")
    except ValueError:
        base_host = ""
    for c in candidates:
        try:
            h = (urlparse(c).netloc or "").lower().removeprefix("www.")
            if base_host and h == base_host:
                return c
        except ValueError:
            continue
    return candidates[0]

def fetch_pdf_url_from_html_page(
    url: str,
    email: str = "",
    timeout: int = 35,
    max_bytes: int = 800_000,
) -> str | None:
    u0 = (url or "").strip()
    if not is_safe_public_http_url(u0):
        return None

    mail = (email or "").strip()
    ua = f"PaperGraph/0.3 (mailto:{mail})" if mail else "PaperGraph/0.3"
    headers = {"User-Agent": ua}

    try:
        with safe_public_get(u0, timeout=timeout, headers=headers, stream=True) as r:
            if r.status_code != 200:
                return None
            buf = bytearray()
            for chunk in r.iter_content(65536):
                if chunk:
                    buf.extend(chunk)
                    if len(buf) >= max_bytes:
                        break
        text = bytes(buf).decode("utf-8", errors="ignore")
    except (requests.RequestException, OSError, ValueError):
        return None

    found: list[str] = []
    seen: set[str] = set()

    def _add_url(raw_url: str) -> None:
        s = raw_url.strip().split("?", 1)[0]
        if not s.lower().endswith(".pdf") or not is_safe_public_http_url(s):
            return
        low = s.lower()
        if low not in seen:
            seen.add(low)
            found.append(s)

    try:
        for m in _ABS_PDF_HTTP.finditer(text):
            _add_url(m.group(0))

        for m in _HREF_PDF.finditer(text):
            path = m.group(2).strip()
            if path.startswith("//"):
                _add_url(urljoin(u0, path))
            elif path.lower().startswith(("http://", "https://")):
                _add_url(path)
            elif _is_relative_pdf_path(path):
                _add_url(urljoin(u0, path))

        # Meta content="..." PDF URLs (e.g., NeurIPS proceedings abstract pages)
        if not found:
            for pat in (_META_CONTENT_PDF, _META_CONTENT_PDF_ALT):
                for m in pat.finditer(text):
                    path = m.group(1).strip()
                    if path.lower().startswith(("http://", "https://")):
                        _add_url(path)
                    elif _is_relative_pdf_path(path):
                        _add_url(urljoin(u0, path))

        if not found:
            for m in _GENERIC_CONTENT_PDF.finditer(text):
                path = m.group(1).strip()
                if path.lower().startswith(("http://", "https://")):
                    _add_url(path)
                elif _is_relative_pdf_path(path):
                    _add_url(urljoin(u0, path))

        if not found:
            for m in _OJS_VIEW_OR_DL.finditer(text):
                path = m.group(2).strip()
                abs_u = urljoin(u0, path)
                if "/article/view/" in abs_u:
                    abs_u = abs_u.replace("/article/view/", "/article/download/", 1)
                low = abs_u.lower()
                if "citationstylelanguage" in low or low in seen:
                    continue
                if not is_safe_public_http_url(abs_u):
                    continue
                seen.add(low)
                found.append(abs_u)
    except re.error:
        return None

    if not found:
        return None
    return _same_site_pdf_preference(u0, found)
