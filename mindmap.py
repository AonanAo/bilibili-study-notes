"""从笔记 Markdown 提取安全、可折叠的第一版思维导图。"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
import re


_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<text>.+?)\s*#*\s*$")
_LIST_RE = re.compile(r"^(?P<indent>\s*)[-*+]\s+(?P<text>.+?)\s*$")
_MAX_LABEL_LENGTH = 120


@dataclass
class _MutableNode:
    text: str
    level: int
    children: list["_MutableNode"] = field(default_factory=list)


@dataclass(frozen=True)
class MindMapNode:
    """一个思维导图节点，只保留知识层级和关键点。"""

    text: str
    level: int
    children: tuple["MindMapNode", ...] = ()


def _clean_label(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > _MAX_LABEL_LENGTH:
        return value[: _MAX_LABEL_LENGTH - 1].rstrip() + "…"
    return value


def _freeze(node: _MutableNode) -> MindMapNode:
    return MindMapNode(
        text=node.text,
        level=node.level,
        children=tuple(_freeze(child) for child in node.children),
    )


def parse_markdown_outline(markdown: str) -> tuple[MindMapNode, ...]:
    """提取 Markdown 标题和列表，忽略长段落正文。"""

    roots: list[_MutableNode] = []
    stack: list[_MutableNode] = []
    for line in markdown.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            text = _clean_label(heading.group("text"))
            level = len(heading.group("marks"))
        else:
            item = _LIST_RE.match(line)
            if not item:
                continue
            text = _clean_label(item.group("text"))
            indent_level = len(item.group("indent").expandtabs(2)) // 2
            parent_level = stack[-1].level if stack else 0
            level = max(parent_level + 1, indent_level + 1)
        if not text:
            continue

        node = _MutableNode(text=text, level=level)
        while stack and stack[-1].level >= level:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)
    return tuple(_freeze(root) for root in roots)


def _render_node(node: MindMapNode) -> str:
    children = "".join(_render_node(child) for child in node.children)
    child_markup = f"<ul>{children}</ul>" if children else ""
    open_attr = " open" if node.level <= 2 else ""
    return (
        f"<li><details{open_attr}><summary>{escape(node.text)}</summary>"
        f"{child_markup}</details></li>"
    )


def render_mindmap_html(markdown: str) -> str:
    """生成不依赖外部资源的思维导图 HTML。"""

    nodes = parse_markdown_outline(markdown)
    if not nodes:
        return '<div class="mindmap-empty">没有可用于思维导图的标题或列表。</div>'
    body = "".join(_render_node(node) for node in nodes)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #263238; }}
.mindmap-toolbar {{ position: sticky; top: 0; z-index: 2; display: flex; gap: 8px; padding: 0 0 10px; background: #f7f9fc; }}
.mindmap-toolbar button {{ border: 1px solid #b8c5d6; background: #fff; border-radius: 6px; padding: 5px 11px; cursor: pointer; font-size: 14px; }}
.mindmap-viewport {{ min-height: 680px; height: 680px; overflow: hidden; padding: 14px; background: #f7f9fc; border: 1px solid #d9e0ea; border-radius: 10px; cursor: grab; }}
.mindmap-viewport.dragging {{ cursor: grabbing; }}
.mindmap-canvas {{ transform-origin: 0 0; width: max-content; min-width: 100%; }}
ul {{ list-style: none; margin: 0; padding-left: 24px; border-left: 1px solid #b8c5d6; }}
.mindmap-canvas > ul {{ padding-left: 0; border-left: 0; }}
li {{ margin: 7px 0; }}
summary {{ cursor: pointer; max-width: 720px; padding: 6px 10px; border-radius: 6px; background: #fff; box-shadow: 0 1px 3px rgba(30,50,80,.12); white-space: normal; font-size: 15px; line-height: 1.4; }}
summary:hover {{ background: #eaf2ff; }}
.mindmap-empty {{ color: #667085; padding: 16px; border: 1px solid #d9e0ea; border-radius: 8px; }}
</style></head><body>
<div class="mindmap-toolbar"><button id="zoomIn">放大</button><button id="zoomOut">缩小</button><button id="reset">复位</button></div>
<div class="mindmap-viewport" id="viewport"><div class="mindmap-canvas" id="canvas"><ul>{body}</ul></div></div>
<script>
const viewport=document.getElementById('viewport'), canvas=document.getElementById('canvas');
let scale=.9, dragging=false, startX=0, startY=0, originX=0, originY=0;
function apply(){{canvas.style.transform=`translate(${{originX}}px, ${{originY}}px) scale(${{scale}})`;}}
document.getElementById('zoomIn').onclick=()=>{{scale=Math.min(2.5,scale*1.15);apply();}};
document.getElementById('zoomOut').onclick=()=>{{scale=Math.max(.45,scale*.85);apply();}};
document.getElementById('reset').onclick=()=>{{scale=.9;originX=0;originY=0;apply();}};
viewport.addEventListener('wheel', e=>{{e.preventDefault(); scale=Math.min(2.5,Math.max(.55,scale*(e.deltaY<0?1.1:.9)));apply();}},{{passive:false}});
viewport.addEventListener('pointerdown', e=>{{dragging=true;viewport.classList.add('dragging');startX=e.clientX-originX;startY=e.clientY-originY;viewport.setPointerCapture(e.pointerId);}});
viewport.addEventListener('pointermove', e=>{{if(!dragging)return;originX=e.clientX-startX;originY=e.clientY-startY;apply();}});
viewport.addEventListener('pointerup', ()=>{{dragging=false;viewport.classList.remove('dragging');}});
apply();
</script></body></html>"""
