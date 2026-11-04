"""Community radar judgement: relevance, intent and the reply floor."""

import datetime as dt

import pytest

from sge import community

NOW = dt.datetime(2026, 11, 4, tzinfo=dt.timezone.utc)

SETTINGS = {
    "queries": {"hackernews": ['"scanner"']},
    "intent_signals": {
        "asking": ["recommend", "alternative", "which tool"],
        "discussing": ["comparison", "review"],
    },
    "avoid": ["hiring", "job"],
    "rules": {"max_age_days": 120, "min_relevance": 25,
              "min_topic_relevance": 25, "mode": "draft_only"},
}


def thread(title, excerpt="", days_old=1):
    when = NOW - dt.timedelta(days=days_old)

    return {
        "title": title, "excerpt": excerpt,
        "url": "https://news.ycombinator.com/item?id=1",
        "created_at_source": when.isoformat().replace("+00:00", "Z"),
    }


def judge(title, excerpt="", days_old=1):
    return community.evaluate_thread(
        thread(title, excerpt, days_old), SETTINGS, now=NOW
    )


def test_a_direct_question_is_worth_a_reply():
    verdict = judge("Which tool: open source vulnerability scanner?",
                    "We need network scanning with CVE data")

    assert verdict["advice"] == "reply"
    assert verdict["intent"] == "asking"


def test_a_relevant_discussion_is_only_worth_considering():
    verdict = judge("Comparison of web application security scanners",
                    "penetration testing review of several tools")

    assert verdict["advice"] == "consider"


def test_an_off_topic_thread_is_skipped_however_it_is_phrased():
    """Real case: this scored 25 on the word "alternative" alone."""
    verdict = judge("Pando - OpenAI's alternative to Git for source control")

    assert verdict["advice"] == "skip"
    assert "not about our subject" in verdict["reasons"][0]


def test_the_intent_bonus_cannot_rescue_an_unrelated_thread():
    asking_but_unrelated = judge("Which tool do you recommend for baking?")

    assert asking_but_unrelated["advice"] == "skip"


def test_a_mention_without_a_question_is_not_an_invitation():
    verdict = judge("We ran a vulnerability scanner on our CVE backlog",
                    "notes about penetration testing")

    assert verdict["advice"] == "skip"
    assert "nobody asked" in verdict["reasons"][0]


def test_old_threads_are_skipped_with_their_age():
    verdict = judge("Which tool: open source vulnerability scanner?",
                    "network scanning", days_old=400)

    assert verdict["advice"] == "skip"
    assert "400 days old" in verdict["reasons"][0]


def test_the_avoid_list_wins_over_everything():
    verdict = judge(
        "Which vulnerability scanner — we are hiring a security engineer",
        "network scanning penetration testing"
    )

    assert verdict["advice"] == "skip"
    assert "avoid list" in verdict["reasons"][0]


def test_intent_detection_prefers_the_strongest_signal():
    assert community.detect_intent("a comparison and a recommend", SETTINGS) \
        == "asking"
    assert community.detect_intent("just a comparison", SETTINGS) \
        == "discussing"
    assert community.detect_intent("nothing in particular", SETTINGS) \
        == "mentioning"


def test_age_handles_missing_and_broken_timestamps():
    assert community.age_days("", NOW) is None
    assert community.age_days("not a date", NOW) is None
    assert community.age_days("2026-11-01T00:00:00Z", NOW) == 3
    assert community.age_days("2026-11-01 00:00:00", NOW) == 3


def test_config_must_stay_draft_only(monkeypatch, tmp_path):
    from sge import config, paths

    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "community.yml").write_text(
        "queries:\n  hackernews: ['x']\nrules:\n  mode: automatic\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "CONFIG_DIR", broken)

    with pytest.raises(config.ConfigError, match="draft_only"):
        community.load_rules()


def test_the_repository_config_loads_and_is_draft_only():
    settings = community.load_rules()

    assert settings["rules"]["mode"] == "draft_only"
    assert settings["queries"]["hackernews"]
