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
