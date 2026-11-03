import pytest

from sge import render


def test_placeholders_are_replaced():
    assert render.render("Hello {{name}}!", {"name": "world"}) == \
        "Hello world!"


def test_whitespace_in_placeholders_is_tolerated():
    assert render.render("{{ name }}", {"name": "x"}) == "x"


def test_repeated_placeholders_are_all_replaced():
    assert render.render("{{a}}-{{a}}", {"a": "1"}) == "1-1"


def test_missing_value_is_an_error_rather_than_an_empty_string():
    with pytest.raises(render.TemplateError, match="missing"):
        render.render("{{present}} {{missing}}", {"present": "ok"})


def test_values_are_not_re_scanned_for_placeholders():
    result = render.render("{{a}}", {"a": "{{b}}"})

    assert result == "{{b}}"


def test_placeholders_lists_template_variables():
    assert render.placeholders("{{b}} {{a}} {{b}}") == ["a", "b"]
