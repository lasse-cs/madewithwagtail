import socket

import pytest
from pydantic import ValidationError

from pipeline.net import check_public_url


class FakeResolutionError(Exception):
    pass


def fake_resolver(host, port, *args, **kwargs):
    # 93.184.216.34 is genuinely global; the plan originally used a
    # documentation-range IP (203.0.113.10), which Python 3.13+ classifies
    # as is_global=False, so it can't stand in for a public address.
    table = {
        "example.com": [("93.184.216.34",)],
        "internal.example": [("10.1.2.3",)],
        "rebound.example": [("127.0.0.1",)],
        "v6private.example": [("fd00::1",)],
        "mixed.example": [("10.1.2.3",), ("93.184.216.34",)],
    }
    if host not in table:
        raise FakeResolutionError(f"cannot resolve {host}")
    return [(socket.AF_INET, None, None, "", (ip, port)) for (ip,) in table[host]]


class TestCheckPublicUrl:
    def test_accepts_https_url(self):
        url = check_public_url("https://example.com/some/path", resolver=fake_resolver)
        assert url == "https://example.com/some/path"

    def test_downgrades_http_kept_as_http(self):
        url = check_public_url("http://example.com", resolver=fake_resolver)
        assert url == "http://example.com"

    def test_rejects_non_http_scheme(self):
        with pytest.raises(ValidationError):
            check_public_url("ftp://example.com", resolver=fake_resolver)

    def test_rejects_javascript_scheme(self):
        with pytest.raises(ValidationError):
            check_public_url("javascript:alert(1)", resolver=fake_resolver)

    def test_rejects_userinfo(self):
        with pytest.raises(ValidationError):
            check_public_url("https://user:pass@example.com", resolver=fake_resolver)

    def test_rejects_explicit_port(self):
        with pytest.raises(ValidationError):
            check_public_url("https://example.com:8080", resolver=fake_resolver)

    def test_rejects_invalid_port(self):
        # An out-of-range port must raise ValidationError, not a bare ValueError.
        with pytest.raises(ValidationError):
            check_public_url("https://example.com:99999", resolver=fake_resolver)
        with pytest.raises(ValidationError):
            check_public_url("https://example.com:abc", resolver=fake_resolver)

    def test_rejects_private_ipv4(self):
        with pytest.raises(ValidationError):
            check_public_url("https://internal.example", resolver=fake_resolver)

    def test_rejects_loopback(self):
        with pytest.raises(ValidationError):
            check_public_url("https://rebound.example", resolver=fake_resolver)

    def test_rejects_private_ipv6(self):
        with pytest.raises(ValidationError):
            check_public_url("https://v6private.example", resolver=fake_resolver)

    def test_rejects_unresolvable(self):
        with pytest.raises(ValidationError):
            check_public_url("https://nxdomain.invalid", resolver=fake_resolver)

    def test_rejects_empty_resolution(self):
        # An injectable resolver may return zero records; all() over an empty
        # list is vacuously True, so this must be rejected explicitly.
        def empty_resolver(host, port, *args, **kwargs):
            return []

        with pytest.raises(ValidationError):
            check_public_url("https://empty.example", resolver=empty_resolver)

    def test_rejects_github_host(self):
        with pytest.raises(ValidationError):
            check_public_url("https://wagtail.github.io", resolver=fake_resolver)

    def test_rejects_github_pages_subdomain(self):
        with pytest.raises(ValidationError):
            check_public_url("https://github.io", resolver=fake_resolver)

    def test_ip_literal_private(self):
        with pytest.raises(ValidationError):
            check_public_url("http://169.254.169.254/latest/meta-data/", resolver=fake_resolver)

    def test_ip_literal_public_ok(self):
        url = check_public_url("https://93.184.216.34", resolver=fake_resolver)
        assert url == "https://93.184.216.34"

    def test_rejects_mixed_public_private_records(self):
        # Attacker-controlled DNS returning one private and one public
        # address: the client might connect to the private one, so the
        # URL must be rejected outright.
        with pytest.raises(ValidationError):
            check_public_url("https://mixed.example", resolver=fake_resolver)
