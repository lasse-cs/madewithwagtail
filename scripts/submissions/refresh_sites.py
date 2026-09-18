#!/usr/bin/env -S uv run --script
"""Periodically refresh showcase listings: sites and developer profiles.

Subcommands (CLI wiring lands with the report/summary tasks):
    sites     re-scan stale site entries, update screenshots/technologies/URLs
    profiles  re-scan stale developer profiles, update logos/online profiles

Each run edits content files in place and writes one CSV report under
local/refresh/<UTC timestamp>/report.csv. Reuses the submission pipeline's
networking, detection, and screenshot code from the pipeline package.
"""

# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.28",
#   "playwright==1.62.0",
#   "pillow>=11",
#   "pydantic>=2.10",
#   "python-slugify>=8",
#   "pyyaml>=6",
#   "wappalyzer>=2,<3",
# ]
# ///

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from slugify import slugify

from pipeline.browser import capture_screenshot
from pipeline.content import read_frontmatter, write_frontmatter_file
from pipeline.detection import (
    classify_technologies,
    detect_wagtail,
    wappalyzer_technologies,
)
from pipeline.images import encode_logo
from pipeline.logos import gather_logo_candidates, select_largest_logo
from pipeline.net import fetch_page
from pipeline.proposal import utcnow

REPORT_FIELDS = [
    "kind",
    "developer_slug",
    "site_slug",
    "name",
    "url",
    "final_url",
    "status",
    "is_wagtail",
    "detected_title",
    "title_match",
    "detected_technologies",
    "changes",
    "candidate_profiles",
    "flags",
    "checked_at",
]


def base_row(kind: str, developer_slug: str, site_slug: str = "") -> dict:
    row = dict.fromkeys(REPORT_FIELDS, "")
    row.update(
        kind=kind,
        developer_slug=developer_slug,
        site_slug=site_slug,
        checked_at=utcnow().isoformat(),
    )
    return row


def frontmatter_date(value: object) -> datetime | None:
    """Parse a frontmatter timestamp; None when missing or unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_stale(data: dict, cutoff: datetime) -> bool:
    """True when the entry has no usable latest_revision_created_at, or it
    predates the cutoff — a missing date means 'never refreshed by us'."""
    revised = frontmatter_date(data.get("latest_revision_created_at"))
    return revised is None or revised < cutoff


def site_entries(
    content_dir: Path,
    developers: set[str] = frozenset(),
    sites: set[str] = frozenset(),
) -> list[tuple[str, str, Path]]:
    """All site entries as (developer_slug, site_slug, entry_dir), sorted.

    An empty filter set means 'no filter'. --site values are 'dev/site'."""
    entries = []
    for dev_dir in sorted(p for p in content_dir.iterdir() if p.is_dir()):
        if developers and dev_dir.name not in developers:
            continue
        for site_dir in sorted(p for p in dev_dir.iterdir() if p.is_dir()):
            if sites and f"{dev_dir.name}/{site_dir.name}" not in sites:
                continue
            if (site_dir / "index.md").is_file():
                entries.append((dev_dir.name, site_dir.name, site_dir))
    return entries


def profile_entries(
    content_dir: Path, developers: set[str] = frozenset()
) -> list[Path]:
    """Developer profile directories with an index.md, sorted."""
    return [
        dev_dir
        for dev_dir in sorted(p for p in content_dir.iterdir() if p.is_dir())
        if (not developers or dev_dir.name in developers)
        and (dev_dir / "index.md").is_file()
    ]


class _MetaTitleParser(HTMLParser):
    """Collects og:site_name and <title> from a page's head."""

    def __init__(self) -> None:
        super().__init__()
        self.og_site_name: str | None = None
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "meta" and self.og_site_name is None:
            mapped = dict(attrs)
            if mapped.get("property", "").casefold() == "og:site_name":
                self.og_site_name = mapped.get("content") or None
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and self.title is None:
            self.title = data


def extract_site_name(html: str) -> str | None:
    """og:site_name when declared, else the <title> text. Never raises."""
    parser = _MetaTitleParser()
    try:
        parser.feed(html)
    except Exception:  # malformed markup mid-feed still leaves partial data
        pass
    return parser.og_site_name or (parser.title.strip() if parser.title else None)


def normalize_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.lower()}"


def refresh_site(
    dev_slug: str,
    site_slug: str,
    entry_dir: Path,
    client,
    *,
    dry_run: bool = False,
    fetch=fetch_page,
    capture=capture_screenshot,
    wappalyzer=wappalyzer_technologies,
    now: datetime | None = None,
) -> dict:
    """Re-scan one site entry; edit content in place unless dry_run.

    Returns a REPORT_FIELDS row. Per spec §3: dead/error sites are reported
    only; live sites get a fresh screenshot, and technologies/site_url are
    updated when they changed, bumping latest_revision_created_at only then.
    """
    row = base_row("site", dev_slug, site_slug)
    index = entry_dir / "index.md"
    data = read_frontmatter(index)
    row["name"] = str(data.get("title") or "")
    url = str(data.get("site_url") or "")
    row["url"] = url
    if not url:
        row["status"], row["flags"] = "error", "missing-site-url"
        return row

    try:
        final_url, html = fetch(client, url)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        row["status"], row["flags"] = "dead", f"fetch-failed: {exc}"
        return row
    except Exception as exc:  # e.g. too many redirects, blocked URL
        row["status"], row["flags"] = "error", f"fetch-failed: {exc}"
        return row

    changes: list[str] = []
    flags: list[str] = []
    row["final_url"] = final_url

    signals = detect_wagtail(html)
    row["is_wagtail"] = "true" if signals else "false"
    if not signals:
        flags.append("no-wagtail-signals")

    detected_title = extract_site_name(html)
    row["detected_title"] = detected_title or ""
    title_match = normalize_title(detected_title) == normalize_title(data.get("title"))
    row["title_match"] = "true" if title_match else "false"
    if not title_match and detected_title:
        flags.append("title-mismatch")

    new_data = dict(data)
    if origin_of(final_url) != origin_of(url):
        new_data["site_url"] = final_url
        changes.append("site_url")
        row["status"] = "redirected"
    else:
        row["status"] = "ok"

    classified = classify_technologies(wappalyzer(final_url))
    complementary = classified["complementary"]
    row["detected_technologies"] = ", ".join(complementary + classified["other"])
    if complementary != list(data.get("technologies") or []):
        if complementary:
            new_data["technologies"] = complementary
        else:
            new_data.pop("technologies", None)
        changes.append("technologies")

    if dry_run:
        changes.append("screenshot")  # planned only
    else:
        screenshot_path = entry_dir / f"{site_slug}.fill-1200x996.webp"
        try:
            capture(final_url, screenshot_path)
            changes.append("screenshot")
        except Exception as exc:
            flags.append(f"screenshot-failed: {exc}")

    if new_data != data:
        changes.append("frontmatter")

    if changes and not dry_run:
        new_data["latest_revision_created_at"] = (now or utcnow()).isoformat()
        write_frontmatter_file(index, new_data)

    row["changes"] = ";".join(changes)
    row["flags"] = ";".join(flags)
    return row


PROFILE_LINK_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)

PROFILE_LINK_DOMAINS = frozenset(
    {
        "linkedin.com",
        "github.com",
        "x.com",
        "twitter.com",
        "mastodon.social",
        "bsky.app",
        "youtube.com",
        "facebook.com",
        "instagram.com",
        "dribbble.com",
        "behance.net",
    }
)


def extract_profile_links(html: str, base_url: str) -> list[str]:
    """Absolute hrefs on known profile domains, first-seen order, deduped."""
    base = httpx.URL(base_url)
    seen: set[str] = set()
    links: list[str] = []
    for href in PROFILE_LINK_HREF_RE.findall(html):
        if not (href := href.strip()):
            continue
        try:
            absolute = str(base.join(href))
        except httpx.InvalidURL:
            continue
        host = (
            (urlsplit(absolute).hostname or "")
            .casefold()
            .removesuffix(".")
            .removeprefix("www.")
        )
        if host not in PROFILE_LINK_DOMAINS:
            continue
        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)
    return links


def link_matches_developer(
    url: str, developer_title: str, github_user: str | None
) -> bool:
    """Conservative match: the URL's alphanumeric soup contains the GitHub
    username or the slugified developer title. Used to decide which found
    profile links are safe to write into online_profiles (spec §4)."""
    hay = re.sub(r"[^a-z0-9]", "", url.casefold())
    if github_user:
        user = re.sub(r"[^a-z0-9]", "", github_user.casefold())
        if len(user) >= 3 and user in hay:
            return True
    slug = slugify(developer_title or "", separator="")
    return bool(slug) and slug in hay


def refresh_profile(
    dev_slug: str,
    dev_dir: Path,
    client,
    *,
    dry_run: bool = False,
    fetch=fetch_page,
    gather=gather_logo_candidates,
    select=select_largest_logo,
    now: datetime | None = None,
) -> dict:
    """Re-scan one developer profile; edit content in place unless dry_run.

    Per spec §4: the company URL's liveness is reported, never auto-edited;
    the logo is refreshed from the company site; only unambiguous profile
    links are written to online_profiles (the rest are candidates in the
    report for later review).
    """
    row = base_row("profile", dev_slug)
    index = dev_dir / "index.md"
    data = read_frontmatter(index)
    row["name"] = str(data.get("title") or "")
    company_url = str(data.get("company_url") or "")
    row["url"] = company_url
    if not company_url:
        row["status"], row["flags"] = "skipped", "no-company-url"
        return row

    try:
        final_url, html = fetch(client, company_url)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        row["status"], row["flags"] = "dead", f"fetch-failed: {exc}"
        return row
    except Exception as exc:
        row["status"], row["flags"] = "error", f"fetch-failed: {exc}"
        return row

    row["final_url"] = final_url
    row["status"] = "ok"
    changes: list[str] = []
    flags: list[str] = []
    new_data = dict(data)

    candidates = extract_profile_links(html, final_url)
    row["candidate_profiles"] = " ".join(candidates)
    unambiguous = [
        link
        for link in candidates
        if link_matches_developer(
            link, str(data.get("title") or ""), data.get("github_user")
        )
    ]
    current = list(data.get("online_profiles") or [])
    merged = current + [link for link in unambiguous if link not in current]
    if merged != current:
        new_data["online_profiles"] = merged
        changes.append("online_profiles")

    logo_path = dev_dir / f"{dev_slug}.max-120x120.webp"
    try:
        logo_bytes = select(client, gather(client, html, final_url, None))
        if logo_bytes:
            encoded = encode_logo(logo_bytes)
            existing = logo_path.read_bytes() if logo_path.exists() else b""
            if encoded != existing:
                if not dry_run:
                    logo_path.write_bytes(encoded)
                changes.append("logo")
        else:
            flags.append("no-logo-found")
    except Exception as exc:
        flags.append(f"logo-failed: {exc}")

    if changes and not dry_run:
        new_data["latest_revision_created_at"] = (now or utcnow()).isoformat()
        write_frontmatter_file(index, new_data)

    row["changes"] = ";".join(changes)
    row["flags"] = ";".join(flags)
    return row


def write_report(rows: list[dict], out_dir: Path) -> Path:
    """One CSV per invocation: the run's high-level status report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def console_summary(rows: list[dict], *, dry_run: bool = False) -> None:
    print(
        f"Scanned {len(rows)} entries{' (dry run — nothing written)' if dry_run else ''}."
    )
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    for status in sorted(by_status):
        print(f"  {status}: {by_status[status]}")
    for row in rows:
        if row["status"] == "dead":
            label = (
                f"{row['developer_slug']}/{row['site_slug']}"
                if row["site_slug"]
                else row["developer_slug"]
            )
            print(f"  DEAD: {label} — {row['url']}")
    screenshots = sum(1 for row in rows if "screenshot" in row["changes"])
    if screenshots:
        replaced = "would be replaced" if dry_run else "were replaced"
        print(
            f"\n{screenshots} screenshots {replaced}. Visually compare them "
            "and revert any that aren't meaningfully different "
            "(git checkout -- <file>), then commit the rest."
        )


DEFAULT_MAX_AGE_MONTHS = 9.0
DEFAULT_DELAY_S = 1.0
DAYS_PER_MONTH = 30.44


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--content-dir", type=Path, default=Path("src/content/developers")
    )
    parser.add_argument(
        "--max-age-months",
        type=float,
        default=DEFAULT_MAX_AGE_MONTHS,
        help="Only entries with latest_revision_created_at older than this",
    )
    parser.add_argument("--all", action="store_true", help="Ignore the age filter")
    parser.add_argument("--developer", action="append", default=[], metavar="SLUG")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_S,
        help="Seconds to wait between scans (politeness)",
    )


def report_dir(args: argparse.Namespace) -> Path:
    if args.out_dir is not None:
        return args.out_dir
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    return Path("local/refresh") / stamp


def run_scans(args, scan) -> tuple[list[dict], Path]:
    """Shared driver: iterate entries in scope, scan each, return rows + report dir."""
    cutoff = utcnow() - timedelta(days=args.max_age_months * DAYS_PER_MONTH)
    rows: list[dict] = []
    with httpx.Client(follow_redirects=True) as client:
        for item in scan.entries:
            dev_slug, slug, entry_dir, data = item
            if not args.all and not is_stale(data, cutoff):
                continue
            rows.append(scan.call(dev_slug, slug, entry_dir, client))
            time.sleep(args.delay)
    out = report_dir(args)
    if not args.dry_run:
        path = write_report(rows, out)
        print(f"Report: {path}")
    console_summary(rows, dry_run=args.dry_run)
    return rows, out


def cmd_sites(args: argparse.Namespace) -> int:
    items = [
        (dev_slug, site_slug, entry_dir, read_frontmatter(entry_dir / "index.md"))
        for dev_slug, site_slug, entry_dir in site_entries(
            args.content_dir, set(args.developer), set(getattr(args, "site", []))
        )
    ]

    class _Scan:
        entries = items

        @staticmethod
        def call(dev_slug, site_slug, entry_dir, client):
            return refresh_site(
                dev_slug, site_slug, entry_dir, client, dry_run=args.dry_run
            )

    run_scans(args, _Scan)
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    items = [
        (dev_dir.name, "", dev_dir, read_frontmatter(dev_dir / "index.md"))
        for dev_dir in profile_entries(args.content_dir, set(args.developer))
    ]

    class _Scan:
        entries = items

        @staticmethod
        def call(dev_slug, _site_slug, dev_dir, client):
            return refresh_profile(dev_slug, dev_dir, client, dry_run=args.dry_run)

    run_scans(args, _Scan)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="refresh_sites",
        description="Periodically refresh showcase site and developer listings.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sites_parser = sub.add_parser("sites", help="Re-scan site entries")
    add_common_options(sites_parser)
    sites_parser.add_argument(
        "--site",
        action="append",
        default=[],
        metavar="DEV/SITE",
        help="Narrow to these site slugs",
    )
    profiles_parser = sub.add_parser("profiles", help="Re-scan developer profiles")
    add_common_options(profiles_parser)
    args = parser.parse_args()

    try:
        return {"sites": cmd_sites, "profiles": cmd_profiles}[args.command](args)
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
