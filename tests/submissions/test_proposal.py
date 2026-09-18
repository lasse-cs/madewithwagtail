from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from pipeline.proposal import Proposal


def make_proposal_kwargs(**overrides):
    kwargs = {
        "schema_version": 1,
        "issue_number": 42,
        "site_url": "https://example.com",
        "site_title": "Example Site",
        "site_description": "A site.",
        "sector": ["technology"],
        "site_type": ["product"],
        "capability": ["multilingual"],
        "developer_name": "Example Co",
        "developer_slug": "example-co",
        "site_slug": "example-site",
        "developer_exists": False,
        "submitted_at": datetime(2026, 8, 5, tzinfo=timezone.utc),
    }
    kwargs.update(overrides)
    return kwargs


class TestProposal:
    def test_roundtrip_via_json(self):
        proposal = Proposal(**make_proposal_kwargs())
        restored = Proposal.model_validate_json(proposal.model_dump_json())
        assert restored == proposal

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            Proposal(**make_proposal_kwargs(surprise="x"))

    def test_rejects_bad_slug(self):
        with pytest.raises(ValidationError):
            Proposal(**make_proposal_kwargs(site_slug="../evil"))

    def test_rejects_long_title(self):
        with pytest.raises(ValidationError):
            Proposal(**make_proposal_kwargs(site_title="x" * 81))

    def test_similar_developers_default_empty(self):
        proposal = Proposal(**make_proposal_kwargs())
        assert proposal.similar_developers == []

    def test_rejects_bad_github_user(self):
        with pytest.raises(ValidationError):
            Proposal(**make_proposal_kwargs(github_user="not valid!"))
