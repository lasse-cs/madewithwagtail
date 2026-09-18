import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline.proposal import Rejection, build_proposal
from test_proposal import make_proposal_kwargs  # noqa: F401  (fixture helper below)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "submissions" / "process_submission.py"
# The content-dir contract points at the developers directory itself.
CONTENT = Path(__file__).parent / "fixtures" / "content" / "developers"

FORM_BODY = """\
### Site URL

https://example.com

### Site title

Example Site

### Short description

A wonderful site about things.

### Sector

technology

### Site type

blog

### Capabilities

multilingual

### Developer name

Example Co

### Developer URL

https://example.co

### Developer location

Stockholm, Sweden

### Latitude

59.34

### Longitude

18.06

### GitHub username

exampleco

### Confirmations

- [X] I am affiliated with this site or have permission to submit it.
- [X] This is a production website built with Wagtail.
"""


def fake_resolver(host, port, *args, **kwargs):
    # FORM_BODY also contains example.co (Developer URL), and the duplicate-origin
    # test rewrites the site URL to www.visitsweden.com — resolve those too.
    table = {
        "example.com": "93.184.216.34",
        "example.co": "93.184.216.35",
        "www.visitsweden.com": "93.184.216.36",
    }
    if host in table:
        return [(socket.AF_INET, None, None, "", (table[host], port))]
    raise FakeResolutionError(host)


class FakeResolutionError(Exception):
    pass


class TestBuildProposal:
    def test_happy_path_new_developer(self):
        proposal = build_proposal(
            FORM_BODY, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
        )
        assert proposal.developer_exists is False
        assert proposal.developer_slug == "example-co"
        assert proposal.site_slug == "example-site"
        assert proposal.sector == ["technology"]
        assert proposal.site_type == ["blog"]
        assert proposal.capability == ["multilingual"]
        assert proposal.lat == "59.34"

    def test_other_notes_captured(self):
        body = FORM_BODY.replace(
            "### Confirmations",
            "### Other notes\n\nLaunched in 2024, redesign of an older site.\n\n### Confirmations",
        )
        proposal = build_proposal(
            body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
        )
        assert proposal.other_notes == "Launched in 2024, redesign of an older site."

    def test_other_notes_absent_is_none(self):
        proposal = build_proposal(
            FORM_BODY, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
        )
        assert proposal.other_notes is None

    def test_existing_developer_inferred_from_exact_name(self):
        body = FORM_BODY.replace("### Developer name\n\nExample Co", "### Developer name\n\nFröjd")
        proposal = build_proposal(
            body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
        )
        assert proposal.developer_exists is True
        assert proposal.developer_slug == "frojd"

    def test_existing_developer_inferred_case_insensitive(self):
        body = FORM_BODY.replace("### Developer name\n\nExample Co", "### Developer name\n\nfröjd")
        proposal = build_proposal(
            body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
        )
        assert proposal.developer_exists is True
        assert proposal.developer_slug == "frojd"

    def test_unmatched_name_starts_new_profile(self):
        proposal = build_proposal(
            FORM_BODY, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
        )
        assert proposal.developer_exists is False
        assert proposal.developer_slug == "example-co"
        assert proposal.similar_developers == []

    def test_near_match_starts_new_profile_with_hint(self):
        # Not an exact match, so a new profile — but reviewers are told
        # about the similar existing profile.
        body = FORM_BODY.replace("### Developer name\n\nExample Co", "### Developer name\n\nFröjd AB")
        proposal = build_proposal(
            body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
        )
        assert proposal.developer_exists is False
        assert proposal.developer_slug == "frojd-ab"
        assert proposal.similar_developers == ["frojd"]

    def test_rejects_name_whose_slug_already_exists(self):
        # "Frojd" (no diacritics) doesn't match the "Fröjd" title, but its
        # slug collides with the existing profile directory.
        body = FORM_BODY.replace("### Developer name\n\nExample Co", "### Developer name\n\nFrojd")
        with pytest.raises(Rejection) as excinfo:
            build_proposal(
                body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
            )
        assert any("already exists" in r for r in excinfo.value.reasons)

    def test_rejects_unconfirmed_permission(self):
        body = FORM_BODY.replace("- [X] I am affiliated", "- [ ] I am affiliated")
        with pytest.raises(Rejection) as excinfo:
            build_proposal(body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver)
        assert any("affiliated" in r for r in excinfo.value.reasons)

    def test_rejects_duplicate_origin(self):
        body = FORM_BODY.replace("https://example.com", "http://www.visitsweden.com")
        with pytest.raises(Rejection) as excinfo:
            build_proposal(body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver)
        assert any("already" in r for r in excinfo.value.reasons)

    def test_no_response_optional_fields_accepted(self):
        # GitHub writes "_No response_" for optional fields the submitter
        # left blank; they must be treated as unset, not as literal data.
        body = FORM_BODY
        for label, value in (
            ("Developer URL", "https://example.co"),
            ("Developer location", "Stockholm, Sweden"),
            ("Latitude", "59.34"),
            ("Longitude", "18.06"),
            ("GitHub username", "exampleco"),
        ):
            body = body.replace(f"### {label}\n\n{value}\n", f"### {label}\n\n_No response_\n")
        for label, value in (
            ("Sector", "technology"),
            ("Site type", "blog"),
            ("Capabilities", "multilingual"),
        ):
            body = body.replace(f"### {label}\n\n{value}\n", f"### {label}\n\n_No response_\n")
        proposal = build_proposal(
            body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
        )
        assert proposal.developer_url is None
        assert proposal.developer_location is None
        assert proposal.lat is None
        assert proposal.lon is None
        assert proposal.github_user is None
        assert proposal.sector == []
        assert proposal.site_type == []
        assert proposal.capability == []

    def test_developer_url_without_scheme_gets_https_prefix(self):
        body = FORM_BODY.replace(
            "### Developer URL\n\nhttps://example.co", "### Developer URL\n\nexample.co"
        )
        proposal = build_proposal(
            body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
        )
        assert proposal.developer_url == "https://example.co"

    def test_rejects_private_url(self):
        class Bad(Exception):
            pass

        def bad_resolver(host, port, *a, **k):
            raise Bad(host)

        with pytest.raises(Rejection):
            build_proposal(FORM_BODY, issue_number=7, content_dir=CONTENT, resolver=bad_resolver)


class TestValidateCLI:
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "validate", *args],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_cli_accepts(self, tmp_path):
        body_file = tmp_path / "body.txt"
        body_file.write_text(FORM_BODY)
        # Patch DNS via a wrapper is not possible through subprocess; instead
        # use a resolvable URL. example.com resolves publicly in CI.
        result = self.run_cli(
            "--issue-body", str(body_file),
            "--issue-number", "7",
            "--content-dir", str(CONTENT),
        )
        assert result.returncode in (0, 2)  # 2 only if DNS unavailable in sandbox
        if result.returncode == 0:
            data = json.loads(result.stdout)
            assert data["schema_version"] == 1


class TestModelLevelRejections:
    """Over-long fields must become structured rejections, not exit-1 crashes."""

    def test_over_long_title_rejected(self):
        body = FORM_BODY.replace("Example Site", "x" * 81)
        with pytest.raises(Rejection) as excinfo:
            build_proposal(
                body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
            )
        assert any("title" in reason and "80" in reason for reason in excinfo.value.reasons)

    def test_over_long_description_rejected(self):
        body = FORM_BODY.replace(
            "A wonderful site about things.", "y" * 801
        )
        with pytest.raises(Rejection) as excinfo:
            build_proposal(
                body, issue_number=7, content_dir=CONTENT, resolver=fake_resolver
            )
        assert any("800" in reason for reason in excinfo.value.reasons)
