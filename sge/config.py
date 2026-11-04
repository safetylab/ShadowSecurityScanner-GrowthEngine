"""Configuration loading and validation.

Configuration drives what the engine is allowed to publish, so a typo
must fail loudly at load time rather than silently disable a rule.
Validation is a small declarative checker instead of a schema library:
the rules are few and the dependency would not pay for itself.
"""

import json
from pathlib import Path

import yaml

from . import paths

PUBLISH_MODES = ("automatic", "draft_only", "manual")
EXTERNAL_CHANNELS = ("reddit", "hackernews", "linkedin", "x", "telegram")


class ConfigError(Exception):
    """Configuration is missing, malformed or violates a policy."""


def _load_file(name):
    path = paths.CONFIG_DIR / name

    if not path.is_file():
        raise ConfigError(f"Missing configuration file: {path}")

    text = path.read_text(encoding="utf-8")

    try:
        if path.suffix == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as error:
        raise ConfigError(f"Cannot parse {name}: {error}") from error

    if data is None:
        raise ConfigError(f"Configuration file is empty: {name}")

    if not isinstance(data, dict):
        raise ConfigError(f"Configuration root must be a mapping: {name}")

    return data


def _require(data, key, kinds, where):
    if key not in data:
        raise ConfigError(f"{where}: missing key '{key}'")

    if not isinstance(data[key], kinds):
        names = getattr(kinds, "__name__", None) or "/".join(
            kind.__name__ for kind in kinds
        )
        raise ConfigError(f"{where}: '{key}' must be {names}")

    return data[key]


def load_brand():
    data = _load_file("brand.yml")

    project = _require(data, "project", dict, "brand.yml")
    for key in ("name", "website", "github", "growth_repository"):
        _require(project, key, str, "brand.yml:project")

    budget = _require(data, "budget", dict, "brand.yml")
    if budget.get("paid_apis") is not False:
        raise ConfigError("brand.yml:budget: paid_apis must stay false")

    return data


def load_channels():
    data = _load_file("channels.yml")
    channels = _require(data, "channels", dict, "channels.yml")

    if not channels:
        raise ConfigError("channels.yml: no channels defined")

    for name, channel in channels.items():
        where = f"channels.yml:{name}"

        if not isinstance(channel, dict):
            raise ConfigError(f"{where}: must be a mapping")

        _require(channel, "enabled", bool, where)
        mode = _require(channel, "mode", str, where)

        if mode not in PUBLISH_MODES:
            raise ConfigError(
                f"{where}: mode must be one of {', '.join(PUBLISH_MODES)}"
            )

        # The plan's core rule: no automatic posting to other people's
        # communities, whatever the rest of the configuration says.
        if name in EXTERNAL_CHANNELS and mode == "automatic":
            raise ConfigError(
                f"{where}: external channels must not be 'automatic'"
            )

    return data


def load_policies():
    data = _load_file("policies.yml")

    security = _require(data, "security", dict, "policies.yml")
    publishing = _require(data, "publishing", dict, "policies.yml")
    automation = _require(data, "automation", dict, "policies.yml")

    required_true = (
        (security, "no_unauthorized_scanning"),
        (security, "secrets_in_environment_only"),
        (security, "never_commit_tokens"),
        (publishing, "no_spam"),
        (publishing, "no_fake_stars"),
        (publishing, "require_fact_checking"),
        (publishing, "respect_platform_terms"),
        (automation, "maintain_audit_log"),
    )

    for section, key in required_true:
        if section.get(key) is not True:
            raise ConfigError(f"policies.yml: '{key}' must be true")

    limit = automation.get("max_posts_per_day")
    if not isinstance(limit, int) or not 0 < limit <= 10:
        raise ConfigError(
            "policies.yml:automation: max_posts_per_day must be 1..10"
        )

    return data


def load_keywords():
    data = _load_file("keywords.yml")

    for key in ("primary", "secondary", "negative"):
        values = _require(data, key, list, "keywords.yml")

        if not all(isinstance(value, str) and value for value in values):
            raise ConfigError(f"keywords.yml:{key}: must be non-empty strings")

    if not data["primary"]:
        raise ConfigError("keywords.yml: 'primary' must not be empty")

    topics = _require(data, "topics", dict, "keywords.yml")

    for topic, values in topics.items():
        if not isinstance(values, list) or not values:
            raise ConfigError(
                f"keywords.yml:topics:{topic}: must be a non-empty list"
            )

    return data


def load_publishing_rules():
    data = _load_file("publishing-rules.yml")

    rules = _require(data, "quality_gate", dict, "publishing-rules.yml")
    where = "publishing-rules.yml:quality_gate"

    _require(rules, "min_sources", int, where)
    _require(rules, "min_words", int, where)
    _require(rules, "require_cta", bool, where)
    _require(rules, "banned_phrases", list, where)
    _require(rules, "placeholder_markers", list, where)

    if rules["min_sources"] < 1:
        raise ConfigError(f"{where}: min_sources must be at least 1")

    external = _require(data, "external_channels", dict,
                        "publishing-rules.yml")
    if external.get("require_human_review") is not True:
        raise ConfigError(
            "publishing-rules.yml: external channels require human review"
        )

    return data


def load_sources():
    data = _load_file("research_sources.json")
    sources = _require(data, "sources", list, "research_sources.json")

    if not sources:
        raise ConfigError("research_sources.json: no sources defined")

    seen = set()

    for index, source in enumerate(sources):
        where = f"research_sources.json:sources[{index}]"

        if not isinstance(source, dict):
            raise ConfigError(f"{where}: must be a mapping")

        name = _require(source, "name", str, where)
        url = _require(source, "url", str, where)
        _require(source, "enabled", bool, where)

        if not url.startswith("https://"):
            raise ConfigError(f"{where}: url must use https")

        if name in seen:
            raise ConfigError(f"{where}: duplicate source name {name!r}")

        seen.add(name)

    return data


def load_community():
    data = _load_file("community.yml")

    queries = _require(data, "queries", dict, "community.yml")
    rules = _require(data, "rules", dict, "community.yml")
    where = "community.yml:rules"

    if not any(queries.values()):
        raise ConfigError("community.yml: no queries defined")

    for platform, entries in queries.items():
        if not isinstance(entries, list):
            raise ConfigError(
                f"community.yml:queries:{platform}: must be a list"
            )

    # The radar has no posting code; this keeps the configuration honest
    # about that rather than implying posting could be switched on.
    if rules.get("mode") != "draft_only":
        raise ConfigError(f"{where}: mode must stay 'draft_only'")

    for key in ("max_age_days", "min_relevance", "min_topic_relevance"):
        value = _require(rules, key, int, where)

        if value < 0:
            raise ConfigError(f"{where}: {key} must not be negative")

    return data


def load_competitors():
    data = _load_file("competitors.yml")
    projects = _require(data, "projects", list, "competitors.yml")

    for index, project in enumerate(projects):
        where = f"competitors.yml:projects[{index}]"

        if not isinstance(project, dict):
            raise ConfigError(f"{where}: must be a mapping")

        _require(project, "name", str, where)
        _require(project, "url", str, where)

    return data


LOADERS = {
    "brand.yml": load_brand,
    "channels.yml": load_channels,
    "policies.yml": load_policies,
    "keywords.yml": load_keywords,
    "publishing-rules.yml": load_publishing_rules,
    "competitors.yml": load_competitors,
    "community.yml": load_community,
    "research_sources.json": load_sources,
}


def validate_all():
    """Validate every configuration file; return {name: error or None}."""
    results = {}

    for name, loader in LOADERS.items():
        try:
            loader()
            results[name] = None
        except ConfigError as error:
            results[name] = str(error)

    return results


def channel_mode(name, channels=None):
    """Publishing mode of a channel, or None when it is not enabled."""
    data = channels or load_channels()
    channel = data["channels"].get(name)

    if not channel or not channel.get("enabled"):
        return None

    return channel["mode"]
