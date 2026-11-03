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


def test_negative_keywords_reduce_the_score():
    item = {
        "title": "Webinar: vulnerability scanner basics",
        "summary": "Conference registration is open.",
        "link": "https://a.test/x",
    }
    score, matched = relevance.score_item(item)

    assert any(keyword.startswith("-") for keyword in matched)
    assert score < relevance.MAX_SCORE


def test_score_never_leaves_its_range():
    stuffed = " ".join(["security scanner cve exploit"] * 50)
    score, _ = relevance.score_item({
        "title": stuffed, "summary": stuffed, "link": "https://a.test/x",
    })

    assert 0 <= score <= relevance.MAX_SCORE


def test_irrelevant_items_score_zero():
    score, _ = relevance.score_item({
        "title": "Quarterly earnings call",
        "summary": "Press release about an award.",
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


def test_evaluate_reports_what_matched():
    verdict = relevance.evaluate({
        "title": "CVE in web application security",
        "summary": "", "link": "https://a.test/x",
    })

    assert "cve" in verdict["matched"]
    assert verdict["topic"] == "advisories"
