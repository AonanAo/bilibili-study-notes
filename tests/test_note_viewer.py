from __future__ import annotations

import pytest

from note_viewer import ViewerContent, get_viewer_content, select_viewer_pair


def _contents() -> tuple[ViewerContent, ...]:
    return (
        ViewerContent("a", "总体笔记 A", "overall", "# A", "a.md"),
        ViewerContent("b", "总体笔记 B", "secondary", "# B", "b.md"),
        ViewerContent(
            "transcript",
            "原始字幕（B站）",
            "transcript",
            "字幕",
            "transcript.txt",
            download_mime="text/plain",
            transcript_source="bilibili",
            srt_text="1\n00:00:00,000 --> 00:00:01,000\n字幕\n",
            srt_filename="transcript.srt",
        ),
    )


def test_viewer_content_lookup_and_pair() -> None:
    contents = _contents()
    assert get_viewer_content(contents, "a").label == "总体笔记 A"
    left, right = select_viewer_pair(contents, "a", "b")
    assert (left.content_id, right.content_id) == ("a", "b")


def test_viewer_pair_rejects_same_content() -> None:
    with pytest.raises(ValueError, match="不能选择同一份"):
        select_viewer_pair(_contents(), "a", "a")


def test_transcript_requires_source() -> None:
    with pytest.raises(ValueError, match="标记来源"):
        ViewerContent("t", "字幕", "transcript", "字幕", "t.txt")
