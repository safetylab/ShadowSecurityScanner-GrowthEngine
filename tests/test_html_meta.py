from sge import html_meta

PAGE = """<!DOCTYPE html>
<html><head>
<title>  A page title  </title>
<meta name="description" content="A description.">
<meta property="og:title" content="OG title">
<link rel="canonical" href="https://example.test/page">
</head><body>
<h1>Main heading</h1>
<h2>Second</h2>
<a href="/internal">internal</a>
<a href="https://other.test/x">external</a>
<a href="#anchor">anchor</a>
<img src="a.png" alt="described">
<img src="b.png">
</body></html>"""


def test_metadata_is_extracted():
    page = html_meta.parse_page(PAGE)

    assert page["title"] == "A page title"
    assert page["meta"]["description"] == "A description."
    assert page["meta"]["og:title"] == "OG title"
    assert page["canonical"] == "https://example.test/page"


def test_headings_keep_their_level():
    page = html_meta.parse_page(PAGE)

    assert ("h1", "Main heading") in page["headings"]
    assert ("h2", "Second") in page["headings"]


def test_links_and_images_are_counted():
    page = html_meta.parse_page(PAGE)

    assert "/internal" in page["links"]
    assert "https://other.test/x" in page["links"]
    assert page["images"] == 2
    assert page["images_without_alt"] == 1


def test_missing_head_elements_are_empty_not_errors():
    page = html_meta.parse_page("<html><body><p>bare</p></body></html>")

    assert page["title"] == ""
    assert page["canonical"] == ""
    assert page["meta"] == {}


def test_malformed_html_does_not_raise():
    page = html_meta.parse_page("<html><title>Unclosed<body><a href=x>")

    assert "Unclosed" in page["title"]


def test_entities_in_the_title_are_decoded():
    page = html_meta.parse_page("<title>A &amp; B</title>")

    assert page["title"] == "A & B"
