"""Developer-logo candidate discovery and selection."""

from __future__ import annotations

import io
import json
import re
import socket
from urllib.parse import urlsplit

from PIL import Image

from .images import encode_logo
from .net import MAX_RESPONSE_BYTES, _response_too_large, check_public_url

ICON_REL_RE = re.compile(
    r"<link[^>]+rel=[\"'][^\"']*(?:apple-touch-icon|icon)[^\"']*[\"'][^>]*>",
    re.IGNORECASE,
)
ICON_HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.IGNORECASE)
MANIFEST_HREF_RE = re.compile(
    r"<link[^>]+rel=[\"'][^\"']*manifest[^\"']*[\"'][^>]*>", re.IGNORECASE
)


def gather_logo_candidates(
    client: "httpx.Client",
    html: str,
    developer_url: str | None,
    logo_url: str | None,
    resolver=socket.getaddrinfo,
) -> list[str]:
    """Candidate developer-logo URLs, most authoritative first: the explicit
    Logo URL submission, then icons declared by the Developer URL's site
    (<link> icons, web app manifest entries, conventional paths).

    The submitted site is deliberately NOT a logo source: the developer's
    brand lives on their own site, and the submitter controls both the
    Logo URL and Developer URL fields. All normalized URLs run through the
    same SSRF checks as page fetches — candidates from attacker-controlled
    HTML must never bypass check_public_url."""
    import httpx

    candidates: list[str] = []

    def add(raw: str, origin: str) -> None:
        try:
            absolute = str(httpx.URL(origin).join(raw))
        except ValueError:
            return
        try:
            validated = check_public_url(absolute, resolver=resolver)
        except Exception:
            return  # best-effort: invalid/private candidates are simply skipped
        if validated not in candidates:
            candidates.append(validated)

    if logo_url:
        add(logo_url, logo_url)
    if not developer_url:
        return candidates

    developer_parts = urlsplit(developer_url)
    developer_origin = f"{developer_parts.scheme}://{developer_parts.netloc}"
    for tag in ICON_REL_RE.findall(html):
        match = ICON_HREF_RE.search(tag)
        if match:
            add(match.group(1), developer_origin)
    # Web app manifest: many sites declare only small favicon links but
    # list large icons (commonly 512x512) in their manifest. Best-effort:
    # an unreadable or non-JSON manifest is skipped.
    for tag in MANIFEST_HREF_RE.findall(html):
        match = ICON_HREF_RE.search(tag)
        if not match:
            continue
        try:
            manifest_url = check_public_url(
                str(httpx.URL(developer_origin).join(match.group(1))), resolver=resolver
            )
            manifest = json.loads(client.get(manifest_url, timeout=5).text)
            icons = manifest.get("icons")
            if not isinstance(icons, list):
                continue
        except Exception:
            continue  # best-effort: unreachable or malformed manifest

        def manifest_entry_size(entry: object) -> int:
            """Largest dimension of a manifest icon's declared sizes.

            W3C format is "sizes": "512x512"; also accepts an object with
            width/height fields. Multiple sizes rank by the largest.
            """
            if not isinstance(entry, dict):
                return 0
            sizes = entry.get("sizes")
            declared: list[int] = []
            if isinstance(sizes, str):
                for token in sizes.split():
                    dims = token.lower().split("x")
                    if len(dims) == 2 and dims[0].isdigit() and dims[1].isdigit():
                        declared.extend((int(dims[0]), int(dims[1])))
            elif isinstance(sizes, (list, dict)):
                values = sizes if isinstance(sizes, list) else sizes.values()
                for value in values:
                    if isinstance(value, (int, float)):
                        declared.append(int(value))
            return max(declared, default=0)

        sized = sorted(
            enumerate(icons),
            key=lambda pair: manifest_entry_size(pair[1]),
            reverse=True,
        )
        for _, entry in sized:
            if isinstance(entry, dict) and isinstance(entry.get("src"), str):
                add(entry["src"], developer_origin)
    add("/apple-touch-icon.png", developer_origin)
    add("/favicon.ico", developer_origin)
    return candidates


def select_largest_logo(client: "httpx.Client", candidates: list[str]) -> bytes | None:
    """Download logo candidates and return the largest decodable image.

    Pages declare icons in arbitrary order (a 16px <link rel="icon">
    commonly precedes the 180px apple-touch-icon), and sizes attributes are
    unreliable across implementations, so every candidate is measured after
    download. Ties keep the earlier candidate — the docstring ordering of
    gather_logo_candidates is most-authoritative-first. Any candidate that
    errors, is oversized, or fails to decode is skipped.
    """
    import httpx

    measured: list[tuple[int, int, bytes]] = []
    for index, candidate in enumerate(candidates):
        try:
            response = client.get(candidate, timeout=10)
            if response.status_code != 200 or _response_too_large(response):
                continue
            # No magic-byte sniffing (it misses ICO favicons): try to decode
            # + encode with Pillow; any failure means "not a usable logo".
            # UnidentifiedImageError is an OSError.
            data = response.content[:MAX_RESPONSE_BYTES]
            width, height = Image.open(io.BytesIO(data)).size
            measured.append((width * height, -index, encode_logo(data)))
        except (httpx.HTTPError, ValueError, OSError):
            continue
    if not measured:
        return None
    return max(measured)[2]
