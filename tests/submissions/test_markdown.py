import re
from pathlib import Path

import yaml

from pipeline.content import developer_markdown, output_paths, site_markdown
from pipeline.proposal import Proposal
from test_proposal import make_proposal_kwargs

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def make_proposal(**overrides):
    return Proposal(**make_proposal_kwargs(**overrides))


def frontmatter_of(text: str) -> dict:
    """Extract the frontmatter block between the --- fences and parse it.

    Regex-based (not split("---")) so values containing '---' cannot
    mis-split the extraction.
    """
    match = FRONTMATTER_RE.match(text)
    assert match, f"No frontmatter block found in: {text[:120]!r}"
    return yaml.safe_load(match.group(1))


class TestSiteMarkdown:
    def test_frontmatter_parses_and_matches(self):
        p = make_proposal(site_description="A wonderful site about things.")
        text = site_markdown(p)
        assert text.startswith("---\n")
        assert frontmatter_of(text) == {
            "title": "Example Site",
            "first_published_at": "2026-08-05T00:00:00+00:00",
            "latest_revision_created_at": "2026-08-05T00:00:00+00:00",
            "site_url": "https://example.com",
            "sector": ["technology"],
            "site_type": ["product"],
            "capability": ["multilingual"],
        }
        assert text.rstrip().endswith("A wonderful site about things.")

    def test_unset_optionals_are_omitted_not_null(self):
        # Regression: committed frontmatter contained `in_cooperation_with_slug: null`;
        # optional fields left unset must be left out entirely (the Astro
        # schema defaults them).
        text = site_markdown(make_proposal())
        assert "in_cooperation_with_slug" not in text
        assert ": null" not in text

    def test_yaml_injection_resisted(self):
        # A title full of YAML/metacharacters must round-trip through
        # safe_dump intact, never break out of frontmatter.
        p = make_proposal(site_title='Weird: "title" #not comment\n---')
        text = site_markdown(p)
        assert frontmatter_of(text)["title"] == 'Weird: "title" #not comment\n---'

    def test_all_fields_new_developer(self):
        p = make_proposal()
        text = site_markdown(p)
        assert "- technology" in text  # facets as YAML lists

    def test_technologies_written_when_detected(self):
        text = site_markdown(
            make_proposal(),
            {"incompatible": ["PHP"], "complementary": ["React"], "other": ["jQuery"]},
        )
        assert frontmatter_of(text)["technologies"] == ["React"]

    def test_technologies_omitted_when_none(self):
        text = site_markdown(make_proposal())
        assert "technologies" not in text

    def test_technologies_omitted_when_empty_list(self):
        text = site_markdown(make_proposal(), {"complementary": []})
        assert "technologies" not in text


class TestDeveloperMarkdown:
    def test_frontmatter(self):
        p = make_proposal(developer_location="Stockholm, Sweden", github_user="exampleco")
        text = developer_markdown(p)
        frontmatter = frontmatter_of(text)
        assert frontmatter["title"] == "Example Co"
        assert frontmatter["location"] == "Stockholm, Sweden"
        assert "twitter_handler" not in frontmatter
        assert frontmatter["github_user"] == "exampleco"
        assert frontmatter["online_profiles"] == []


class TestOutputPaths:
    def test_paths(self):
        p = make_proposal()
        paths = output_paths(p)
        assert paths["site_md"] == Path("src/content/developers/example-co/example-site/index.md")
        assert paths["developer_md"] == Path("src/content/developers/example-co/index.md")
        assert paths["screenshot"] == Path(
            "src/content/developers/example-co/example-site/example-site.fill-1200x996.webp"
        )
        assert paths["logo"] == Path("src/content/developers/example-co/example-co.max-120x120.webp")

    def test_existing_developer_has_no_developer_paths(self):
        p = make_proposal(developer_exists=True, developer_slug="frojd")
        paths = output_paths(p)
        assert "developer_md" not in paths
        assert "logo" not in paths


class TestWriteFrontmatterFile:
    def test_rewrites_frontmatter_and_preserves_body(self, tmp_path):
        from pipeline.content import read_frontmatter, write_frontmatter_file

        index = tmp_path / "index.md"
        index.write_text(
            "---\ntitle: Old\nsite_url: https://old.example/\n---\n\nBody text here.\n",
            encoding="utf-8",
        )
        write_frontmatter_file(index, {"title": "New", "site_url": "https://new.example/"})
        # The block is followed by a blank line and the body, as in every
        # committed content file (frontmatter_block ends with "---\n").
        assert index.read_text(encoding="utf-8").endswith("Body text here.\n")
        assert read_frontmatter(index) == {
            "title": "New",
            "site_url": "https://new.example/",
        }

    def test_writes_bodyless_file(self, tmp_path):
        from pipeline.content import write_frontmatter_file

        index = tmp_path / "index.md"
        write_frontmatter_file(index, {"title": "Only frontmatter"})
        assert index.read_text(encoding="utf-8") == "---\ntitle: Only frontmatter\n---\n"
