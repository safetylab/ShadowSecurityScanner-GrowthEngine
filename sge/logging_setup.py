"""Logging with secret redaction.

Every agent logs to stderr and to logs/sge.log. Logs are an artifact we
may paste into issues or CI output, so values that look like credentials
are replaced before a record is ever formatted.
"""

import logging
import os
import re

from . import paths

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# Environment variables whose *values* must never reach a log line.
SECRET_ENV_MARKERS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "PASSWD", "COOKIE")

# Credential shapes that must be masked even if they never passed through
# an environment variable we know about.
SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{10,}"),
    re.compile(r"(?i)(api[-_]?key|token|secret|password)(\"?\s*[:=]\s*\"?)"
               r"[^\s\"',]{6,}"),
)

REDACTED = "***redacted***"


def secret_values():
    """Values of credential-looking environment variables."""
    values = set()

    for name, value in os.environ.items():
        if not value or len(value) < 6:
            continue
        if any(marker in name.upper() for marker in SECRET_ENV_MARKERS):
            values.add(value)

    return values


def redact(text):
    """Mask known secret values and credential-shaped substrings."""
    if not isinstance(text, str):
        text = str(text)

    for value in secret_values():
        text = text.replace(value, REDACTED)

    for pattern in SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}",
                               text)
        else:
            text = pattern.sub(REDACTED, text)

    return text


class RedactingFilter(logging.Filter):
    """Redact the formatted message and arguments of every record."""

    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def get_logger(name, level=None, log_file=True):
    """Return a configured logger; safe to call repeatedly."""
    logger = logging.getLogger(name)

    if getattr(logger, "_sge_configured", False):
        return logger

    logger.setLevel(level or os.environ.get("SGE_LOG_LEVEL", "INFO").upper())
    logger.addFilter(RedactingFilter())

    formatter = logging.Formatter(LOG_FORMAT)

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    if log_file:
        try:
            paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(
                paths.LOG_DIR / "sge.log", encoding="utf-8"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        except OSError:
            # A read-only checkout must not stop an agent from running.
            logger.debug("File logging unavailable")

    logger._sge_configured = True
    return logger
