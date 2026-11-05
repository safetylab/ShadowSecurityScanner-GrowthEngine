"""Configuration validation, including the policy rules it enforces."""

import pytest

from sge import config


def test_repository_configuration_is_valid():
    assert config.validate_all() == {name: None for name in config.LOADERS}


def test_every_configuration_file_has_a_loader():
    on_disk = {
        path.name
        for path in config.paths.CONFIG_DIR.iterdir()
        if path.suffix in (".yml", ".yaml", ".json")
    }

    assert on_disk == set(config.LOADERS)


def test_automatic_is_refused_where_the_platform_has_no_publisher(
    monkeypatch, tmp_path
):
    """Hacker News cannot be automated, so the config may not claim it."""
    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "channels.yml").write_text(
        "channels:\n"
        "  hackernews:\n"
        "    enabled: true\n"
        "    mode: automatic\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config.paths, "CONFIG_DIR", broken)

    with pytest.raises(config.ConfigError, match="read-only"):
        config.load_channels()


def test_automatic_needs_a_target_where_the_platform_has_places(
    monkeypatch, tmp_path
):
    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "channels.yml").write_text(
        "channels:\n"
        "  reddit:\n"
        "    enabled: true\n"
        "    mode: automatic\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config.paths, "CONFIG_DIR", broken)

    with pytest.raises(config.ConfigError, match="needs 'targets'"):
        config.load_channels()


def test_automatic_with_a_publisher_and_a_target_loads(monkeypatch, tmp_path):
    ok = tmp_path / "config"
    ok.mkdir()
    (ok / "channels.yml").write_text(
        "channels:\n"
        "  reddit:\n"
        "    enabled: true\n"
        "    mode: automatic\n"
        "    targets: ['netsec']\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config.paths, "CONFIG_DIR", ok)

    assert config.channel_mode("reddit", config.load_channels()) == "automatic"


def test_unknown_publish_mode_is_rejected(monkeypatch, tmp_path):
    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "channels.yml").write_text(
        "channels:\n  website:\n    enabled: true\n    mode: yolo\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config.paths, "CONFIG_DIR", broken)

    with pytest.raises(config.ConfigError, match="mode must be one of"):
        config.load_channels()


def test_paid_apis_cannot_be_enabled(monkeypatch, tmp_path):
    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "brand.yml").write_text(
        "project:\n"
        "  name: X\n  website: https://x.test\n"
        "  github: https://x.test\n  growth_repository: https://x.test\n"
        "budget:\n  paid_apis: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config.paths, "CONFIG_DIR", broken)

    with pytest.raises(config.ConfigError, match="paid_apis"):
        config.load_brand()


def test_sources_must_use_https(monkeypatch, tmp_path):
    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "research_sources.json").write_text(
        '{"sources": [{"name": "x", "url": "http://x.test/f.xml",'
        ' "enabled": true}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(config.paths, "CONFIG_DIR", broken)

    with pytest.raises(config.ConfigError, match="https"):
        config.load_sources()


def test_duplicate_source_names_are_rejected(monkeypatch, tmp_path):
    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "research_sources.json").write_text(
        '{"sources": ['
        '{"name": "x", "url": "https://a.test/f.xml", "enabled": true},'
        '{"name": "x", "url": "https://b.test/f.xml", "enabled": true}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(config.paths, "CONFIG_DIR", broken)

    with pytest.raises(config.ConfigError, match="duplicate"):
        config.load_sources()


def test_malformed_json_is_reported_with_the_file_name(monkeypatch, tmp_path):
    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "research_sources.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(config.paths, "CONFIG_DIR", broken)

    with pytest.raises(config.ConfigError, match="research_sources.json"):
        config.load_sources()


def test_empty_file_is_reported(monkeypatch, tmp_path):
    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "keywords.yml").write_text("", encoding="utf-8")
    monkeypatch.setattr(config.paths, "CONFIG_DIR", broken)

    with pytest.raises(config.ConfigError, match="empty"):
        config.load_keywords()


def test_missing_file_is_reported(monkeypatch, tmp_path):
    empty = tmp_path / "config"
    empty.mkdir()
    monkeypatch.setattr(config.paths, "CONFIG_DIR", empty)

    with pytest.raises(config.ConfigError, match="Missing configuration"):
        config.load_policies()


def test_policy_flags_cannot_be_turned_off(monkeypatch, tmp_path):
    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "policies.yml").write_text(
        "security:\n"
        "  no_unauthorized_scanning: false\n"
        "  secrets_in_environment_only: true\n"
        "  never_commit_tokens: true\n"
        "publishing:\n"
        "  no_spam: true\n  no_fake_stars: true\n"
        "  require_fact_checking: true\n  respect_platform_terms: true\n"
        "automation:\n  maintain_audit_log: true\n  max_posts_per_day: 2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config.paths, "CONFIG_DIR", broken)

    with pytest.raises(config.ConfigError,
                       match="no_unauthorized_scanning"):
        config.load_policies()


def test_channel_mode_returns_none_for_disabled_channels():
    channels = {"channels": {"telegram": {"enabled": False,
                                          "mode": "manual"}}}

    assert config.channel_mode("telegram", channels) is None
    assert config.channel_mode("absent", channels) is None


def test_channel_mode_reads_the_repository_configuration():
    assert config.channel_mode("hackernews") == "draft_only"
    assert config.channel_mode("website") == "automatic"


def test_community_config_must_stay_draft_only(monkeypatch, tmp_path):
    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "community.yml").write_text(
        "queries:\n  hackernews: ['x']\n"
        "rules:\n  mode: automatic\n  max_age_days: 120\n"
        "  min_relevance: 25\n  min_topic_relevance: 25\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config.paths, "CONFIG_DIR", broken)

    with pytest.raises(config.ConfigError, match="draft_only"):
        config.load_community()


def test_community_config_needs_at_least_one_query(monkeypatch, tmp_path):
    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "community.yml").write_text(
        "queries:\n  hackernews: []\n  github: []\n"
        "rules:\n  mode: draft_only\n  max_age_days: 120\n"
        "  min_relevance: 25\n  min_topic_relevance: 25\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config.paths, "CONFIG_DIR", broken)

    with pytest.raises(config.ConfigError, match="no queries"):
        config.load_community()


@pytest.mark.parametrize("extra, message", [
    ('"relevance_floor": "high"', "must be an integer"),
    ('"relevance_floor": 500', "must be 0..100"),
    ('"default_topic": ""', "non-empty string"),
    ('"environments": "local"', "non-empty list"),
    ('"environments": []', "non-empty list"),
    ('"environments": [1]', "non-empty strings"),
])
def test_source_options_are_validated(monkeypatch, tmp_path, extra, message):
    """A typo here would otherwise crash mid-collection."""
    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "research_sources.json").write_text(
        '{"sources": [{"name": "x", "url": "https://x.test/f.xml",'
        ' "enabled": true, ' + extra + '}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(config.paths, "CONFIG_DIR", broken)

    with pytest.raises(config.ConfigError, match=message):
        config.load_sources()


def test_valid_source_options_are_accepted(monkeypatch, tmp_path):
    good = tmp_path / "config"
    good.mkdir()
    (good / "research_sources.json").write_text(
        '{"sources": [{"name": "x", "url": "https://x.test/f.xml",'
        ' "enabled": true, "relevance_floor": 20,'
        ' "default_topic": "tooling", "environments": ["local"]}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(config.paths, "CONFIG_DIR", good)

    assert config.load_sources()["sources"][0]["relevance_floor"] == 20
