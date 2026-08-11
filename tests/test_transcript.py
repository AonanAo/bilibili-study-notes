from __future__ import annotations

import pytest

from transcript import Transcript, TranscriptCue, TranscriptParseError, parse_srt


def test_parse_srt_preserves_timeline_and_plain_text() -> None:
    transcript = parse_srt(
        """\ufeff1\r
00:00:00,250 --> 00:00:02,500\r
大家好\r
\r
00:00:02.500 --> 00:00:04.000\r
Welcome to\r
this course.\r
""",
        source="bilibili",
        language="zh-CN",
    )

    assert transcript.source == "bilibili"
    assert transcript.language == "zh-CN"
    assert transcript.plain_text == "大家好\nWelcome to this course."
    assert transcript.cues == (
        TranscriptCue(0.25, 2.5, "大家好"),
        TranscriptCue(2.5, 4.0, "Welcome to this course."),
    )


def test_parse_srt_returns_empty_transcript_for_empty_input() -> None:
    transcript = parse_srt("\ufeff \n", source="bilibili", language="zh-CN")

    assert transcript.cues == ()
    assert transcript.plain_text == ""
    assert transcript.to_srt() == ""


def test_transcript_slice_keeps_overlapping_cues_and_absolute_times() -> None:
    transcript = Transcript(
        source="bilibili",
        language="zh-CN",
        cues=(
            TranscriptCue(0.0, 2.0, "第一段"),
            TranscriptCue(2.0, 4.0, "第二段"),
            TranscriptCue(4.0, 6.0, "第三段"),
        ),
    )

    sliced = transcript.slice(1.5, 4.0)

    assert sliced.source == "bilibili"
    assert sliced.language == "zh-CN"
    assert sliced.cues == transcript.cues[:2]
    assert sliced.plain_text == "第一段\n第二段"


def test_transcript_to_srt_can_be_parsed_again() -> None:
    original = Transcript(
        source="bilibili",
        language="zh-CN",
        cues=(
            TranscriptCue(0.125, 2.5, "第一段"),
            TranscriptCue(3661.0, 3662.75, "超过一小时"),
        ),
    )

    srt_text = original.to_srt()

    assert "00:00:00,125 --> 00:00:02,500" in srt_text
    assert "01:01:01,000 --> 01:01:02,750" in srt_text
    assert parse_srt(
        srt_text,
        source="bilibili",
        language="zh-CN",
    ) == original


@pytest.mark.parametrize(
    "timeline",
    [
        "00:00:02,000 --> 00:00:02,000",
        "00:00:03,000 --> 00:00:02,000",
    ],
)
def test_parse_srt_rejects_end_time_not_after_start(timeline: str) -> None:
    with pytest.raises(
        TranscriptParseError,
        match="字幕结束时间必须晚于开始时间",
    ):
        parse_srt(
            f"1\n{timeline}\n字幕\n",
            source="bilibili",
            language="zh-CN",
        )


def test_parse_srt_reports_missing_timeline() -> None:
    with pytest.raises(TranscriptParseError, match="缺少有效时间轴"):
        parse_srt(
            "1\n这不是时间轴\n字幕\n",
            source="bilibili",
            language="zh-CN",
        )


def test_parse_srt_reports_invalid_time_value() -> None:
    with pytest.raises(TranscriptParseError, match="超出有效范围"):
        parse_srt(
            "1\n00:61:00,000 --> 00:61:01,000\n字幕\n",
            source="bilibili",
            language="zh-CN",
        )


def test_transcript_rejects_decreasing_start_times() -> None:
    with pytest.raises(TranscriptParseError, match="按开始时间递增"):
        Transcript(
            source="bilibili",
            language="zh-CN",
            cues=(
                TranscriptCue(2.0, 3.0, "第二段"),
                TranscriptCue(1.0, 2.0, "第一段"),
            ),
        )


@pytest.mark.parametrize(
    ("start_seconds", "end_seconds"),
    [(-1.0, 2.0), (2.0, 2.0), (3.0, 2.0)],
)
def test_transcript_slice_rejects_invalid_range(
    start_seconds: float,
    end_seconds: float,
) -> None:
    transcript = Transcript(source="bilibili", language="zh-CN", cues=())

    with pytest.raises(ValueError):
        transcript.slice(start_seconds, end_seconds)
