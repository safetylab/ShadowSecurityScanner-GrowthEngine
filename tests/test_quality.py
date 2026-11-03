"""The quality gate must block anything unattributed or overclaiming."""

import pytest

from sge import quality

PROJECT_URL = "https://github.com/safetylab/ShadowSecurityScanner"
SOURCE_URL = "https://example.test/advisory"


def good_text(extra=""):
    body = " ".join(["Operators verify advisories against their own hosts."]
                    * 25)
    return (
        f"# Digest\n\n{body}\n\n"
        f"- [Advisory]({SOURCE_URL})\n\n"
        f"Run the scanner only where authorised: {PROJECT_URL}\n{extra}"
    )


def test_a_complete_draft_passes():
    report = quality.check(good_text(), sources=[SOURCE_URL],
                           cta_url=PROJECT_URL)

    assert report["passed"] is True
    assert report["problems"] == []
    assert report["stats"]["sources"] == 1


def test_draft_without_sources_fails():
    report = quality.check(good_text(), sources=[], cta_url=PROJECT_URL)

    assert report["passed"] is False
    assert any("not enough sources" in p for p in report["problems"])


def test_source_must_actually_appear_in_the_text():
    report = quality.check(good_text(), sources=[SOURCE_URL,
                                                 "https://other.test/x"],
                           cta_url=PROJECT_URL)

    assert report["passed"] is False
    assert any("not linked" in p for p in report["problems"])


def test_invalid_source_url_fails():
    report = quality.check(good_text(), sources=["javascript:alert(1)"],
                           cta_url=PROJECT_URL)

    assert report["passed"] is False
    assert any("invalid source URL" in p for p in report["problems"])


def test_missing_project_cta_fails():
    text = good_text().replace(PROJECT_URL, "")
    report = quality.check(text, sources=[SOURCE_URL], cta_url=PROJECT_URL)

    assert report["passed"] is False
    assert any("call to action" in p for p in report["problems"])


def test_text_without_any_link_fails():
    report = quality.check("word " * 200, sources=[], cta_url=None)

    assert report["passed"] is False
    assert any("no links at all" in p for p in report["problems"])


def test_too_short_fails():
    short = f"Short note with a source {SOURCE_URL} and {PROJECT_URL}"
    report = quality.check(short, sources=[SOURCE_URL], cta_url=PROJECT_URL)

    assert report["passed"] is False
    assert any("too short" in p for p in report["problems"])


@pytest.mark.parametrize("claim", [
    "This is the best scanner in the world.",
    "We provide 100% detection of every threat.",
    "Guaranteed security for your network.",
    "It can bypass any protection.",
])
def test_unverifiable_claims_fail(claim):
    report = quality.check(good_text(claim), sources=[SOURCE_URL],
                           cta_url=PROJECT_URL)

    assert report["passed"] is False
    assert any("unverifiable claim" in p for p in report["problems"])


@pytest.mark.parametrize("marker", ["TODO: write intro", "TBD", "FIXME later",
                                    "lorem ipsum dolor"])
def test_placeholders_fail(marker):
    report = quality.check(good_text(marker), sources=[SOURCE_URL],
                           cta_url=PROJECT_URL)

    assert report["passed"] is False
    assert any("placeholder" in p for p in report["problems"])


def test_enforce_raises_instead_of_returning_a_report():
    with pytest.raises(quality.QualityGateError):
        quality.enforce("too short", sources=[])


def test_enforce_returns_the_report_when_it_passes():
    report = quality.enforce(good_text(), sources=[SOURCE_URL],
                             cta_url=PROJECT_URL)

    assert report["passed"] is True
