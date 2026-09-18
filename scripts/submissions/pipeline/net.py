"""SSRF-safe URL validation and page fetching."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

# Submissions must not point at our own hosting (self-DoS guard, spec §Security 7).
HOST_BLOCKLIST_SUFFIXES = (
    "github.com",
    "github.io",
    "githubusercontent.com",
)


def _is_browsable_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return False
    return ip.is_global


def check_public_url(raw: str, resolver=socket.getaddrinfo) -> str:
    """Validate a user-supplied URL for browsing; return the normalized URL.

    Enforces the spec's SSRF rules: http(s) only, default port only, no
    userinfo, public DNS resolution, not on the host blocklist.
    Raises pydantic ValidationError with a descriptive message otherwise.
    """
    from pydantic import ValidationError as _VE

    def bad(reason: str) -> Exception:
        # pydantic 2 requires a title; single-dict construction (as the plan
        # sketched) raises TypeError. Build via from_exception_data instead.
        return _VE.from_exception_data(
            "URL validation",
            [
                {
                    "type": "value_error",
                    "loc": ("url",),
                    "input": raw,
                    "ctx": {"error": ValueError(reason)},
                }
            ],
        )

    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise bad(f"URL does not parse: {exc}") from exc

    if parts.scheme not in ("http", "https"):
        raise bad(f"Scheme must be http or https, got {parts.scheme!r}")
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise bad("URL must not contain credentials (userinfo)")
    try:
        explicit_port = parts.port
    except ValueError as exc:
        raise bad("URL has an invalid port") from exc
    if explicit_port is not None:
        raise bad("URL must use the default port")

    hostname = parts.hostname or ""
    if not hostname:
        raise bad("URL has no hostname")

    host_lower = hostname.rstrip(".").lower()
    if any(
        host_lower == s or host_lower.endswith("." + s) for s in HOST_BLOCKLIST_SUFFIXES
    ):
        raise bad(f"Host {host_lower} is on the submission blocklist")

    # IP literals: validate directly. Hostnames: resolve.
    try:
        ips = [ipaddress.ip_address(host_lower)]
    except ValueError:
        try:
            infos = resolver(
                host_lower, parts.port or (443 if parts.scheme == "https" else 80)
            )
        except Exception as exc:
            raise bad(f"Hostname {host_lower} does not resolve") from exc
        try:
            ips = [ipaddress.ip_address(info[4][0]) for info in infos]
        except ValueError as exc:
            raise bad(f"Resolver returned a malformed address: {exc}") from exc

    # An empty resolution list must not pass vacuously through all().
    if not ips or not all(_is_browsable_ip(ip) for ip in ips):
        raise bad(f"Host {host_lower} does not resolve to a public-only address")

    # str(SplitResult) returns the repr on Python 3.14+; geturl() returns the
    # URL string on every supported version.
    return parts.geturl()

ADMIN_PATHS = ("/admin/", "/cms/", "/cms-admin/")


def _response_too_large(response) -> bool:
    """Best-effort size gate: honor content-length when present."""
    headers = getattr(response, "headers", None) or {}
    content_length = headers.get("content-length")
    return bool(
        content_length
        and content_length.isdigit()
        and int(content_length) > MAX_RESPONSE_BYTES
    )


def _capped_text(response) -> str:
    """Decode at most MAX_RESPONSE_BYTES of the response body."""
    if _response_too_large(response):
        return ""
    content = getattr(response, "content", None)
    if content is None:
        return response.text
    return content[:MAX_RESPONSE_BYTES].decode("utf-8", errors="replace")


def probe_admin_pages(client: "httpx.Client", origin: str) -> list[str]:
    """Best-effort admin probe; every failure mode is silently skipped.

    No redirect following: fetch_page's per-hop SSRF checks are the only
    validated network path, so a redirecting admin simply yields no signal.
    """
    import httpx

    signals = []
    for path in ADMIN_PATHS:
        try:
            response = client.get(origin + path, timeout=5)
        except httpx.HTTPError:
            continue
        body = _capped_text(response)
        if response.status_code == 200 and "wagtail" in body.casefold():
            signals.append(f"Wagtail admin page at {path}")
    return signals

MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 3_000_000
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def fetch_page(client: "httpx.Client", url: str) -> tuple[str, str]:
    """GET a page following redirects manually, SSRF-checking every hop.

    Status-code redirect detection and getattr fallbacks keep this testable
    against minimal fake responses (no httpx.Response required).
    """
    import httpx

    current = check_public_url(url)
    for _ in range(MAX_REDIRECTS):
        response = client.get(
            current,
            timeout=15,
            headers={"user-agent": "madewithwagtail-submission-bot"},
        )
        if response.status_code in REDIRECT_STATUSES:
            location = response.headers.get("location", "")
            if not location:
                raise ValueError("Redirect without a location header")
            current = check_public_url(str(httpx.URL(str(response.url)).join(location)))
            continue
        # Cap the body at MAX_RESPONSE_BYTES before decoding.
        content = getattr(response, "content", None)
        if content is None:
            content = response.text.encode("utf-8", errors="replace")
        encoding = getattr(response, "encoding", None) or "utf-8"
        text = content[:MAX_RESPONSE_BYTES].decode(encoding, errors="replace")
        return str(response.url), text
    raise ValueError(f"More than {MAX_REDIRECTS} redirects")
