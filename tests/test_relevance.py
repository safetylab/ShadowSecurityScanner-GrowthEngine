from sge import relevance


def test_primary_keyword_in_the_title_scores_highest():
    high = relevance.score_item({
        "title": "New vulnerability scanner release",
        "summary": "", "link": "https://a.test/x",
    })[0]
    low = relevance.score_item({
        "title": "Unrelated news",
        "summary": "mentions a vulnerability scanner in passing",
        "link": "https://a.test/x",
    })[0]

    assert high > low > 0


def test_negative_keywords_exclude_without_destroying_the_score():
    """A real advisory that also says "webinar" stays scored, but is
    kept out of digests."""
    item = {
        "title": "Webinar: vulnerability scanner basics",
        "summary": "Conference registration is open.",
        "link": "https://a.test/x",
    }
    verdict = relevance.evaluate(item)

    assert verdict["relevance"] > 0
    assert verdict["excluded"] is True
    assert "webinar" in verdict["exclusion_reasons"]


def test_clean_items_are_not_excluded():
    verdict = relevance.evaluate({
        "title": "CVE in a vulnerability scanner",
        "summary": "", "link": "https://a.test/x",
    })

    assert verdict["excluded"] is False
    assert verdict["exclusion_reasons"] == []


def test_source_floor_rescues_items_our_keywords_miss():
    """Real case: an ICS advisory whose title is only a product name
    that matches none of our keywords."""
    item = {"title": "Kiloview E3 NDI Encoder",
            "summary": "", "link": "https://a.test/x"}

    without_floor, _ = relevance.score_item(item)
    with_floor, _ = relevance.score_item(item, floor=20)

    assert without_floor == 0
    assert with_floor == 20


def test_floor_never_lowers_a_good_score():
    item = {"title": "Critical CVE in a vulnerability scanner",
            "summary": "web application security", "link": "https://a.test/x"}

    high, _ = relevance.score_item(item)
    floored, _ = relevance.score_item(item, floor=10)

    assert floored == high


def test_score_never_leaves_its_range():
    stuffed = " ".join(["security scanner cve exploit"] * 50)
    score, _ = relevance.score_item({
        "title": stuffed, "summary": stuffed, "link": "https://a.test/x",
    })

    assert 0 <= score <= relevance.MAX_SCORE


def test_items_matching_nothing_score_zero():
    score, _ = relevance.score_item({
        "title": "Quarterly earnings call",
        "summary": "An award was received.",
        "link": "https://a.test/x",
    })

    assert score == 0


def test_topic_classification_picks_the_best_match():
    assert relevance.classify_item({
        "title": "Kubernetes container cloud security",
        "summary": "docker aws", "link": "https://a.test/x",
    }) == "cloud_and_containers"


def test_evaluate_returns_general_topic_for_zero_scores():
    verdict = relevance.evaluate({
        "title": "We are hiring", "summary": "press release",
        "link": "https://a.test/x",
    })

    assert verdict["relevance"] == 0
    assert verdict["topic"] == "general"
    assert verdict["excluded"] is True


def test_evaluate_reports_what_matched():
    verdict = relevance.evaluate({
        "title": "CVE in web application security",
        "summary": "", "link": "https://a.test/x",
    })

    assert "cve" in verdict["matched"]
    assert verdict["topic"] == "advisories"
