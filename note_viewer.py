"""已生成笔记的统一查看器数据模型和选择规则。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ViewerContentKind = Literal["overall", "secondary", "segmented", "part", "transcript"]


@dataclass(frozen=True)
class ViewerContent:
    """查看器中的一份已经生成的内容。"""

    content_id: str
    label: str
    kind: ViewerContentKind
    text: str
    filename: str
    download_mime: str = "text/markdown"
    template_name: str | None = None
    section_keys: tuple[str, ...] = ()
    transcript_source: str | None = None
    srt_text: str | None = None
    srt_filename: str | None = None

    def __post_init__(self) -> None:
        if not self.content_id.strip():
            raise ValueError("查看器内容 ID 不能为空。")
        if not self.label.strip():
            raise ValueError("查看器内容标题不能为空。")
        if not self.filename.strip():
            raise ValueError("查看器内容文件名不能为空。")
        if self.kind == "transcript" and not self.transcript_source:
            raise ValueError("字幕内容必须标记来源。")


def validate_viewer_contents(
    contents: tuple[ViewerContent, ...] | list[ViewerContent],
) -> tuple[ViewerContent, ...]:
    """校验内容 ID 唯一，并返回不可变内容集合。"""

    result = tuple(contents)
    ids = [content.content_id for content in result]
    if len(ids) != len(set(ids)):
        raise ValueError("查看器内容 ID 不能重复。")
    return result


def get_viewer_content(
    contents: tuple[ViewerContent, ...] | list[ViewerContent],
    content_id: str,
) -> ViewerContent:
    """按稳定 ID 查找内容。"""

    for content in contents:
        if content.content_id == content_id:
            return content
    raise ValueError(f"找不到查看器内容：{content_id}")


def select_viewer_pair(
    contents: tuple[ViewerContent, ...] | list[ViewerContent],
    left_id: str,
    right_id: str,
) -> tuple[ViewerContent, ViewerContent]:
    """选择双篇对比内容，禁止两侧选择同一份内容。"""

    if left_id == right_id:
        raise ValueError("左右两侧不能选择同一份内容。")
    return get_viewer_content(contents, left_id), get_viewer_content(contents, right_id)
