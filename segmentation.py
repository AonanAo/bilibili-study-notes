"""语义分段计划、字幕 cue 分配和分段笔记渲染。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from transcript import Transcript, TranscriptCue


# 模型边界可能略早或略晚于字幕轨。只容忍真正很短的边界/异常 cue，
# 不按视频总时长放大容差，避免长视频漏掉几十秒内容仍被接受。
MAX_OMITTED_CUE_SECONDS = 1.5
MAX_TOTAL_OMITTED_SECONDS = 3.0
PROGRAM_STRUCTURE_PATTERN = re.compile(
    r"^\s*(?:#{1,2}\s+.+|#{3,6}\s*(?:总结重点|本段概要)\s*|\*\*时间：.+\*\*)$",
    re.MULTILINE,
)


class SegmentationError(ValueError):
    """分段 JSON、cue 分配或分段内容不符合协议。"""


@dataclass(frozen=True)
class SemanticSegment:
    """模型规划的一个语义分段。"""

    title: str
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise SegmentationError("分段标题不能为空。")
        if (
            isinstance(self.start_seconds, bool)
            or not isinstance(self.start_seconds, (int, float))
            or not math.isfinite(self.start_seconds)
            or self.start_seconds < 0
        ):
            raise SegmentationError("分段开始时间必须是非负有限数值。")
        if (
            isinstance(self.end_seconds, bool)
            or not isinstance(self.end_seconds, (int, float))
            or not math.isfinite(self.end_seconds)
        ):
            raise SegmentationError("分段结束时间必须是有限数值。")
        if self.end_seconds <= self.start_seconds:
            raise SegmentationError("分段结束时间必须晚于开始时间。")


@dataclass(frozen=True)
class SegmentPlan:
    """按时间递增、互不重叠的完整语义分段方案。"""

    segments: tuple[SemanticSegment, ...]

    def __post_init__(self) -> None:
        if not self.segments:
            raise SegmentationError("分段方案至少需要一个分段。")
        previous_end = -1.0
        for segment in self.segments:
            if segment.start_seconds < previous_end:
                raise SegmentationError("分段范围必须递增且不能重叠。")
            previous_end = segment.end_seconds


@dataclass(frozen=True)
class AssignedSegment:
    """一个分段及唯一分配给它的字幕 cue。"""

    segment: SemanticSegment
    transcript: Transcript


@dataclass(frozen=True)
class SegmentNoteContent:
    """模型为一个分段生成的自适应正文和总结重点。"""

    body_markdown: str
    summary_points: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.body_markdown, str) or not self.body_markdown.strip():
            raise SegmentationError("分段笔记正文不能为空。")
        if PROGRAM_STRUCTURE_PATTERN.search(self.body_markdown):
            raise SegmentationError(
                "分段正文不能自行生成序号、标题、时间、“本段概要”或“总结重点”。"
            )
        if not self.summary_points:
            raise SegmentationError("每个分段至少需要一条总结重点。")
        if any(not isinstance(point, str) or not point.strip() for point in self.summary_points):
            raise SegmentationError("总结重点必须是非空字符串。")


def _clean_json_response(raw_response: str) -> str:
    """移除模型偶尔添加的 JSON 代码块外壳。"""

    if not isinstance(raw_response, str) or not raw_response.strip():
        raise SegmentationError("DeepSeek 返回了空的 JSON。")
    content = raw_response.strip()
    if content.startswith("```") and content.endswith("```"):
        lines = content.splitlines()
        if len(lines) >= 3:
            content = "\n".join(lines[1:-1]).strip()
    return content


def _load_json_object(raw_response: str) -> dict[str, object]:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise SegmentationError(f"分段 JSON 包含重复字段：{key}。")
            value[key] = item
        return value

    def reject_non_standard_number(value: str) -> object:
        raise SegmentationError(f"分段 JSON 包含非有限数值：{value}。")

    try:
        value = json.loads(
            _clean_json_response(raw_response),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_standard_number,
        )
    except json.JSONDecodeError as error:
        raise SegmentationError(f"DeepSeek 返回的分段 JSON 无法解析：{error.msg}") from error
    if not isinstance(value, dict):
        raise SegmentationError("分段 JSON 顶层必须是对象。")
    return value


def parse_segment_plan(raw_response: str) -> SegmentPlan:
    """按严格协议解析并校验模型生成的分段方案。"""

    value = _load_json_object(raw_response)
    if set(value) != {"segments"}:
        raise SegmentationError("分段方案 JSON 顶层只能包含 segments。")
    raw_segments = value["segments"]
    if not isinstance(raw_segments, list):
        raise SegmentationError("segments 必须是数组。")

    segments: list[SemanticSegment] = []
    for index, item in enumerate(raw_segments, start=1):
        if not isinstance(item, dict):
            raise SegmentationError(f"第 {index} 个分段必须是对象。")
        if set(item) != {"title", "start_seconds", "end_seconds"}:
            raise SegmentationError(
                f"第 {index} 个分段必须且只能包含 title、start_seconds、end_seconds。"
            )
        try:
            segment = SemanticSegment(
                title=item["title"],
                start_seconds=item["start_seconds"],
                end_seconds=item["end_seconds"],
            )
        except SegmentationError as error:
            raise SegmentationError(f"第 {index} 个分段无效：{error}") from error
        segments.append(segment)
    return SegmentPlan(tuple(segments))


def _cue_overlap_seconds(cue: TranscriptCue, segment: SemanticSegment) -> float:
    return max(
        0.0,
        min(cue.end_seconds, segment.end_seconds)
        - max(cue.start_seconds, segment.start_seconds),
    )


def assign_cues_to_segments(
    transcript: Transcript,
    plan: SegmentPlan,
) -> tuple[AssignedSegment, ...]:
    """把 cue 唯一分配给相交最多的分段，并校验遗漏容差。

    相交时长相同则保留按时间更早的分段，这是由从前向后遍历且只在
    ``overlap > best_overlap`` 时替换保证的确定性规则。
    """

    if not transcript.cues:
        raise SegmentationError("字幕没有有效 cue，无法生成分段笔记。")

    assigned: list[list[TranscriptCue]] = [[] for _segment in plan.segments]
    omitted: list[TranscriptCue] = []
    for cue in transcript.cues:
        best_index: int | None = None
        best_overlap = 0.0
        for index, segment in enumerate(plan.segments):
            overlap = _cue_overlap_seconds(cue, segment)
            if overlap > best_overlap:
                best_index = index
                best_overlap = overlap
        if best_index is None:
            omitted.append(cue)
        else:
            assigned[best_index].append(cue)

    if omitted:
        omitted_durations = [cue.end_seconds - cue.start_seconds for cue in omitted]
        if (
            max(omitted_durations) > MAX_OMITTED_CUE_SECONDS
            or sum(omitted_durations) > MAX_TOTAL_OMITTED_SECONDS
        ):
            raise SegmentationError(
                "分段方案遗漏了超出容差的有效字幕："
                f"最长 {max(omitted_durations):.3f} 秒，"
                f"累计 {sum(omitted_durations):.3f} 秒；"
                f"允许每条不超过 {MAX_OMITTED_CUE_SECONDS:.1f} 秒且"
                f"累计不超过 {MAX_TOTAL_OMITTED_SECONDS:.1f} 秒。"
            )

    empty_segments = [index for index, cues in enumerate(assigned, start=1) if not cues]
    if empty_segments:
        labels = "、".join(str(index) for index in empty_segments)
        raise SegmentationError(f"分段 {labels} 没有分配到有效字幕 cue。")

    return tuple(
        AssignedSegment(
            segment=segment,
            transcript=Transcript(
                source=transcript.source,
                language=transcript.language,
                cues=tuple(cues),
            ),
        )
        for segment, cues in zip(plan.segments, assigned)
    )


def parse_segment_note_contents(
    raw_response: str,
    *,
    expected_count: int,
) -> tuple[SegmentNoteContent, ...]:
    """解析一次模型调用返回的全部分段正文。"""

    value = _load_json_object(raw_response)
    if set(value) != {"segments"}:
        raise SegmentationError("分段内容 JSON 顶层只能包含 segments。")
    raw_segments = value["segments"]
    if not isinstance(raw_segments, list):
        raise SegmentationError("分段内容 segments 必须是数组。")
    if len(raw_segments) != expected_count:
        raise SegmentationError(
            f"分段内容数量应为 {expected_count}，实际为 {len(raw_segments)}。"
        )

    contents: list[SegmentNoteContent] = []
    for index, item in enumerate(raw_segments, start=1):
        if not isinstance(item, dict):
            raise SegmentationError(f"第 {index} 段内容必须是对象。")
        if set(item) != {"segment_number", "body_markdown", "summary_points"}:
            raise SegmentationError(
                f"第 {index} 段内容字段必须且只能包含 segment_number、"
                "body_markdown、summary_points。"
            )
        if item["segment_number"] != index:
            raise SegmentationError(f"第 {index} 段的 segment_number 必须为 {index}。")
        points = item["summary_points"]
        if not isinstance(points, list):
            raise SegmentationError(f"第 {index} 段的 summary_points 必须是数组。")
        try:
            content = SegmentNoteContent(
                body_markdown=item["body_markdown"],
                summary_points=tuple(points),
            )
        except SegmentationError as error:
            raise SegmentationError(f"第 {index} 段内容无效：{error}") from error
        contents.append(content)
    return tuple(contents)


def format_note_time(seconds: float) -> str:
    """把分段秒数渲染为适合笔记阅读的时间。"""

    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, whole_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"
    return f"{minutes:02d}:{whole_seconds:02d}"


def render_segmented_notes(
    assigned_segments: tuple[AssignedSegment, ...],
    contents: tuple[SegmentNoteContent, ...],
    *,
    video_title: str = "",
) -> str:
    """由程序统一渲染序号、标题、时间和“总结重点”标题。"""

    if len(assigned_segments) != len(contents):
        raise SegmentationError("分段方案与分段内容数量不一致。")
    if not assigned_segments:
        raise SegmentationError("没有可渲染的分段笔记。")

    document_title = f"# {video_title.strip()}：分段学习笔记" if video_title.strip() else "# 分段学习笔记"
    blocks = [document_title]
    for index, (assigned, content) in enumerate(
        zip(assigned_segments, contents),
        start=1,
    ):
        segment = assigned.segment
        points = "\n".join(f"- {point.strip()}" for point in content.summary_points)
        blocks.append(
            f"## {index}. {segment.title.strip()}\n\n"
            f"**时间：{format_note_time(segment.start_seconds)}–"
            f"{format_note_time(segment.end_seconds)}**\n\n"
            f"{content.body_markdown.strip()}\n\n"
            f"### 总结重点\n\n{points}"
        )
    return "\n\n".join(blocks)
