"""Tests for the render subcommand's fetch/logo logic (browser capture excluded)."""

import io
import json
from pathlib import Path

from PIL import Image

import pytest

from pipeline.browser import is_private_browser_host
from pipeline.logos import gather_logo_candidates, select_largest_logo
from pipeline.net import (
    MAX_RESPONSE_BYTES,
    check_public_url,
    fetch_page,
    probe_admin_pages,
)

FIXTURES = Path(__file__).parent / "fixtures"
WAGTAIL_HTML = (FIXTURES / "wagtail-home.html").read_text()


class FakeClient:
    """Records requests; returns canned HTML."""

    def __init__(self, responses: dict[str, tuple[int, str]]):
        self.responses = responses
        self.requested: list[str] = []

    def get(self, url, **kwargs):
        import httpx

        self.requested.append(url)
        status, text = self.responses.get(url, (404, ""))
        if status >= 400:
            raise httpx.HTTPStatusError(f"{status}", request=None, response=None)
        return FakeResponse(status, text, url)


class FakeResponse:
    def __init__(self, status_code, text, url):
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": "text/html"}
        self.url = url

    def raise_for_status(self):
        pass


LOCAL_HOSTS = ("example.com", "www.example.com", "final.example", "cdn.example")


def local_resolver(host, port, *args, **kwargs):
    """Deterministic resolver: the test hosts map to a public IP, everything
    else (including real DNS) is rejected. Keeps redirect tests hermetic —
    `final.example` is a reserved TLD that never resolves publicly."""
    if host in LOCAL_HOSTS:
        return [(2, None, None, "", ("93.184.216.34", port))]
    raise OSError(f"cannot resolve {host}")


@pytest.fixture
def url_guard(monkeypatch):
    """Point check_public_url at the deterministic resolver.

    After the pipeline split, check_public_url is resolved in the module
    of each caller: fetch_page resolves it in pipeline.net, while
    gather_logo_candidates resolves it in pipeline.logos (its from-import
    bound the original at import time). Patch both resolution sites so
    every SSRF check still runs for real with only the resolver swapped;
    select_largest_logo calls client.get directly (no URL validation).
    """
    guarded = lambda raw, resolver=None: check_public_url(raw, resolver=local_resolver)
    monkeypatch.setattr("pipeline.net.check_public_url", guarded)
    monkeypatch.setattr("pipeline.logos.check_public_url", guarded)


class TestFetchPage:
    def test_follows_redirect_chain(self, url_guard):
        hops = ["https://example.com/start", "https://www.example.com/", "https://final.example/"]

        class HopClient(FakeClient):
            def get(self, url, **kwargs):
                self.requested.append(url)
                if url != hops[-1]:
                    resp = FakeResponse(302, "", url)
                    resp.headers = {"location": hops[hops.index(url) + 1], "content-type": "text/html"}
                    return resp
                return FakeResponse(200, WAGTAIL_HTML, url)

        client = HopClient({})
        final_url, html = fetch_page(client, hops[0])
        assert final_url == hops[-1]
        assert "generator" in html
        assert client.requested == hops

    def test_rejects_redirect_to_private_host(self, url_guard):
        # No explicit port: an explicit port trips the default-port guard
        # before the public-IP check, and this test targets the IP check.
        class RedirectClient(FakeClient):
            def get(self, url, **kwargs):
                resp = FakeResponse(302, "", url)
                resp.headers = {"location": "http://127.0.0.1/", "content-type": "text/html"}
                return resp

        with pytest.raises(ValueError, match="public"):
            fetch_page(RedirectClient({}), "https://example.com")

    def test_rejects_too_many_redirects(self, url_guard):
        class LoopClient(FakeClient):
            def get(self, url, **kwargs):
                resp = FakeResponse(302, "", url)
                resp.headers = {"location": "https://example.com/next", "content-type": "text/html"}
                return resp

        with pytest.raises(ValueError, match="redirect"):
            fetch_page(LoopClient({}), "https://example.com")


class TestGatherLogoCandidates:
    def test_explicit_logo_url_first(self, url_guard):
        candidates = gather_logo_candidates(
            FakeClient({}), "", None, "https://cdn.example/logo.png"
        )
        assert candidates[0] == "https://cdn.example/logo.png"

    def test_no_developer_url_yields_no_candidates(self, url_guard):
        """Without a Developer URL, only the explicit Logo URL is a candidate —
        the submitted site's icons are never used."""
        assert gather_logo_candidates(FakeClient({}), "", None, None) == []

    def test_developer_page_link_icons(self, url_guard):
        html = '<link rel="apple-touch-icon" href="/touch.png"><link rel="icon" href="/fav.ico">'
        candidates = gather_logo_candidates(
            FakeClient({}), html, "https://example.com", None
        )
        assert candidates == [
            "https://example.com/touch.png",
            "https://example.com/fav.ico",
            "https://example.com/apple-touch-icon.png",
            "https://example.com/favicon.ico",
        ]

    def test_private_ip_absolute_link_excluded(self, url_guard):
        # RFC 3986 join: an absolute reference in the developer page wins over
        # the origin — it must still pass check_public_url before fetching.
        html = '<link rel="icon" href="http://169.254.169.254/latest/meta-data/">'
        candidates = gather_logo_candidates(
            FakeClient({}), html, "https://example.com", None
        )
        assert candidates == [
            "https://example.com/apple-touch-icon.png",
            "https://example.com/favicon.ico",
        ]

    def test_private_hostname_link_excluded(self, url_guard):
        # internal.example resolves to a private IP under local_resolver.
        html = '<link rel="icon" href="http://internal.example/icon.png">'
        candidates = gather_logo_candidates(
            FakeClient({}), html, "https://example.com", None
        )
        assert candidates == [
            "https://example.com/apple-touch-icon.png",
            "https://example.com/favicon.ico",
        ]

    def test_manifest_icons_after_link_icons(self, url_guard):
        """Manifest icons enter after <link> icons, ranked by declared size
        (largest first) — many sites declare only small favicons but list a
        512x512 manifest icon."""
        class ManifestClient(FakeClient):
            def get(self, url, **kwargs):
                if url == "https://example.com/manifest.webmanifest":
                    self.requested.append(url)
                    return FakeResponse(
                        200,
                        json.dumps(
                            {
                                "icons": [
                                    {"src": "/icon-192.png", "sizes": "192x192"},
                                    {"src": "/icon-512.png", "sizes": "512x512"},
                                    {"src": "https://cdn.example/maskable.png", "sizes": "any"},
                                ]
                            }
                        ),
                        url,
                    )
                return super().get(url, **kwargs)

        html = (
            '<link rel="manifest" href="/manifest.webmanifest">'
            '<link rel="icon" href="/fav.ico">'
        )
        candidates = gather_logo_candidates(
            ManifestClient({}), html, "https://example.com", None
        )
        assert candidates == [
            "https://example.com/fav.ico",
            "https://example.com/icon-512.png",
            "https://example.com/icon-192.png",
            "https://cdn.example/maskable.png",
            "https://example.com/apple-touch-icon.png",
            "https://example.com/favicon.ico",
        ]

    def test_manifest_urls_pass_ssrf_check(self, url_guard):
        """A manifest icon pointing at a private IP is dropped like any
        other candidate."""
        class PrivateManifestClient(FakeClient):
            def get(self, url, **kwargs):
                if url == "https://example.com/manifest.json":
                    self.requested.append(url)
                    return FakeResponse(
                        200,
                        json.dumps(
                            {
                                "icons": [
                                    {"src": "http://169.254.169.254/latest/", "sizes": "512x512"},
                                    {"src": "/public.png", "sizes": "192x192"},
                                ]
                            }
                        ),
                        url,
                    )
                return super().get(url, **kwargs)

        html = '<link rel="manifest" href="/manifest.json">'
        candidates = gather_logo_candidates(
            PrivateManifestClient({}), html, "https://example.com", None
        )
        assert candidates == [
            "https://example.com/public.png",
            "https://example.com/apple-touch-icon.png",
            "https://example.com/favicon.ico",
        ]

    def test_manifest_private_url_not_fetched(self, url_guard):
        """The manifest itself must pass check_public_url before fetching."""

        class GuardedClient(FakeClient):
            def get(self, url, **kwargs):
                self.requested.append(url)
                raise AssertionError(f"unwanted fetch of {url}")

        html = '<link rel="manifest" href="http://169.254.169.254/meta.json">'
        candidates = gather_logo_candidates(
            GuardedClient({}), html, "https://example.com", None
        )
        assert candidates == [
            "https://example.com/apple-touch-icon.png",
            "https://example.com/favicon.ico",
        ]


class ImageClient:
    """Serves canned image bytes; records requests."""

    def __init__(self, responses: dict[str, tuple[int, bytes]]):
        self.responses = responses
        self.requested: list[str] = []

    def get(self, url, **kwargs):
        import httpx

        self.requested.append(url)
        status, data = self.responses.get(url, (404, b""))
        if status >= 400:
            raise httpx.HTTPStatusError(f"{status}", request=None, response=None)
        return FakeImageResponse(status, data, url)


class FakeImageResponse:
    def __init__(self, status_code, data, url):
        self.status_code = status_code
        self.content = data
        self.headers = {"content-length": str(len(data))}
        self.url = url


def png_bytes(size: int, color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "PNG")
    return buf.getvalue()


class TestSelectLargestLogo:
    """Selection ranks by measured pixel size, not document order."""

    def test_prefers_larger_icon_declared_later(self, url_guard):
        client = ImageClient(
            {
                "https://example.com/fav.ico": (200, png_bytes(32)),
                "https://example.com/touch.png": (200, png_bytes(180)),
            }
        )
        html = (
            '<link rel="icon" href="/fav.ico">'
            '<link rel="apple-touch-icon" href="/touch.png">'
        )
        candidates = gather_logo_candidates(client, html, "https://example.com", None)
        selected = Image.open(io.BytesIO(select_largest_logo(client, candidates)))
        # 180px input survives encode_logo's 120px cap; the 32px one wouldn't.
        assert selected.format == "WEBP" and selected.size == (120, 120)

    def test_first_wins_on_tie(self, url_guard):
        client = ImageClient(
            {
                "https://example.com/a.png": (200, png_bytes(64, "red")),
                "https://example.com/b.png": (200, png_bytes(64, "blue")),
            }
        )
        html = (
            '<link rel="icon" href="/a.png">'
            '<link rel="apple-touch-icon" href="/b.png">'
        )
        candidates = gather_logo_candidates(client, html, "https://example.com", None)
        # Equal-size candidates: the earlier (more authoritative) one wins.
        selected = Image.open(io.BytesIO(select_largest_logo(client, candidates)))
        assert selected.format == "WEBP" and selected.size == (64, 64)
        # Every candidate is fetched: the winner is measured, not assumed.
        assert client.requested == [
            "https://example.com/a.png",
            "https://example.com/b.png",
            "https://example.com/apple-touch-icon.png",
            "https://example.com/favicon.ico",
        ]

    def test_skips_undecodable_and_error_responses(self, url_guard):
        client = ImageClient(
            {
                "https://example.com/broken.png": (200, b"not an image"),
                "https://example.com/good.png": (200, png_bytes(96)),
            }
        )
        html = '<link rel="icon" href="/broken.png"><link rel="icon" href="/good.png">'
        candidates = gather_logo_candidates(client, html, "https://example.com", None)
        selected = Image.open(io.BytesIO(select_largest_logo(client, candidates)))
        assert selected.format == "WEBP" and selected.size == (96, 96)

    def test_returns_none_when_nothing_usable(self, url_guard):
        client = ImageClient(
            {"https://example.com/broken.png": (200, b"not an image")}
        )
        html = '<link rel="icon" href="/broken.png">'
        candidates = gather_logo_candidates(client, html, "https://example.com", None)
        assert select_largest_logo(client, candidates) is None


class TestProbeAdminPages:
    def test_redirecting_admin_yields_no_signal(self, url_guard):
        """A redirecting admin page must produce no signal: redirects are only
        followed through fetch_page's per-hop SSRF-checked path."""

        class RedirectClient(FakeClient):
            def get(self, url, **kwargs):
                self.requested.append(url)
                assert "follow_redirects" not in kwargs, "admin probe must not follow redirects"
                resp = FakeResponse(302, "", url)
                resp.headers = {"location": "https://example.com/login", "content-type": "text/html"}
                return resp

        assert probe_admin_pages(RedirectClient({}), "https://example.com") == []

    def test_huge_content_length_skipped(self):
        class HugeClient(FakeClient):
            def get(self, url, **kwargs):
                resp = FakeResponse(200, "wagtail everywhere", url)
                resp.headers = {"content-type": "text/html", "content-length": str(MAX_RESPONSE_BYTES + 1)}
                resp.content = b"x" * 10
                return resp

        assert probe_admin_pages(HugeClient({}), "https://example.com") == []


class TestIsPrivateBrowserHost:
    """Route-guard classification: private/loopback IP literals and
    localhost are blocked; hostnames pass (container isolation is the
    hostname-SSRF backstop)."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://[::1]/",
            "http://[::1]:8080/",
            "http://[fe80::1]/",
            "http://[::ffff:127.0.0.1]/",
            "http://127.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.1/",
            "http://localhost/",
            "http://LOCALHOST:8000/",
        ],
    )
    def test_private_hosts_blocked(self, url):
        assert is_private_browser_host(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/page",
            "https://93.184.216.34/",
            "https://attacker.example/",
            "",
        ],
    )
    def test_public_hosts_allowed(self, url):
        assert is_private_browser_host(url) is False
