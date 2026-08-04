"""DuckDuckGo HTML parsing for web_search."""

from backend.workspace.web_search import _parse_ddg_html, web_search


def test_parse_ddg_classic_layout():
    html = (
        '<div class="result">'
        '<a class="result__a" href="https://example.com/a">Example A</a>'
        '<a class="result__snippet">Snippet A text here</a>'
        "</div>"
        '<div class="result">'
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fb">Example B</a>'
        '<a class="result__snippet">Snippet B</a>'
        "</div>"
    )
    out = _parse_ddg_html(html, 5)
    assert len(out) == 2
    assert "Example A" in out[0]
    assert "https://example.com/a" in out[0]
    assert "https://example.com/b" in out[1]


def test_parse_ddg_result_title_layout():
    html = (
        '<div class="result">'
        '<h2 class="result__title">'
        '<a class="result__a" href="https://docs.python.org/3/">Python docs</a>'
        "</h2>"
        '<a class="result__snippet">Official documentation</a>'
        "</div>"
    )
    out = _parse_ddg_html(html, 3)
    assert len(out) == 1
    assert "Python docs" in out[0]
    assert "docs.python.org" in out[0]


def test_no_results_is_error_for_tool_success():
    from backend.agents.tool_outcomes import is_tool_failure

    assert is_tool_failure("web_search", "Error: No web results for 'x'.")
    assert not is_tool_failure(
        "web_search",
        "Web search (DuckDuckGo): 'x' (1 result(s))\n--- Result 1 ---\nTitle\nhttps://x.com\n",
    )
