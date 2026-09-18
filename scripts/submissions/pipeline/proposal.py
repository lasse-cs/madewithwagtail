"""Issue-form parsing, the Proposal contract, and validation."""

from __future__ import annotations

import difflib
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from slugify import slugify

from .content import read_frontmatter
from .net import check_public_url


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


SLUG_RE = r"^[a-z0-9][a-z0-9-]{0,49}$"

# Controlled vocabularies for the site classification facets. Must match the
# option lists in .github/ISSUE_TEMPLATE/site-submission.yml and the values
# migrated into src/content/developers/**/index.md.
SECTOR_VALUES = frozenset(
    {
        "agriculture",
        "arts",
        "climate",
        "consumer",
        "culture",
        "education",
        "energy",
        "engineering",
        "entertainment",
        "environment",
        "finance",
        "food",
        "forestry",
        "games",
        "government",
        "healthcare",
        "hospitality",
        "industry",
        "non-profit",
        "professional services",
        "research",
        "retail",
        "science",
        "sport",
        "sustainability",
        "technology",
        "telecom",
        "travel",
    }
)
SITE_TYPE_VALUES = frozenset(
    {
        "blog",
        "documentation",
        "e-commerce",
        "events",
        "news",
        "portfolio",
        "product",
        "reports",
    }
)
CAPABILITY_VALUES = frozenset(
    {
        "3D",
        "booking",
        "chatbot",
        "headless",
        "maps",
        "multilingual",
        "multisite",
    }
)

RESERVED_SLUGS = frozenset(
    {
        "index",
        "public",
        "src",
        "dist",
        "images",
        "page",
        "developers",
        "sites",
        "api",
        "admin",
        "assets",
        "static",
    }
)


class Proposal(BaseModel):
    """The single contract between the validate, render, and publish jobs."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    issue_number: int = Field(ge=1)
    site_url: str = Field(min_length=1, max_length=2000)
    site_title: str = Field(min_length=1, max_length=80)
    site_description: str = Field(min_length=1, max_length=800)
    sector: list[str] = Field(default_factory=list)
    site_type: list[str] = Field(default_factory=list)
    capability: list[str] = Field(default_factory=list)
    developer_name: str = Field(min_length=1, max_length=80)
    developer_slug: str = Field(pattern=SLUG_RE)
    site_slug: str = Field(pattern=SLUG_RE)
    developer_exists: bool
    # Slugs of existing profiles with a similar name, when the submission
    # creates a new profile: a reviewer-facing hint against duplicates.
    similar_developers: list[str] = Field(default_factory=list)
    developer_url: str | None = Field(default=None, max_length=2000)
    developer_location: str | None = Field(default=None, max_length=100)
    lat: str | None = Field(default=None)
    lon: str | None = Field(default=None)
    github_user: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$"
    )
    logo_url: str | None = Field(default=None, max_length=2000)
    other_notes: str | None = Field(default=None, max_length=2000)
    submitted_at: datetime


class FormParseError(ValueError):
    """The issue body is not one of our form submissions."""


CHECKBOX_RE = re.compile(r"^- \[([xX]| )\] (.*)$", re.MULTILINE)

# Only the form's own labels are section boundaries: user-typed Markdown
# containing `### Something` must stay inside the previous field's content.
SECTION_RE = re.compile(
    r"^### (?P<heading>Site URL|Site title|Short description|Sector|"
    r"Site type|Capabilities|"
    r"Developer name|Developer URL|Developer location|Latitude|Longitude|GitHub username|"
    r"Logo URL|Other notes|Confirmations)[ \t]*$",
    re.MULTILINE,
)

# The form renders sections in this exact order; it is the yardstick for
# telling real sections from headings forged inside free-text fields.
FORM_HEADINGS = (
    "Site URL",
    "Site title",
    "Short description",
    "Sector",
    "Site type",
    "Capabilities",
    "Developer name",
    "Developer URL",
    "Developer location",
    "Latitude",
    "Longitude",
    "GitHub username",
    "Logo URL",
    "Other notes",
    "Confirmations",
)
FORM_ORDER = {heading: index for index, heading in enumerate(FORM_HEADINGS)}

# GitHub substitutes this literal for optional fields the submitter left
# blank; it must be treated as unset, not as submitted data.
NO_RESPONSE_PLACEHOLDER = "_No response_"


def parse_issue_form_body(
    body: str,
) -> dict[str, str | list[str] | list[tuple[str, bool]]]:
    """Parse a GitHub issue form body into {heading: content}.

    GitHub renders form issues as `### <label>` sections. Multiselect
    values arrive comma-separated; confirmations as a checkbox list.
    Blank optional fields arrive as the `_No response_` placeholder and
    are reported as unset (empty list).
    """
    matches = list(SECTION_RE.finditer(body))
    if not any(match.group("heading") == "Site URL" for match in matches):
        raise FormParseError("Issue body does not look like a site submission form.")

    def parse_section(heading: str, content: str):
        if content == NO_RESPONSE_PLACEHOLDER:
            return []
        if heading == "Confirmations":
            return [
                (label.strip(), mark.casefold() == "x")
                for mark, label in CHECKBOX_RE.findall(content)
            ]
        if heading in ("Sector", "Site type", "Capabilities"):
            return [value.strip() for value in content.split(",") if value.strip()]
        return content

    result: dict[str, str | list[str] | list[tuple[str, bool]]] = {}
    # Real sections appear in the form's canonical order; a heading that is
    # out of order or repeats an already-seen section was forged inside a
    # free-text field, so its text stays with the field it was typed in.
    prev_index = -1
    for index, match in enumerate(matches):
        heading = match.group("heading")
        if heading == "Confirmations":
            continue  # handled after the loop: last occurrence always wins
        section_index = FORM_ORDER[heading]
        if section_index <= prev_index or heading in result:
            continue
        prev_index = section_index
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        result[heading] = parse_section(heading, body[match.end() : end].strip())

    # Confirmations is the form's final section, so the last match is the
    # real one even when earlier free text contains a forged heading.
    confirmations = [
        match for match in matches if match.group("heading") == "Confirmations"
    ]
    if confirmations:
        match = confirmations[-1]
        result["Confirmations"] = parse_section(
            "Confirmations", body[match.end() :].strip()
        )
    return result


def make_slug(name: str) -> str:
    slug = slugify(name, max_length=50)
    if not re.fullmatch(SLUG_RE, slug):
        raise ValueError(f"Cannot derive a valid slug from {name!r} (got {slug!r})")
    if slug in RESERVED_SLUGS:
        raise ValueError(f"Slug {slug!r} is reserved")
    return slug


def existing_developers(content_dir: Path) -> dict[str, str]:
    devs = {}
    for index in sorted(content_dir.glob("*/index.md")):
        data = read_frontmatter(index)
        title = data.get("title")
        if isinstance(title, str):
            devs[index.parent.name] = title
    return devs


def match_developer(name: str, devs: dict[str, str]) -> tuple[str, bool] | list[str]:
    by_title = {title.casefold(): slug for slug, title in devs.items()}
    hit = by_title.get(name.casefold())
    if hit:
        return hit, True
    close = difflib.get_close_matches(name.casefold(), list(by_title), n=3, cutoff=0.6)
    return sorted(by_title[c] for c in close)


def existing_site_origins(content_dir: Path) -> set[str]:
    origins = set()
    for index in sorted(content_dir.glob("*/*/index.md")):
        url = read_frontmatter(index).get("site_url")
        if isinstance(url, str) and url:
            try:
                parts = urlsplit(url)
                if parts.scheme and parts.hostname:
                    origins.add(f"{parts.scheme}://{parts.hostname.lower()}")
            except ValueError:
                continue
    return origins


def check_slug_free(kind: str, slug: str, content_dir: Path) -> None:
    if slug in RESERVED_SLUGS:
        raise ValueError(f"Slug {slug!r} is reserved")
    if kind == "developer" and (content_dir / slug).exists():
        raise ValueError(f"Developer directory {slug!r} already exists")
    if kind == "site" and any(p.is_dir() for p in content_dir.glob(f"*/{slug}")):
        raise ValueError(f"Site directory {slug!r} already exists for a developer")


CONFIRMATION_LABELS = (
    "I am affiliated with this site or have permission to submit it",
    "This is a production website built with Wagtail",
)

LAT_RE = re.compile(r"^-?(?:[0-8]?\d|90)(?:\.\d+)?$")
LON_RE = re.compile(r"^-?(?:\d{1,2}|1[0-7]\d|180)(?:\.\d+)?$")


class Rejection(Exception):
    def __init__(self, *reasons: str):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def build_proposal(
    body: str,
    issue_number: int,
    content_dir: Path,
    resolver=socket.getaddrinfo,
    now: datetime | None = None,
) -> Proposal:
    reasons: list[str] = []
    try:
        fields = parse_issue_form_body(body)
    except FormParseError:
        raise Rejection("This issue was not created with the site submission form.")

    def field(label: str) -> str:
        value = fields.get(label, "")
        return value.strip() if isinstance(value, str) else ""

    # Confirmations.
    confirmations = fields.get("Confirmations", [])
    for label in CONFIRMATION_LABELS:
        checked = any(label in item_label and ok for item_label, ok in confirmations)
        if not checked:
            reasons.append(f"Tick the confirmation: “{label}.”")

    # URL.
    site_url = ""
    raw_url = field("Site URL")
    if not raw_url:
        reasons.append("Fill in the site URL.")
    else:
        try:
            site_url = check_public_url(raw_url, resolver=resolver)
        except Exception as exc:
            reasons.append(f"The site URL was rejected: {_validation_message(exc)}")

    # Required text fields.
    site_title = field("Site title")
    if not site_title:
        reasons.append("Fill in the site title.")
    site_description = field("Short description")
    if not site_description:
        reasons.append("Fill in the short description.")
    developer_name = field("Developer name")
    if not developer_name:
        reasons.append("Fill in the developer name.")

    # Facets (already list-valued from the parser), validated against the
    # controlled vocabularies so typos can never reach the content files.
    def facet_values(heading: str, allowed: frozenset[str], label: str) -> list[str]:
        values = [
            v for v in (fields.get(heading) or []) if isinstance(v, str) and v.strip()
        ]
        for value in values:
            if value not in allowed:
                reasons.append(f"{value!r} is not a valid {label} option.")
        return values

    sector = facet_values("Sector", SECTOR_VALUES, "sector")
    site_type = facet_values("Site type", SITE_TYPE_VALUES, "site type")
    capability = facet_values("Capabilities", CAPABILITY_VALUES, "capability")

    # Developer profile, inferred from the name: an exact (case-insensitive)
    # match against an existing profile title adds the site to that profile,
    # and any other name starts a new one. Near-misses surface as a
    # reviewer-facing hint so duplicate profiles are caught at review time.
    developer_exists = False
    developer_slug = ""
    similar_developers: list[str] = []
    if developer_name:
        result = match_developer(developer_name, existing_developers(content_dir))
        if isinstance(result, list):
            similar_developers = result
            try:
                developer_slug = make_slug(developer_name)
                check_slug_free("developer", developer_slug, content_dir)
            except ValueError as exc:
                reasons.append(f"The developer name is not usable: {exc}")
        else:
            developer_exists = True
            developer_slug = result[0]

    # Site slug + dedup.
    site_slug = ""
    if site_title:
        try:
            site_slug = make_slug(site_title)
            check_slug_free("site", site_slug, content_dir)
        except ValueError as exc:
            reasons.append(f"The site title is not usable as a page name: {exc}")

    if site_url:
        origin = urlsplit(site_url)
        origin_key = (
            f"{origin.scheme}://{origin.hostname.lower()}" if origin.hostname else ""
        )
        if origin_key and origin_key in existing_site_origins(content_dir):
            reasons.append(f"{origin_key} is already in the showcase.")

    # Optional fields.
    developer_url = ""
    # Submitters often enter a bare domain ("madewithwagtail.org");
    # assume https rather than rejecting the URL outright.
    if raw := field("Developer URL"):
        if not raw.lower().startswith("http"):
            raw = f"https://{raw}"
        try:
            developer_url = check_public_url(raw, resolver=resolver)
        except Exception as exc:
            reasons.append(
                f"The developer URL was rejected: {_validation_message(exc)}"
            )

    logo_url = ""
    if field("Logo URL"):
        try:
            logo_url = check_public_url(field("Logo URL"), resolver=resolver)
        except Exception as exc:
            reasons.append(f"The logo URL was rejected: {_validation_message(exc)}")

    location = field("Developer location") or None
    lat = field("Latitude") or None
    lon = field("Longitude") or None
    if lat and not LAT_RE.fullmatch(lat):
        reasons.append("Latitude must be a decimal degrees value between -90 and 90.")
        lat = None
    if lon and not LON_RE.fullmatch(lon):
        reasons.append(
            "Longitude must be a decimal degrees value between -180 and 180."
        )
        lon = None

    github_user = field("GitHub username") or None
    other_notes = field("Other notes") or None

    if reasons:
        raise Rejection(*reasons)

    try:
        return Proposal(
            schema_version=1,
            issue_number=issue_number,
            site_url=site_url,
            site_title=site_title,
            site_description=site_description,
            sector=sector,
            site_type=site_type,
            capability=capability,
            developer_name=developer_name,
            developer_slug=developer_slug,
            site_slug=site_slug,
            developer_exists=developer_exists,
            similar_developers=similar_developers,
            developer_url=developer_url or None,
            developer_location=location,
            lat=lat,
            lon=lon,
            github_user=github_user,
            logo_url=logo_url or None,
            other_notes=other_notes,
            submitted_at=now or utcnow(),
        )
    except ValidationError as exc:
        # Model-level caps (title > 80, description > 800, ...) are reachable
        # through the real form — they must surface as a structured rejection,
        # not an exit-1 traceback that silently drops the submission.
        raise Rejection(
            *(_proposal_error_reason(error) for error in exc.errors())
        ) from exc


# Model-level constraints on user-editable fields, mapped to rejection copy.
PROPOSAL_ERROR_REASONS = {
    "site_title": "The site title must be at most 80 characters.",
    "site_description": "The short description must be at most 800 characters.",
    "developer_name": "The developer name must be at most 80 characters.",
    "developer_location": "The developer location must be at most 100 characters.",
}


def _proposal_error_reason(error: dict) -> str:
    loc = error.get("loc") or ()
    field = str(loc[-1]) if loc else ""
    return PROPOSAL_ERROR_REASONS.get(
        field,
        f"An entry in the form was rejected: {error.get('msg', 'invalid value')}.",
    )


def _validation_message(exc: Exception) -> str:
    """Human-readable first message from a pydantic ValidationError."""
    if hasattr(exc, "errors") and callable(exc.errors):
        errors = exc.errors()
        if errors:
            return str(errors[0].get("msg", exc))
    return str(exc)
