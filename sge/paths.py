"""Filesystem layout of the Growth Engine repository."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CONFIG_DIR = ROOT / "config"
DATABASE_DIR = ROOT / "database"
MIGRATIONS_DIR = DATABASE_DIR / "migrations"
LOG_DIR = ROOT / "logs"

CONTENT_DIR = ROOT / "content"
DRAFTS_DIR = CONTENT_DIR / "drafts"
PUBLISHED_DIR = CONTENT_DIR / "published"
TEMPLATES_DIR = CONTENT_DIR / "templates"

REPORTS_DIR = ROOT / "analytics" / "reports"

DEFAULT_DB_PATH = DATABASE_DIR / "growth.db"


def db_path():
    """Database location, overridable with SGE_DB_PATH (used by tests)."""
    override = os.environ.get("SGE_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


class UnsafePath(ValueError):
    """A stored path pointed outside the repository."""


def inside_root(relative):
    """Resolve a stored relative path, refusing to leave the repository.

    Paths in the database are written by the agents themselves, so this
    is defence in depth rather than a known hole: a '../../etc/passwd'
    in content_items.path would otherwise be read and published.
    """
    candidate = (ROOT / str(relative)).resolve()
    root = ROOT.resolve()

    if candidate != root and root not in candidate.parents:
        raise UnsafePath(f"Path escapes the repository: {relative!r}")

    return candidate
