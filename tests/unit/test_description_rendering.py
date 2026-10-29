import pytest

from jira_cli.descriptions import description
from jira_cli.presentation import text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", ""),
        ("https://example.test?a=1&b=2", "https://example.test?a=1&b=2"),
        ("https://example.test?a=1&amp;b=2", "https://example.test?a=1&b=2"),
        (
            '<a href="https://example.test?a=1&amp;b=2">Docs</a>',
            "Docs: https://example.test?a=1&b=2",
        ),
        ("[Docs|https://example.test?a=1&b=2]", "Docs: https://example.test?a=1&b=2"),
        ("[https://example.test]", "https://example.test"),
        ("First\n  second\n* third", "First\n  second\n* third"),
        ("`<a href='x'>&amp;</a>`", "`<a href='x'>&amp;</a>`"),
        (
            "{code}<script>literal</script>{code}",
            "{code}<script>literal</script>{code}",
        ),
        ("<p>Hi</p><script>alert(1)</script><p>Bye</p>", "Hi\n\nBye"),
        (
            "text <a href='https://example.test'>unfinished",
            "text unfinished: https://example.test",
        ),
        ("&amp;amp;", "&amp;"),
        (
            "https://example.test?a=1&copy=2&not=3",
            "https://example.test?a=1&copy=2&not=3",
        ),
        (
            "<pre><code>&lt;a&gt;[Label|https://example.test]&lt;/a&gt;</code></pre>",
            "<a>[Label|https://example.test]</a>",
        ),
        ("<code><script>literal</script></code>", "<script>literal</script>"),
        ("\tindented", "    indented"),
        ('<img src="x" onerror="alert(1)" alt="Image">', "Image"),
    ],
)
def test_description(raw: str | None, expected: str | None) -> None:
    assert description(raw) == expected


def test_multiple_links_and_terminal_controls() -> None:
    rendered = description("<p>[A|https://a.test]</p>https://b.test\x1b[2J\x9b31m")
    assert isinstance(rendered, str)
    assert "A: https://a.test" in rendered
    assert "https://b.test" in rendered
    assert "\x1b" not in text(rendered)
    assert "\x9b" not in text(rendered)
