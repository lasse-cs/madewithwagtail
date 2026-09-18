"""Content-file frontmatter and Markdown writers."""

from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from .images import assert_webp, encode_logo, encode_screenshot

if TYPE_CHECKING:
    from .proposal import Proposal


def read_frontmatter(path: Path) -> dict:
    """Minimal frontmatter reader: YAML between the first two --- lines."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    _, fm, _ = text.split("---", 2)
    import yaml

    data = yaml.safe_load(fm)
    return data if isinstance(data, dict) else {}


def frontmatter_block(data: dict) -> str:
    import yaml

    # Optional fields left unset are omitted rather than written as null:
    # the Astro schemas default them, and `key: null` in committed content
    # is noise reviewers shouldn't see.
    data = {key: value for key, value in data.items() if value is not None}
    return "---\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True) + "---\n"


def iso(dt: datetime) -> str:
    return dt.isoformat()


def site_markdown(p: Proposal, technologies: dict[str, list[str]] | None = None) -> str:
    complementary = (technologies or {}).get("complementary", [])
    frontmatter = {
        "title": p.site_title,
        "first_published_at": iso(p.submitted_at),
        "latest_revision_created_at": iso(p.submitted_at),
        "site_url": p.site_url,
        "in_cooperation_with_slug": None,
        # Facet blocks are omitted entirely when empty: the Astro schemas
        # default them to [], matching how migrated entries are written.
        **({"sector": p.sector} if p.sector else {}),
        **({"site_type": p.site_type} if p.site_type else {}),
        **({"capability": p.capability} if p.capability else {}),
        # Complementary technologies from the Wappalyzer scan, e.g.
        # ["React", "Tailwind CSS"]. Omitted when nothing was detected:
        # the Astro schema defaults to [].
        **({"technologies": complementary} if complementary else {}),
    }
    return frontmatter_block(frontmatter) + f"\n{p.site_description}\n"


def developer_markdown(p: Proposal) -> str:
    frontmatter = {
        "title": p.developer_name,
        "first_published_at": iso(p.submitted_at),
        "latest_revision_created_at": iso(p.submitted_at),
        "location": p.developer_location,
        "lat": p.lat,
        "lon": p.lon,
        "company_url": p.developer_url,
        "twitter_handler": None,
        "github_user": p.github_user,
        "online_profiles": [],
    }
    return frontmatter_block(frontmatter)


# Proposal fields merged into an existing developer profile when provided,
# mapped to the developer frontmatter keys they update.
PROFILE_UPDATE_FIELDS = {
    "developer_url": "company_url",
    "developer_location": "location",
    "lat": "lat",
    "lon": "lon",
    "github_user": "github_user",
}


def profile_updates(p: Proposal) -> dict[str, str]:
    """Provided developer details for an existing profile, as
    {frontmatter key: value}.

    Empty when the submission provides none of these, so a Developer name
    alone still adds a site without touching the profile.
    """
    return {
        key: value
        for field, key in PROFILE_UPDATE_FIELDS.items()
        if (value := getattr(p, field)) is not None
    }


# Frontmatter occupies the file's first "---\n...\n---\n" block; the profile
# text follows it. Values containing '---' cannot mis-split the extraction.
FRONTMATTER_SPLIT_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def write_frontmatter_file(path: Path, data: dict) -> None:
    """Rewrite only the frontmatter block of a content file, preserving the
    body. A missing file is created with no body."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    match = FRONTMATTER_SPLIT_RE.match(text)
    body = text[match.end() :].strip() if match else text.strip()
    path.write_text(
        frontmatter_block(data) + (f"\n{body}\n" if body else ""), encoding="utf-8"
    )


def update_developer_profile(path: Path, p: Proposal) -> bool:
    """Merge the provided developer details into an existing profile.

    Only the fields the submitter provided are written; every other
    frontmatter value and the profile text are left untouched. The revision
    timestamp is bumped so listing pages reflect the update. Returns False —
    leaving the file alone — when there is nothing to change.
    """
    updates = profile_updates(p)
    if not updates or not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_SPLIT_RE.match(text)
    if not match:
        return False
    import yaml

    data = yaml.safe_load(match.group(1))
    if not isinstance(data, dict):
        return False
    changed = {key: value for key, value in updates.items() if data.get(key) != value}
    if not changed:
        return False
    data.update(changed)
    data["latest_revision_created_at"] = iso(p.submitted_at)
    write_frontmatter_file(path, data)
    return True


def output_paths(p: Proposal) -> dict[str, Path]:
    paths = {
        "site_md": Path(
            f"src/content/developers/{p.developer_slug}/{p.site_slug}/index.md"
        ),
        "screenshot": Path(
            f"src/content/developers/{p.developer_slug}/{p.site_slug}/{p.site_slug}.fill-1200x996.webp"
        ),
    }
    if not p.developer_exists:
        paths["developer_md"] = Path(
            f"src/content/developers/{p.developer_slug}/index.md"
        )
        paths["logo"] = Path(
            f"src/content/developers/{p.developer_slug}/{p.developer_slug}.max-120x120.webp"
        )
    elif profile_updates(p):
        # Existing profile with provided details: the profile is updated in
        # place as part of the submission.
        paths["developer_md"] = Path(
            f"src/content/developers/{p.developer_slug}/index.md"
        )
    return paths


def write_content_files(
    p: Proposal,
    repo_root: Path,
    screenshot: bytes,
    logo: bytes | None,
    technologies: dict[str, list[str]] | None = None,
) -> list[Path]:
    """Write validated content into the repo. Images are re-encoded through
    Pillow and dimension-checked — artifact bytes are never trusted as-is."""
    paths = output_paths(p)

    screenshot_img = assert_webp(screenshot)
    if screenshot_img.size != (1200, 996):
        raise ValueError(f"Screenshot must be 1200x996, got {screenshot_img.size}")
    reencoded_screenshot = encode_screenshot(screenshot)

    logo_out: bytes | None = None
    if not p.developer_exists:
        if logo is None:
            raise ValueError("New developer submission requires a logo (may be empty)")
        if logo:
            assert_webp(logo)  # format check; encode_logo normalizes the size
            logo_out = encode_logo(logo)
            if max(Image.open(io.BytesIO(logo_out)).size) > 120:
                raise ValueError("Encoded logo exceeds the 120x120 limit")

    written: list[Path] = []
    for key in ("site_md", "developer_md"):
        if key not in paths:
            continue
        target = repo_root / paths[key]
        if key == "developer_md" and p.developer_exists:
            # Existing profile: merge the provided details into it instead
            # of overwriting; a no-op update leaves the file alone.
            if update_developer_profile(target, p):
                written.append(target)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            site_markdown(p, technologies)
            if key == "site_md"
            else developer_markdown(p),
            encoding="utf-8",
        )
        written.append(target)

    screenshot_target = repo_root / paths["screenshot"]
    screenshot_target.parent.mkdir(parents=True, exist_ok=True)
    screenshot_target.write_bytes(reencoded_screenshot)
    written.append(screenshot_target)

    if logo_out is not None:
        logo_target = repo_root / paths["logo"]
        logo_target.parent.mkdir(parents=True, exist_ok=True)
        logo_target.write_bytes(logo_out)
        written.append(logo_target)

    return written
