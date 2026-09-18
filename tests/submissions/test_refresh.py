import sys as sys_module
from datetime import datetime, timedelta, timezone

import pytest

from refresh_sites import (
    base_row,
    frontmatter_date,
    is_stale,
    profile_entries,
    site_entries,
)

NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)

SITE_INDEX_MD = """---
title: Visit Sweden
latest_revision_created_at: "2017-03-14T05:43:32.476155+13:00"
site_url: http://visitsweden.example/
---

Sweden's official website.
"""

FRESH_SITE_INDEX_MD = """---
title: Fresh Site
latest_revision_created_at: "%s"
site_url: https://fresh.example/
---

Recently updated.
""" % (NOW - timedelta(days=30)).isoformat()

PROFILE_INDEX_MD = """---
title: Fröjd
latest_revision_created_at: "2022-01-08T14:48:21.885038+13:00"
company_url: https://www.frojd.example/
github_user: frojd
online_profiles: []
---

Fröjd is a web agency.
"""


def make_content_tree(tmp_path):
    """developers/frojd with one stale site, one fresh site, and a profile."""
    dev = tmp_path / "developers" / "frojd"
    (dev / "visit-sweden").mkdir(parents=True)
    (dev / "visit-sweden" / "index.md").write_text(SITE_INDEX_MD, encoding="utf-8")
    (dev / "fresh-site").mkdir()
    (dev / "fresh-site" / "index.md").write_text(FRESH_SITE_INDEX_MD, encoding="utf-8")
    (dev / "index.md").write_text(PROFILE_INDEX_MD, encoding="utf-8")
    empty = tmp_path / "developers" / "empty-dev"
    empty.mkdir(parents=True)
    (empty / "index.md").write_text(PROFILE_INDEX_MD, encoding="utf-8")  # no sites
    return tmp_path / "developers"


class TestFrontmatterDate:
    def test_parses_iso_string(self):
        assert frontmatter_date("2017-03-14T05:43:32.476155+13:00") is not None

    def test_missing_is_none(self):
        assert frontmatter_date(None) is None
        assert frontmatter_date("") is None
        assert frontmatter_date(42) is None

    def test_unparseable_is_none(self):
        assert frontmatter_date("not a date") is None

    def test_naive_gets_utc(self):
        parsed = frontmatter_date("2020-01-01T00:00:00")
        assert parsed.tzinfo == timezone.utc


class TestIsStale:
    def test_old_date_is_stale(self):
        assert is_stale(
            {"latest_revision_created_at": "2020-01-01T00:00:00+00:00"},
            NOW - timedelta(days=365),
        )

    def test_recent_date_is_not_stale(self):
        assert not is_stale(
            {"latest_revision_created_at": (NOW - timedelta(days=30)).isoformat()},
            NOW - timedelta(days=365),
        )

    def test_missing_date_is_stale(self):
        assert is_stale({}, NOW - timedelta(days=365))


class TestEntries:
    def test_site_entries_lists_all(self, tmp_path):
        content_dir = make_content_tree(tmp_path)
        entries = site_entries(content_dir)
        assert [(dev, site) for dev, site, _ in entries] == [
            ("frojd", "fresh-site"),
            ("frojd", "visit-sweden"),
        ]

    def test_site_entries_developer_filter(self, tmp_path):
        content_dir = make_content_tree(tmp_path)
        entries = site_entries(content_dir, developers={"empty-dev"})
        assert entries == []

    def test_site_entries_site_filter(self, tmp_path):
        content_dir = make_content_tree(tmp_path)
        entries = site_entries(content_dir, sites={"frojd/visit-sweden"})
        assert [site for _, site, _ in entries] == ["visit-sweden"]

    def test_profile_entries(self, tmp_path):
        content_dir = make_content_tree(tmp_path)
        profiles = profile_entries(content_dir)
        assert [p.name for p in profiles] == ["empty-dev", "frojd"]


class TestBaseRow:
    def test_has_all_report_fields_empty(self):
        row = base_row("site", "frojd", "visit-sweden")
        assert set(row) == set(
            [
                "kind", "developer_slug", "site_slug", "name", "url", "final_url",
                "status", "is_wagtail", "detected_title", "title_match",
                "detected_technologies", "changes", "candidate_profiles", "flags",
                "checked_at",
            ]
        )
        assert row["kind"] == "site"
        assert row["developer_slug"] == "frojd"
        assert row["site_slug"] == "visit-sweden"
        assert row["checked_at"]  # timestamped


from refresh_sites import extract_site_name, normalize_title


class TestExtractSiteName:
    def test_prefers_og_site_name(self):
        html = "<title>Page</title><meta property='og:site_name' content='Visit Sweden'>"
        assert extract_site_name(html) == "Visit Sweden"

    def test_falls_back_to_title(self):
        assert extract_site_name("<html><head><title>  Visit  Sweden </title>") == "Visit  Sweden"

    def test_empty_html_is_none(self):
        assert extract_site_name("") is None
        assert extract_site_name("<html><body></body></html>") is None

    def test_malformed_html_does_not_raise(self):
        assert extract_site_name("<title>Unclosed") == "Unclosed"


class TestNormalizeTitle:
    def test_collapses_whitespace_and_casefolds(self):
        assert normalize_title("  Visit   Sweden ") == "visit sweden"

    def test_none_is_empty(self):
        assert normalize_title(None) == ""


import httpx as httpx_module

from refresh_sites import refresh_site


def make_entry(tmp_path, text=SITE_INDEX_MD, slug="visit-sweden"):
    entry = tmp_path / slug
    entry.mkdir()
    (entry / "index.md").write_text(text, encoding="utf-8")
    return entry


def fake_fetch(final_url, html):
    def _fetch(client, url):
        return final_url, html
    return _fetch


OG_HTML = (
    "<meta property='og:site_name' content='Visit Sweden'>"
    "<a href='/media/images/photo.width-100.original.jpg'>x</a>"
)

# --- liveness -----------------------------------------------------------

def test_refresh_site_dead_reports_without_edits(tmp_path):
    entry = make_entry(tmp_path)
    before = (entry / "index.md").read_bytes()

    def dead(client, url):
        raise httpx_module.ConnectError("no route to host")

    row = refresh_site("frojd", "visit-sweden", entry, None, fetch=dead, capture=object())
    assert row["status"] == "dead"
    assert "fetch-failed" in row["flags"]
    assert (entry / "index.md").read_bytes() == before
    assert not (entry / "visit-sweden.fill-1200x996.webp").exists()


def test_refresh_site_error_status_on_non_http_error(tmp_path):
    entry = make_entry(tmp_path)

    def too_many_redirects(client, url):
        raise ValueError("More than 5 redirects")

    row = refresh_site("frojd", "visit-sweden", entry, None,
                       fetch=too_many_redirects, capture=object())
    assert row["status"] == "error"


# --- live scan ----------------------------------------------------------

def test_refresh_site_updates_technologies_and_screenshot(tmp_path):
    entry = make_entry(tmp_path)

    def fake_capture(url, out_path):
        out_path.write_bytes(b"png-bytes")

    def fake_wappalyzer(url):
        return {"React": {"version": "", "categories": ["JavaScript frameworks"]},
                "PHP": {"version": "", "categories": ["Programming languages"]}}

    row = refresh_site(
        "frojd", "visit-sweden", entry, None,
        fetch=fake_fetch("http://visitsweden.example/", OG_HTML),
        capture=fake_capture, wappalyzer=fake_wappalyzer,
    )
    assert row["status"] == "ok"
    assert row["is_wagtail"] == "true"
    assert row["detected_title"] == "Visit Sweden"
    assert row["title_match"] == "true"
    assert "technologies" in row["changes"]
    assert "screenshot" in row["changes"]
    assert "React" in row["detected_technologies"]

    import yaml
    data = yaml.safe_load(
        (entry / "index.md").read_text(encoding="utf-8").split("---")[1]
    )
    assert data["technologies"] == ["React"]
    # Timestamp was bumped from the 2017 value.
    assert data["latest_revision_created_at"].startswith("20")
    assert (entry / "visit-sweden.fill-1200x996.webp").read_bytes() == b"png-bytes"


def test_refresh_site_noop_leaves_file_untouched(tmp_path):
    entry = make_entry(tmp_path)
    before = (entry / "index.md").read_bytes()

    def failing_capture(url, out_path):
        raise RuntimeError("screenshot unavailable")

    def empty_wappalyzer(url):
        return {}

    row = refresh_site(
        "frojd", "visit-sweden", entry, None,
        fetch=fake_fetch("http://visitsweden.example/", OG_HTML),
        capture=failing_capture, wappalyzer=empty_wappalyzer,
    )
    assert row["status"] == "ok"
    assert row["changes"] == ""  # no frontmatter diff, screenshot failed
    assert "screenshot-failed" in row["flags"]
    assert (entry / "index.md").read_bytes() == before


def test_refresh_site_redirect_updates_site_url(tmp_path):
    entry = make_entry(tmp_path)
    row = refresh_site(
        "frojd", "visit-sweden", entry, None,
        fetch=fake_fetch("https://visitsweden.example/", OG_HTML),
        capture=lambda url, out: out.write_bytes(b"x"),
        wappalyzer=lambda url: {},
    )
    assert row["status"] == "redirected"
    import yaml
    data = yaml.safe_load(
        (entry / "index.md").read_text(encoding="utf-8").split("---")[1]
    )
    assert data["site_url"] == "https://visitsweden.example/"


def test_refresh_site_title_mismatch_is_flagged_not_edited(tmp_path):
    entry = make_entry(tmp_path)
    row = refresh_site(
        "frojd", "visit-sweden", entry, None,
        fetch=fake_fetch("http://visitsweden.example/", "<title>Something Else</title>"),
        capture=lambda url, out: out.write_bytes(b"x"),
        wappalyzer=lambda url: {},
    )
    assert row["title_match"] == "false"
    assert "title-mismatch" in row["flags"]
    import yaml
    data = yaml.safe_load(
        (entry / "index.md").read_text(encoding="utf-8").split("---")[1]
    )
    assert data["title"] == "Visit Sweden"  # untouched


def test_refresh_site_dry_run_writes_nothing(tmp_path):
    entry = make_entry(tmp_path)
    before = (entry / "index.md").read_bytes()

    def fake_capture(url, out_path):
        out_path.write_bytes(b"png-bytes")

    row = refresh_site(
        "frojd", "visit-sweden", entry, None,
        fetch=fake_fetch("http://visitsweden.example/", OG_HTML),
        capture=fake_capture, wappalyzer=lambda url: {},
        dry_run=True,
    )
    assert "screenshot" in row["changes"]  # planned, not done
    assert (entry / "index.md").read_bytes() == before
    assert not (entry / "visit-sweden.fill-1200x996.webp").exists()


from refresh_sites import extract_profile_links, link_matches_developer

LINKS_HTML = """
<a href="https://www.linkedin.com/company/frojd/">LinkedIn</a>
<a href="https://github.com/frojd">GitHub</a>
<a href="https://x.com/frojdagency">X</a>
<a href="https://unrelated.example/team">Team</a>
<a href="/about">About</a>
"""


class TestExtractProfileLinks:
    def test_keeps_only_known_profile_domains(self):
        links = extract_profile_links(LINKS_HTML, "https://www.frojd.example/")
        assert "https://www.linkedin.com/company/frojd/" in links
        assert "https://github.com/frojd" in links
        assert "https://x.com/frojdagency" in links
        assert not any("unrelated.example" in link for link in links)
        assert not any(link.endswith("/about") for link in links)

    def test_deduplicates(self):
        html = '<a href="https://github.com/frojd">a</a><a href="https://github.com/frojd">b</a>'
        assert extract_profile_links(html, "https://frojd.example/") == [
            "https://github.com/frojd"
        ]

    def test_empty_html(self):
        assert extract_profile_links("", "https://frojd.example/") == []


class TestLinkMatchesDeveloper:
    def test_github_user_match(self):
        assert link_matches_developer(
            "https://github.com/frojd", "Fröjd", github_user="frojd"
        )

    def test_slugified_title_match(self):
        assert link_matches_developer(
            "https://www.linkedin.com/company/frojd/", "Fröjd", github_user=None
        )

    def test_multiword_slug_matches_plain_concatenation(self):
        assert link_matches_developer(
            "https://www.linkedin.com/company/rockkitchenharris/",
            "Rock Kitchen Harris",
            github_user=None,
        )

    def test_no_match(self):
        assert not link_matches_developer(
            "https://www.linkedin.com/company/someone-else/", "Fröjd", github_user="frojd"
        )


from refresh_sites import refresh_profile

COMPANY_HTML = """
<img src="/static/logo-big.png">
<a href="https://www.linkedin.com/company/frojd/">LinkedIn</a>
<a href="https://github.com/someone-else">Other person's GitHub</a>
"""


def fake_profile_fetch(final_url, html):
    def _fetch(client, url):
        return final_url, html
    return _fetch


def make_dev_dir(tmp_path, text=PROFILE_INDEX_MD, slug="frojd"):
    dev = tmp_path / slug
    dev.mkdir(parents=True)
    (dev / "index.md").write_text(text, encoding="utf-8")
    return dev


def fake_logo_bytes() -> bytes:
    """A real 200x100 PNG: encode_logo must be able to decode it."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (200, 100), (10, 10, 10)).save(buf, "PNG")
    return buf.getvalue()


def fake_gather(logo_bytes: bytes | None):
    def _gather(client, html, developer_url, logo_url):
        return ["https://www.frojd.example/static/logo-big.png"] if logo_bytes else []

    def _select(client, candidates):
        return logo_bytes

    return _gather, _select


def test_refresh_profile_updates_logo_and_online_profiles(tmp_path):
    import yaml

    dev = make_dev_dir(tmp_path)
    gather, select = fake_gather(fake_logo_bytes())
    row = refresh_profile(
        "frojd", dev, None,
        fetch=fake_profile_fetch("https://www.frojd.example/", COMPANY_HTML),
        gather=gather, select=select,
    )
    assert row["status"] == "ok"
    assert "logo" in row["changes"]
    assert "online_profiles" in row["changes"]
    # Unambiguous link written, ambiguous one only reported.
    assert row["candidate_profiles"].count("linkedin.com") == 1
    assert "github.com/someone-else" in row["candidate_profiles"]
    assert (dev / "frojd.max-120x120.webp").exists()
    from pipeline.images import assert_webp

    assert_webp((dev / "frojd.max-120x120.webp").read_bytes())  # re-encoded, valid WebP
    data = yaml.safe_load(
        (dev / "index.md").read_text(encoding="utf-8").split("---")[1]
    )
    assert data["online_profiles"] == ["https://www.linkedin.com/company/frojd/"]
    assert data["company_url"] == "https://www.frojd.example/"  # untouched


def test_refresh_profile_dead_company_url_reports_only(tmp_path):
    dev = make_dev_dir(tmp_path)
    before = (dev / "index.md").read_bytes()

    def dead(client, url):
        raise httpx_module.ConnectError("refused")

    row = refresh_profile("frojd", dev, None, fetch=dead, gather=None, select=None)
    assert row["status"] == "dead"
    assert (dev / "index.md").read_bytes() == before


def test_refresh_profile_no_company_url_skipped(tmp_path):
    text = PROFILE_INDEX_MD.replace(
        'company_url: https://www.frojd.example/\n', ""
    )
    dev = make_dev_dir(tmp_path, text=text)
    row = refresh_profile("frojd", dev, None, fetch=None, gather=None, select=None)
    assert row["status"] == "skipped"
    assert "no-company-url" in row["flags"]


def test_refresh_profile_no_logo_found_is_flagged(tmp_path):
    dev = make_dev_dir(tmp_path)
    gather, select = fake_gather(None)
    row = refresh_profile(
        "frojd", dev, None,
        fetch=fake_profile_fetch("https://www.frojd.example/", "<p>hi</p>"),
        gather=gather, select=select,
    )
    assert "no-logo-found" in row["flags"]
    assert not (dev / "frojd.max-120x120.webp").exists()
    assert row["changes"] == ""  # no unambiguous links in COMPANY_HTML-less page


def test_refresh_profile_scans_logo_without_profile_links(tmp_path):
    # The logo scan runs against the company page unconditionally: a page
    # with no social-profile links can still declare a logo (spec §4).
    from pipeline.images import assert_webp

    dev = make_dev_dir(tmp_path)
    gather, select = fake_gather(fake_logo_bytes())
    row = refresh_profile(
        "frojd", dev, None,
        fetch=fake_profile_fetch(
            "https://www.frojd.example/", "<p>No social links here.</p>"
        ),
        gather=gather, select=select,
    )
    assert "logo" in row["changes"]
    assert row["candidate_profiles"] == ""
    assert (dev / "frojd.max-120x120.webp").exists()
    assert_webp((dev / "frojd.max-120x120.webp").read_bytes())


def test_refresh_profile_dry_run_writes_nothing(tmp_path):
    dev = make_dev_dir(tmp_path)
    before = (dev / "index.md").read_bytes()
    gather, select = fake_gather(fake_logo_bytes())
    row = refresh_profile(
        "frojd", dev, None,
        fetch=fake_profile_fetch("https://www.frojd.example/", COMPANY_HTML),
        gather=gather, select=select,
        dry_run=True,
    )
    assert "logo" in row["changes"] and "online_profiles" in row["changes"]
    assert (dev / "index.md").read_bytes() == before
    assert not (dev / "frojd.max-120x120.webp").exists()


from refresh_sites import write_report


class TestWriteReport:
    def test_writes_csv_with_header_and_rows(self, tmp_path, capsys):
        rows = [
            base_row("site", "frojd", "visit-sweden") | {"status": "ok", "changes": "screenshot"},
            base_row("profile", "frojd") | {"status": "dead", "flags": "fetch-failed: refused"},
        ]
        path = write_report(rows, tmp_path)
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert lines[0].startswith("kind,developer_slug,site_slug")
        assert "site,frojd,visit-sweden" in lines[1]
        assert "profile,frojd" in lines[2]

    def test_empty_rows_still_writes_header(self, tmp_path):
        path = write_report([], tmp_path)
        assert path.read_text(encoding="utf-8").startswith("kind,developer_slug")


class TestConsoleSummary:
    def test_mentions_screenshot_review_nudge(self, capsys):
        from refresh_sites import console_summary

        rows = [base_row("site", "frojd", "visit-sweden") | {"status": "ok", "changes": "screenshot"}]
        console_summary(rows)
        out = capsys.readouterr().out
        assert "1" in out
        assert "screenshot" in out
        assert "revert" in out

    def test_lists_dead_entries(self, capsys):
        from refresh_sites import console_summary

        rows = [base_row("site", "frojd", "gone") | {"status": "dead", "url": "https://dead.example/"}]
        console_summary(rows)
        assert "dead.example" in capsys.readouterr().out


from refresh_sites import main


def run_cli(monkeypatch, argv):
    monkeypatch.setattr(sys_module, "argv", ["refresh_sites.py", *argv])
    return main()


class TestCliSites:
    def test_dry_run_scans_and_skips_report(self, tmp_path, capsys, monkeypatch):
        import refresh_sites as rs

        content_dir = make_content_tree(tmp_path)
        out_dir = tmp_path / "out"
        monkeypatch.setattr(
            rs, "site_entries", lambda *a, **k: site_entries(content_dir)
        )
        monkeypatch.setattr(
            rs, "refresh_site",
            lambda *a, **k: base_row("site", "frojd", "visit-sweden")
            | {"status": "ok", "changes": "screenshot"},
        )
        code = run_cli(monkeypatch, [
            "sites", "--dry-run", "--all", "--out-dir", str(out_dir),
        ])
        assert code == 0
        assert not (out_dir / "report.csv").exists()  # dry-run writes nothing
        assert "screenshot" in capsys.readouterr().out

    def test_real_run_writes_report(self, tmp_path, capsys, monkeypatch):
        import refresh_sites as rs

        content_dir = make_content_tree(tmp_path)
        out_dir = tmp_path / "out"
        monkeypatch.setattr(
            rs, "site_entries", lambda *a, **k: site_entries(content_dir)
        )
        monkeypatch.setattr(
            rs, "refresh_site",
            lambda *a, **k: base_row("site", "frojd", "visit-sweden") | {"status": "ok"},
        )
        monkeypatch.setattr(rs.time, "sleep", lambda seconds: None)
        code = run_cli(monkeypatch, [
            "sites", "--all", "--out-dir", str(out_dir),
        ])
        assert code == 0
        assert (out_dir / "report.csv").exists()

    def test_age_filter_applied_by_default(self, tmp_path, monkeypatch):
        import refresh_sites as rs

        content_dir = make_content_tree(tmp_path)
        scanned = []
        monkeypatch.setattr(
            rs, "site_entries", lambda *a, **k: site_entries(content_dir)
        )
        def recording_refresh(dev_slug, site_slug, entry_dir, client, **kwargs):
            scanned.append(site_slug)
            return base_row("site", dev_slug, site_slug) | {"status": "ok"}
        monkeypatch.setattr(rs, "refresh_site", recording_refresh)
        monkeypatch.setattr(rs.time, "sleep", lambda seconds: None)
        run_cli(monkeypatch, ["sites", "--out-dir", str(tmp_path / "out")])
        # fresh-site (updated 30 days ago) is filtered out; only the stale one scans.
        assert scanned == ["visit-sweden"]

    def test_crash_exits_1(self, tmp_path, monkeypatch):
        import refresh_sites as rs

        monkeypatch.setattr(
            rs, "site_entries",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert run_cli(monkeypatch, ["sites", "--all", "--out-dir", str(tmp_path)]) == 1
