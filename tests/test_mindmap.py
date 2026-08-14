from __future__ import annotations

from mindmap import parse_markdown_outline, render_mindmap_html


def test_parse_markdown_outline_keeps_headings_and_lists_only() -> None:
    nodes = parse_markdown_outline(
        "# 主题\n长段落正文不会进入节点。\n## 概念\n- 要点一\n  - 子要点\n"
    )
    assert [node.text for node in nodes] == ["主题"]
    assert [node.text for node in nodes[0].children] == ["概念"]
    assert [node.text for node in nodes[0].children[0].children] == ["要点一"]
    assert nodes[0].children[0].children[0].children[0].text == "子要点"


def test_mindmap_escapes_html_and_limits_long_labels() -> None:
    markdown = "# <危险>\n- " + ("很长" * 100)
    html = render_mindmap_html(markdown)
    assert "&lt;危险&gt;" in html
    assert "很长" * 100 not in html
    assert "mindmap-viewport" in html


def test_empty_mindmap_is_readable() -> None:
    assert "没有可用于思维导图" in render_mindmap_html("普通正文")
