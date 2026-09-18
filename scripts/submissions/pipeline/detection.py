"""Wagtail HTML fingerprints and Wappalyzer technology detection."""

from __future__ import annotations

import re
import sys

from .proposal import utcnow


RESPONSIVE_EMBED_RE = re.compile(
    r'<div[^>]*\bclass=["\'][^"\']*\bresponsive-object\b', re.IGNORECASE
)
STREAMFIELD_BLOCK_RE = re.compile(
    r'<div[^>]*\bclass=["\'][^"\']*\bw-block-', re.IGNORECASE
)
RICH_TEXT_RE = re.compile(r'\bdata-block-key=["\'][a-z0-9]{5}["\']')

# Rendition URL tiers, most strict first; detect_wagtail reports only the
# most confident matching tier. Adapted from JS regexes proven against
# real-world Wagtail sites. Character classes use [\w.-] (dash not last):
# Python re rejects a trailing dash inside a range.
WAGTAIL_RENDITION_TIERS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        (
            "strictest",
            r"\/media\/(?:original_images\/[\w-]+\.|images\/[\w.-]+\.((?:fill|max|min)-\d+x\d+(?:-c\d+)?|(?:width|height|scale)-\d+|original)\.)",
        ),
        (
            "strict",
            r"(?:\.[a-z]+|\/media)(?:\/[\w-]+)?\/(?:original_images\/[\w-]+\.|images\/[\w.-]+\.((?:fill|max|min|width|height|scale)-\d|original))",
        ),
        (
            "less_strict_but_long",
            r"(?:\.[a-z]+|\/media)(?:\/[\w-]+)?\/(?:images\/[\w.-]+\.original|original_images\/[\w-]+\.)|\/images\/[\w.-]+\.(?:fill|max|min|width|height|scale)-\d",
        ),
        (
            "lax",
            r"\/(?:original_images\/[\w-]+\.|images\/[\w.-]+\.((?:fill|max|min|width|height|scale)-\d|original))",
        ),
        (
            "laxest",
            r"\/original_images\/|\/[\w.-]+\.((?:fill|max|min|width|height|scale)-\d|original)",
        ),
    )
)


def rendition_tier(html: str) -> str | None:
    """Most strict rendition tier matching anywhere in the page, if any."""
    for name, pattern in WAGTAIL_RENDITION_TIERS:
        if pattern.search(html):
            return name
    return None


def detect_wagtail(html: str) -> list[str]:
    """Best-effort Wagtail fingerprints from page HTML (spec: evidence, never a gate)."""
    signals = []
    tier = rendition_tier(html)
    if tier is not None:
        signals.append(f"Wagtail rendition URL in image sources ({tier} tier)")
    if RICH_TEXT_RE.search(html):
        signals.append("Rich text data-block-key attribute")
    if RESPONSIVE_EMBED_RE.search(html):
        signals.append("Responsive embed container (responsive-object)")
    if STREAMFIELD_BLOCK_RE.search(html):
        signals.append("StreamField block classes (w-block-*)")
    return signals


# Wappalyzer-next (https://github.com/s0md3v/wappalyzer-next) fingerprints
# the page in headless Chromium, complementing the Wagtail HTML heuristics
# above (which stay authoritative for Wagtail itself).

# Sites built on these are not Wagtail sites; the Wappalyzer report gates
# the submission (spec: the showcase only lists Wagtail sites). PHP-hosted
# frameworks mostly imply PHP, so PHP itself covers most of them.
INCOMPATIBLE_TECHNOLOGIES = frozenset(
    {
        "PHP",
        "Microsoft ASP.NET",
        "Java",
        "Wix",
        "Webflow",
        "Squarespace",
    }
)

# Front-end stacks worth surfacing on the site page: Wagtail sites routinely
# pair with one of these, and the showcase's tags/series benefit from knowing.
COMPLEMENTARY_TECHNOLOGIES = frozenset(
    {
        "React",
        "Vue.js",
        "Next.js",
        "Nuxt.js",
        "Astro",
        "Svelte",
        "Alpine.js",
        "Htmx",
        "Bootstrap",
        "Tailwind CSS",
    }
)

# Technologies never reported in the "Detected technologies" section:
# Wagtail detection stays with our own HTML heuristics above, and
# Django/Python are implied for every Wagtail site.
UNREPORTED_TECHNOLOGIES = frozenset({"wagtail", "django", "python"})

# Wappalyzer categories whose detections are relevant at all; anything else
# (analytics, CDNs, widgets, ...) is noise for a technology report. The
# library reports category *names* in each technology's "categories" list.
WAPPALYZER_CATEGORIES = frozenset(
    {
        "CMS",
        "Blogs",
        "JavaScript frameworks",
        "Web frameworks",
        "Programming languages",
        "Page builders",
        "Static site generator",
        "UI frameworks",
        "JavaScript libraries",
    }
)

WAPPALYZER_SCAN_TIMEOUT_S = 45


def wappalyzer_technologies(url: str) -> dict[str, dict]:
    """Detect technologies with the Wappalyzer extension in Chromium.

    Returns {name: {"version": str, "categories": [str]}} — filtered to the
    categories we report on — or {} when the scan fails for any reason: a
    failed technology scan must never fail the render stage (the Wagtail
    detection above remains the gate for showcase-worthiness).
    """
    try:
        from wappalyzer import Wappalyzer as _Wappalyzer

        with _Wappalyzer(timeout=WAPPALYZER_SCAN_TIMEOUT_S) as scanner:
            results = scanner.analyze(url)
        # analyze() keys the result by the scanner's own final URL, which
        # may differ from the input (redirect, trailing slash) — take the
        # single value regardless of key.
        return next(iter(results.values()), {})
    except Exception as exc:
        print(f"wappalyzer scan failed for {url}: {exc}", file=sys.stderr)
        return {}


def classify_technologies(technologies: dict[str, dict]) -> dict[str, list[str]]:
    """Split detected technologies into incompatible / complementary / other.

    Only technologies in a reportable Wappalyzer category are considered;
    classification is by exact fingerprint name (Wappalyzer DB names, e.g.
    "Vue.js" not "Vue").
    """
    reportable = {
        name: data
        for name, data in technologies.items()
        if any(cat in WAPPALYZER_CATEGORIES for cat in data.get("categories", []))
    }
    return {
        "incompatible": sorted(INCOMPATIBLE_TECHNOLOGIES & reportable.keys()),
        "complementary": sorted(COMPLEMENTARY_TECHNOLOGIES & reportable.keys()),
        "other": sorted(
            name
            for name in reportable
            if name not in INCOMPATIBLE_TECHNOLOGIES
            and name not in COMPLEMENTARY_TECHNOLOGIES
            and name.casefold() not in UNREPORTED_TECHNOLOGIES
        ),
    }


def detection_result(
    signals: list[str], url: str, technologies: dict[str, list[str]] | None = None
) -> dict:
    return {
        "url": url,
        "is_wagtail": bool(signals),
        "signals": signals,
        "technologies": technologies or {},
        "checked_at": utcnow().isoformat(),
    }
