from sge import text


def test_tags_are_removed_and_entities_decoded():
    assert text.plain("<p>ZAP 2.16 &amp; more</p>") == "ZAP 2.16 & more"


def test_a_table_becomes_readable_text():
    """The ZAP release notes are a full HTML table."""
    html = ("<table><tbody><tr><td>File</td><td>Checksum</td></tr>"
            "<tr><td>zap.zip</td><td>a54b4f</td></tr></tbody></table>")

    result = text.plain(html)

    assert "tbody" not in result
    assert result == "File Checksum zap.zip a54b4f"


def test_a_tag_truncated_by_a_length_limit_is_removed():
    """A summary cut at 2000 characters can end mid-tag."""
    truncated = 'See the catalog that meets the <a href="https://www.cisa.g'

    assert text.plain(truncated) == "See the catalog that meets the"


def test_non_breaking_spaces_become_ordinary_spaces():
    assert text.plain("A B") == "A B"
    assert text.plain("A&nbsp;B") == "A B"


def test_whitespace_is_collapsed():
    assert text.plain("  many\n\n  spaces\there ") == "many spaces here"


def test_empty_input_is_an_empty_string():
    assert text.plain("") == ""
    assert text.plain(None) == ""


def test_the_limit_cuts_on_a_word_boundary():
    result = text.plain("one two three four five six", limit=12)

    assert result == "one two…"


def test_text_under_the_limit_is_unchanged():
    assert text.plain("short", limit=100) == "short"


def test_as_int_converts_what_it_can():
    assert text.as_int("5") == 5
    assert text.as_int(5) == 5
    assert text.as_int(3.7) == 3


def test_as_int_falls_back_instead_of_raising():
    """int("many") would end a whole scan over a comment count."""
    for value in ("many", None, "", [], {}, "1.5"):
        assert text.as_int(value) == 0

    assert text.as_int("nonsense", 7) == 7
