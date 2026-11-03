"""Logs are an artifact we share: they must never carry credentials."""

import logging

from sge import logging_setup


def test_token_shaped_strings_are_masked():
    masked = logging_setup.redact(
        "cloning with ghp_abcdefghijklmnopqrstuvwxyz012345"
    )

    assert "ghp_" not in masked
    assert logging_setup.REDACTED in masked


def test_environment_secret_values_are_masked(monkeypatch):
    monkeypatch.setenv("SGE_TEST_TOKEN", "s3cret-value-not-in-logs")

    masked = logging_setup.redact("auth header had s3cret-value-not-in-logs")

    assert "s3cret-value-not-in-logs" not in masked


def test_bearer_headers_are_masked():
    masked = logging_setup.redact("Authorization: Bearer abcdef1234567890")

    assert "abcdef1234567890" not in masked


def test_key_value_pairs_are_masked():
    masked = logging_setup.redact('{"api_key": "abcdef123456"}')

    assert "abcdef123456" not in masked


def test_ordinary_text_is_untouched():
    text = "CISA Cybersecurity Advisories: 30 entries, 30 new"

    assert logging_setup.redact(text) == text


def test_records_pass_through_the_filter(monkeypatch, caplog):
    monkeypatch.setenv("SGE_TEST_SECRET", "leaky-value-123456")
    logger = logging.getLogger("sge.test.redaction")
    logger.addFilter(logging_setup.RedactingFilter())

    with caplog.at_level(logging.INFO, logger="sge.test.redaction"):
        logger.info("token=%s", "leaky-value-123456")

    assert "leaky-value-123456" not in caplog.text
    assert logging_setup.REDACTED in caplog.text
