from __future__ import annotations

import json

import pytest

from segmentation import (
    AssignedSegment,
    SegmentNoteContent,
    SegmentPlan,
    SegmentationError,
    SemanticSegment,
    assign_cues_to_segments,
    parse_segment_note_contents,
    parse_segment_plan,
    render_segmented_notes,
)
from transcript import Transcript, TranscriptCue


def _transcript(*cues: TranscriptCue) -> Transcript:
    return Transcript(source="bilibili", language="zh-CN", cues=cues)


def _plan_json(segments: list[dict[str, object]]) -> str:
    return json.dumps({"segments": segments}, ensure_ascii=False)


def test_parse_valid_segment_plan_json() -> None:
    plan = parse_segment_plan(
        "```json\n"
        + _plan_json(
            [
                {"title": "变量", "start_seconds": 0, "end_seconds": 10.5},
                {"title": "函数", "start_seconds": 10.5, "end_seconds": 25},
            ]
        )
        + "\n```"
    )

    assert [segment.title for segment in plan.segments] == ["变量", "函数"]
    assert plan.segments[1].start_seconds == 10.5


@pytest.mark.parametrize(
    ("segments", "message"),
    [
        ([], "至少需要一个"),
        ([{"title": "", "start_seconds": 0, "end_seconds": 1}], "标题不能为空"),
        ([{"title": "A", "start_seconds": 1, "end_seconds": 1}], "晚于"),
        ([{"title": "A", "start_seconds": 2, "end_seconds": 1}], "晚于"),
        (
            [
                {"title": "A", "start_seconds": 10, "end_seconds": 20},
                {"title": "B", "start_seconds": 0, "end_seconds": 5},
            ],
            "递增",
        ),
        (
            [
                {"title": "A", "start_seconds": 0, "end_seconds": 10},
                {"title": "B", "start_seconds": 9, "end_seconds": 20},
            ],
            "重叠",
        ),
    ],
)
def test_parse_rejects_invalid_segment_plans(
    segments: list[dict[str, object]],
    message: str,
) -> None:
    with pytest.raises(SegmentationError, match=message):
        parse_segment_plan(_plan_json(segments))


@pytest.mark.parametrize(
    "raw_response",
    [
        "not json",
        "[]",
        '{"segments": "wrong"}',
        '{"segments": [{"title": "A", "start_seconds": 0}]}',
        '{"segments": [{"title": "A", "start_seconds": NaN, "end_seconds": 1}]}',
    ],
)
def test_parse_rejects_malformed_or_non_finite_plan_json(raw_response: str) -> None:
    with pytest.raises(SegmentationError):
        parse_segment_plan(raw_response)


def test_assigns_each_cue_by_intersection_without_duplicates() -> None:
    crossing = TranscriptCue(4.0, 7.0, "跨边界但后段相交更长")
    transcript = _transcript(
        TranscriptCue(0.0, 2.0, "第一段"),
        crossing,
        TranscriptCue(7.0, 9.0, "第二段"),
    )
    plan = SegmentPlan(
        (
            SemanticSegment("前段", 0.0, 5.0),
            SemanticSegment("后段", 5.0, 10.0),
        )
    )

    assigned = assign_cues_to_segments(transcript, plan)

    assert [cue.text for cue in assigned[0].transcript.cues] == ["第一段"]
    assert [cue.text for cue in assigned[1].transcript.cues] == [
        "跨边界但后段相交更长",
        "第二段",
    ]
    all_cues = [cue for segment in assigned for cue in segment.transcript.cues]
    assert len(all_cues) == len(set(map(id, all_cues))) == 3


def test_equal_cross_boundary_overlap_deterministically_goes_to_earlier_segment() -> None:
    tied = TranscriptCue(4.0, 6.0, "相交时长相同")
    assigned = assign_cues_to_segments(
        _transcript(
            TranscriptCue(0.0, 1.0, "前段内容"),
            tied,
            TranscriptCue(6.0, 7.0, "后段内容"),
        ),
        SegmentPlan(
            (
                SemanticSegment("前段", 0.0, 5.0),
                SemanticSegment("后段", 5.0, 8.0),
            )
        ),
    )

    assert tied in assigned[0].transcript.cues
    assert tied not in assigned[1].transcript.cues


def test_short_boundary_cue_outside_plan_is_tolerated() -> None:
    assigned = assign_cues_to_segments(
        _transcript(
            TranscriptCue(0.0, 1.0, "短异常 cue"),
            TranscriptCue(2.0, 5.0, "正常内容"),
        ),
        SegmentPlan((SemanticSegment("正常分段", 2.0, 5.0),)),
    )

    assert [cue.text for cue in assigned[0].transcript.cues] == ["正常内容"]


def test_long_effective_cue_omission_is_rejected() -> None:
    with pytest.raises(SegmentationError, match="超出容差"):
        assign_cues_to_segments(
            _transcript(
                TranscriptCue(0.0, 2.0, "被遗漏的大段字幕"),
                TranscriptCue(3.0, 5.0, "正常内容"),
            ),
            SegmentPlan((SemanticSegment("正常分段", 3.0, 5.0),)),
        )


def test_many_short_omissions_over_total_tolerance_are_rejected() -> None:
    with pytest.raises(SegmentationError, match="累计 3.300 秒"):
        assign_cues_to_segments(
            _transcript(
                TranscriptCue(0.0, 1.1, "遗漏一"),
                TranscriptCue(2.0, 3.1, "遗漏二"),
                TranscriptCue(4.0, 5.1, "遗漏三"),
                TranscriptCue(10.0, 11.0, "正常内容"),
            ),
            SegmentPlan((SemanticSegment("正常分段", 10.0, 11.0),)),
        )


def test_every_segment_must_receive_an_effective_cue() -> None:
    with pytest.raises(SegmentationError, match="分段 2"):
        assign_cues_to_segments(
            _transcript(TranscriptCue(0.0, 1.0, "只有第一段")),
            SegmentPlan(
                (
                    SemanticSegment("第一段", 0.0, 1.0),
                    SemanticSegment("空分段", 2.0, 3.0),
                )
            ),
        )


def test_parse_contents_and_program_render_final_structure() -> None:
    segment = SemanticSegment("变量与命名", 0.0, 65.0)
    assigned = (
        AssignedSegment(
            segment,
            _transcript(TranscriptCue(0.0, 65.0, "变量字幕")),
        ),
    )
    contents = parse_segment_note_contents(
        json.dumps(
            {
                "segments": [
                    {
                        "segment_number": 1,
                        "body_markdown": "### 概念\n变量用于绑定数据。",
                        "summary_points": ["命名要表达含义", "变量绑定数据"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        expected_count=1,
    )

    markdown = render_segmented_notes(assigned, contents, video_title="Python 入门")

    assert markdown.startswith("# Python 入门：分段学习笔记")
    assert "## 1. 变量与命名" in markdown
    assert "**时间：00:00–01:05**" in markdown
    assert markdown.count("### 总结重点") == 1
    assert "- 命名要表达含义" in markdown
    assert "本段概要" not in markdown


@pytest.mark.parametrize(
    "payload",
    [
        {"segments": []},
        {
            "segments": [
                {
                    "segment_number": 2,
                    "body_markdown": "正文",
                    "summary_points": ["重点"],
                }
            ]
        },
        {
            "segments": [
                {
                    "segment_number": 1,
                    "body_markdown": "正文",
                    "summary_points": [],
                }
            ]
        },
        {
            "segments": [
                {
                    "segment_number": 1,
                    "body_markdown": "正文\n\n### 总结重点",
                    "summary_points": ["重点"],
                }
            ]
        },
    ],
)
def test_parse_rejects_incomplete_or_model_rendered_content(
    payload: dict[str, object],
) -> None:
    with pytest.raises(SegmentationError):
        parse_segment_note_contents(json.dumps(payload, ensure_ascii=False), expected_count=1)


def test_render_rejects_plan_content_count_mismatch() -> None:
    assigned = (
        AssignedSegment(
            SemanticSegment("A", 0.0, 1.0),
            _transcript(TranscriptCue(0.0, 1.0, "字幕")),
        ),
    )
    with pytest.raises(SegmentationError, match="数量不一致"):
        render_segmented_notes(assigned, ())


def test_summary_points_are_model_content_not_program_placeholder() -> None:
    content = SegmentNoteContent("正文", ("模型生成的独特重点",))
    assigned = (
        AssignedSegment(
            SemanticSegment("A", 0.0, 1.0),
            _transcript(TranscriptCue(0.0, 1.0, "字幕")),
        ),
    )

    markdown = render_segmented_notes(assigned, (content,))

    assert "### 总结重点\n\n- 模型生成的独特重点" in markdown
