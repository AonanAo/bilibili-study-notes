from __future__ import annotations

from pathlib import Path

import pytest

from bilibili import NoSubtitleError, VideoCollection, VideoPart, VideoSubtitle
from llm import LLMError
from prompt import resolve_note_template
from pipeline import (
    process_multi_part_video,
    process_single_part_video,
    safe_filename,
    save_part_notes,
)
from segmentation import SegmentNoteContent, SegmentPlan, SemanticSegment
from transcript import Transcript, TranscriptCue


def _collection() -> VideoCollection:
    return VideoCollection(
        bvid="BV1DfrdByE2Hx",
        title="Python 完整课程",
        description="",
        parts=(
            VideoPart(1, "第 1 分P", "https://example.test?p=1"),
            VideoPart(2, "第 2 分P", "https://example.test?p=2"),
            VideoPart(3, "第 3 分P", "https://example.test?p=3"),
        ),
    )


def _transcript(text: str) -> Transcript:
    return Transcript(
        source="bilibili",
        language="zh-CN",
        cues=(TranscriptCue(0.0, 1.0, text),),
    )


def test_safe_filename_and_part_output(tmp_path: Path) -> None:
    assert safe_filename('  Python/函数: "入门"  ') == "Python_函数_入门"

    path = save_part_notes(
        "# 视频主题\n函数",
        output_dir=tmp_path,
        page_number=2,
        title="Python/函数",
    )

    assert path.name == "P02_Python_函数.md"
    assert path.read_text(encoding="utf-8") == "# 视频主题\n函数\n"


def test_single_part_pipeline_passes_web_options_and_keeps_output_name(
    tmp_path: Path,
) -> None:
    collection = VideoCollection(
        bvid="BV1DfrdByE2Hx",
        title="Python 视频",
        description="页面简介",
        parts=(VideoPart(1, "Python 视频", "https://example.test/video"),),
    )
    received: dict[str, object] = {}
    video = VideoSubtitle(
        bvid=collection.bvid,
        title="真实视频标题",
        description="真实视频简介",
        transcript=_transcript("字幕正文"),
    )

    def fake_fetch(url: str, **kwargs: object) -> VideoSubtitle:
        received["url"] = url
        received["fetch_options"] = kwargs
        return video

    def fake_notes(subtitle_text: str, **kwargs: str) -> str:
        received["subtitle_text"] = subtitle_text
        received["note_options"] = kwargs
        return "# 视频主题\n单P笔记"

    report = process_single_part_video(
        collection,
        output_root=tmp_path,
        note_mode="technical",
        extra_instruction="重点解释代码。",
        subtitle_fetcher=fake_fetch,
        notes_generator=fake_notes,
    )

    assert received["url"] == collection.parts[0].url
    assert received["subtitle_text"] == "字幕正文"
    assert received["note_options"] == {
        "video_title": "真实视频标题",
        "video_description": "真实视频简介",
        "mode": "technical",
        "extra_instruction": "重点解释代码。",
    }
    assert report.video is video
    assert report.video.transcript.plain_text == "字幕正文"
    assert report.output_path == tmp_path / "BV1DfrdByE2Hx_study_notes.md"
    assert report.output_path.read_text(encoding="utf-8") == "# 视频主题\n单P笔记\n"


def test_single_part_default_keeps_one_model_call_and_no_segment_file(
    tmp_path: Path,
) -> None:
    collection = VideoCollection(
        bvid="BV1DfrdByE2Hx",
        title="Python",
        description="",
        parts=(VideoPart(1, "Python", "https://example.test/video"),),
    )
    calls: list[str] = []
    video = VideoSubtitle(
        collection.bvid,
        "Python",
        "",
        _transcript("字幕"),
    )

    def fake_notes(*_args: object, **_kwargs: object) -> str:
        calls.append("overall")
        return "# 视频主题\n总体笔记"

    def unexpected(*_args: object, **_kwargs: object) -> object:
        calls.append("unexpected")
        raise AssertionError("默认关闭时不得进入分段流程")

    report = process_single_part_video(
        collection,
        output_root=tmp_path,
        subtitle_fetcher=lambda *_args, **_kwargs: video,
        notes_generator=fake_notes,
        segment_planner=unexpected,
        segment_notes_generator=unexpected,
    )

    assert calls == ["overall"]
    assert report.segmented_notes_requested is False
    assert report.segmented_output_path is None
    assert not (tmp_path / "BV1DfrdByE2Hx_segmented_notes.md").exists()


def test_single_part_segmented_flow_uses_three_calls_in_order(
    tmp_path: Path,
) -> None:
    collection = VideoCollection(
        bvid="BV1DfrdByE2Hx",
        title="Python",
        description="",
        parts=(VideoPart(1, "Python", "https://example.test/video"),),
    )
    transcript = Transcript(
        "bilibili",
        "zh-CN",
        (
            TranscriptCue(0.0, 5.0, "变量字幕"),
            TranscriptCue(5.0, 10.0, "函数字幕"),
        ),
    )
    video = VideoSubtitle(collection.bvid, "Python", "简介", transcript)
    calls: list[str] = []

    def fake_notes(*_args: object, **_kwargs: object) -> str:
        calls.append("overall")
        return "# 视频主题\n总体笔记"

    def fake_plan(received: Transcript, **kwargs: object) -> SegmentPlan:
        calls.append("plan")
        assert received is transcript
        assert kwargs["video_title"] == "Python"
        return SegmentPlan(
            (
                SemanticSegment("变量", 0.0, 5.0),
                SemanticSegment("函数", 5.0, 10.0),
            )
        )

    def fake_segment_contents(assigned, **kwargs: object):
        calls.append("contents")
        assert [item.transcript.plain_text for item in assigned] == [
            "变量字幕",
            "函数字幕",
        ]
        assert kwargs["extra_instruction"] == "关注代码"
        return (
            SegmentNoteContent("变量正文", ("变量重点",)),
            SegmentNoteContent("函数正文", ("函数重点",)),
        )

    report = process_single_part_video(
        collection,
        output_root=tmp_path,
        extra_instruction="关注代码",
        generate_segmented_notes=True,
        subtitle_fetcher=lambda *_args, **_kwargs: video,
        notes_generator=fake_notes,
        segment_planner=fake_plan,
        segment_notes_generator=fake_segment_contents,
    )

    assert calls == ["overall", "plan", "contents"]
    assert report.output_path.exists()
    assert report.segmented_notes_requested is True
    assert report.segmented_error is None
    assert report.segmented_output_path == (
        tmp_path / "BV1DfrdByE2Hx_segmented_notes.md"
    )
    segmented = report.segmented_output_path.read_text(encoding="utf-8")
    assert "## 1. 变量" in segmented
    assert "## 2. 函数" in segmented
    assert segmented.count("### 总结重点") == 2


def test_single_part_supports_two_independent_overall_notes(
    tmp_path: Path,
) -> None:
    collection = VideoCollection(
        "BV1DfrdByE2Hx", "Python", "", (VideoPart(1, "Python", "https://example.test"),)
    )
    video = VideoSubtitle(collection.bvid, "Python", "", _transcript("字幕"))
    calls: list[str] = []
    primary = resolve_note_template("course", section_keys=("core_knowledge",))
    secondary = resolve_note_template("technical", section_keys=("principles",))

    def fake_notes(_text: str, **kwargs: object) -> str:
        calls.append(kwargs["note_template"].template_key)
        return f"# {kwargs['note_template'].name}\n\n## {kwargs['note_template'].sections[0].title}\n正文"

    report = process_single_part_video(
        collection,
        output_root=tmp_path,
        note_template=primary,
        secondary_note_template=secondary,
        subtitle_fetcher=lambda *_args, **_kwargs: video,
        notes_generator=fake_notes,
    )

    assert calls == ["course", "technical"]
    assert report.output_path.name == "BV1DfrdByE2Hx_study_notes.md"
    assert report.secondary_output_path is not None
    assert report.secondary_output_path.name == "BV1DfrdByE2Hx_study_notes_B.md"
    assert report.secondary_error is None


def test_secondary_overall_failure_preserves_primary_note(
    tmp_path: Path,
) -> None:
    collection = VideoCollection(
        "BV1DfrdByE2Hx", "Python", "", (VideoPart(1, "Python", "https://example.test"),)
    )
    video = VideoSubtitle(collection.bvid, "Python", "", _transcript("字幕"))
    calls = 0
    primary = resolve_note_template("course")
    secondary = resolve_note_template("technical")

    def fake_notes(_text: str, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise LLMError("第二份失败")
        return "# 主总结\n\n## 内容概括\n正文"

    report = process_single_part_video(
        collection,
        output_root=tmp_path,
        note_template=primary,
        secondary_note_template=secondary,
        subtitle_fetcher=lambda *_args, **_kwargs: video,
        notes_generator=fake_notes,
    )

    assert report.output_path.exists()
    assert report.secondary_output_path is None
    assert "第二份失败" in (report.secondary_error or "")


def test_two_overall_notes_and_segmented_notes_run_in_four_call_order(
    tmp_path: Path,
) -> None:
    collection = VideoCollection(
        "BV1DfrdByE2Hx", "Python", "", (VideoPart(1, "Python", "https://example.test"),)
    )
    transcript = Transcript(
        "bilibili", "zh-CN", (TranscriptCue(0.0, 1.0, "字幕"),)
    )
    video = VideoSubtitle(collection.bvid, "Python", "", transcript)
    calls: list[str] = []
    primary = resolve_note_template("course")
    secondary = resolve_note_template("technical")

    def fake_notes(_text: str, **kwargs: object) -> str:
        calls.append(kwargs["note_template"].template_key)
        return "# 笔记\n\n## 核心知识点\n正文"

    def fake_plan(*_args: object, **_kwargs: object) -> SegmentPlan:
        calls.append("plan")
        return SegmentPlan((SemanticSegment("段", 0.0, 1.0),))

    def fake_contents(*_args: object, **_kwargs: object):
        calls.append("contents")
        return (SegmentNoteContent("正文", ("重点",)),)

    report = process_single_part_video(
        collection,
        output_root=tmp_path,
        note_template=primary,
        secondary_note_template=secondary,
        generate_segmented_notes=True,
        subtitle_fetcher=lambda *_args, **_kwargs: video,
        notes_generator=fake_notes,
        segment_planner=fake_plan,
        segment_notes_generator=fake_contents,
    )

    assert calls == ["course", "technical", "plan", "contents"]
    assert report.secondary_output_path is not None
    assert report.segmented_output_path is not None


@pytest.mark.parametrize("failure_stage", ["plan", "contents"])
def test_segment_failure_preserves_overall_and_writes_no_pseudo_complete_file(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    collection = VideoCollection(
        bvid="BV1DfrdByE2Hx",
        title="Python",
        description="",
        parts=(VideoPart(1, "Python", "https://example.test/video"),),
    )
    transcript = _transcript("字幕")
    video = VideoSubtitle(collection.bvid, "Python", "", transcript)

    def fake_plan(*_args: object, **_kwargs: object) -> SegmentPlan:
        if failure_stage == "plan":
            raise LLMError("规划失败")
        return SegmentPlan((SemanticSegment("一段", 0.0, 1.0),))

    def fake_contents(*_args: object, **_kwargs: object):
        if failure_stage == "contents":
            raise LLMError("内容失败")
        raise AssertionError("规划失败后不应调用内容生成")

    report = process_single_part_video(
        collection,
        output_root=tmp_path,
        generate_segmented_notes=True,
        subtitle_fetcher=lambda *_args, **_kwargs: video,
        notes_generator=lambda *_args, **_kwargs: "# 视频主题\n总体笔记",
        segment_planner=fake_plan,
        segment_notes_generator=fake_contents,
    )

    assert report.output_path.read_text(encoding="utf-8").endswith("总体笔记\n")
    assert report.segmented_notes_requested is True
    expected_error = "规划失败" if failure_stage == "plan" else "内容失败"
    assert expected_error in report.segmented_error
    assert report.segmented_output_path is None
    assert not (tmp_path / "BV1DfrdByE2Hx_segmented_notes.md").exists()
    assert not list(tmp_path.glob(".*_segmented_notes_*.tmp"))


def test_multi_part_processing_skips_failures_and_creates_summary(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    summary_input: dict[str, object] = {}

    def fake_fetch(url: str, **_kwargs: object) -> VideoSubtitle:
        if url.endswith("p=2"):
            raise NoSubtitleError("该视频没有字幕", video_title="第二章")
        page = 1 if url.endswith("p=1") else 3
        return VideoSubtitle(
            bvid="BV1DfrdByE2Hx",
            title={1: "第一章", 3: "第三章"}[page],
            description="",
            transcript=_transcript(f"P{page} 字幕"),
        )

    def fake_notes(subtitle_text: str, **_kwargs: str) -> str:
        if subtitle_text == "P3 字幕":
            raise LLMError("模型生成失败")
        return f"# 视频主题\n{subtitle_text}"

    def fake_summary(
        part_notes: list[tuple[int, str, str]],
        **kwargs: object,
    ) -> str:
        summary_input["part_notes"] = part_notes
        summary_input.update(kwargs)
        return "# 视频整体主题\n课程总结\n\n## 处理状态\n已记录"

    report = process_multi_part_video(
        _collection(),
        output_root=tmp_path,
        on_event=events.append,
        subtitle_fetcher=fake_fetch,
        notes_generator=fake_notes,
        summary_generator=fake_summary,
    )

    assert report.succeeded_count == 1
    assert report.failed_count == 2
    assert report.parts[0].error_type is None
    assert report.parts[1].error_type == "no_subtitle"
    assert report.parts[2].error_type == "processing_failed"
    assert report.parts[0].transcript is not None
    assert report.parts[0].transcript.plain_text == "P1 字幕"
    assert report.parts[1].transcript is None
    assert report.parts[2].transcript is not None
    assert report.parts[2].transcript.plain_text == "P3 字幕"
    assert report.collection_summary_requested is True
    assert report.summary_path == tmp_path / "BV1DfrdByE2Hx" / "summary.md"
    assert report.summary_path.exists()
    assert (tmp_path / "BV1DfrdByE2Hx" / "P01_第一章.md").exists()
    assert not list((tmp_path / "BV1DfrdByE2Hx").glob("P02_*.md"))
    assert not list((tmp_path / "BV1DfrdByE2Hx").glob("P03_*.md"))

    part_notes = summary_input["part_notes"]
    assert len(part_notes) == 1
    assert part_notes[0][0:2] == (1, "第一章")
    # 证明合集总结使用的是已保存 Markdown 文件内容。
    assert part_notes[0][2] == "# 视频主题\nP1 字幕\n"
    assert summary_input["course_title"] == "Python 完整课程"
    assert any("P02 第二章" in item for item in summary_input["failed_parts"])
    assert any("P03 第三章" in item for item in summary_input["failed_parts"])
    assert any("已继续" in event for event in events)


def test_pipeline_only_processes_selected_part_and_keeps_page_number(
    tmp_path: Path,
) -> None:
    collection = _collection()
    fetched_urls: list[str] = []
    generated_subtitles: list[str] = []
    generated_modes: list[str | None] = []
    summary_parts: list[tuple[int, str, str]] = []
    summary_options: dict[str, object] = {}

    def fake_fetch(url: str, **_kwargs: object) -> VideoSubtitle:
        fetched_urls.append(url)
        return VideoSubtitle(
            bvid=collection.bvid,
            title="第三章",
            description="",
            transcript=_transcript("仅有 P3 字幕"),
        )

    def fake_notes(subtitle_text: str, **kwargs: str) -> str:
        generated_subtitles.append(subtitle_text)
        generated_modes.append(kwargs.get("mode"))
        return "# 视频主题\n第三章"

    def fake_summary(
        part_notes: list[tuple[int, str, str]],
        **kwargs: object,
    ) -> str:
        summary_parts.extend(part_notes)
        summary_options.update(kwargs)
        return "# 视频整体主题\n课程总结"

    report = process_multi_part_video(
        collection,
        output_root=tmp_path,
        selected_parts=(collection.parts[2],),
        note_mode="technical",
        subtitle_fetcher=fake_fetch,
        notes_generator=fake_notes,
        summary_generator=fake_summary,
    )

    output_dir = tmp_path / collection.bvid
    assert fetched_urls == ["https://example.test?p=3"]
    assert generated_subtitles == ["仅有 P3 字幕"]
    assert generated_modes == ["technical"]
    assert [result.page_number for result in report.parts] == [3]
    assert (output_dir / "P03_第三章.md").exists()
    assert not list(output_dir.glob("P01_*.md"))
    assert not list(output_dir.glob("P02_*.md"))
    assert [part[0] for part in summary_parts] == [3]
    assert "mode" not in summary_options


def test_multi_part_pipeline_passes_extra_instruction_only_to_part_notes(
    tmp_path: Path,
) -> None:
    collection = _collection()
    note_options: list[dict[str, str]] = []
    summary_options: dict[str, object] = {}

    def fake_fetch(url: str, **_kwargs: object) -> VideoSubtitle:
        page_number = int(url.rsplit("=", 1)[1])
        return VideoSubtitle(
            bvid=collection.bvid,
            title=f"第 {page_number} 章",
            description="",
            transcript=_transcript(f"P{page_number} 字幕"),
        )

    def fake_notes(_subtitle_text: str, **kwargs: str) -> str:
        note_options.append(kwargs)
        return "# 视频主题\n分P笔记"

    def fake_summary(
        _part_notes: list[tuple[int, str, str]],
        **kwargs: object,
    ) -> str:
        summary_options.update(kwargs)
        return "# 视频整体主题\n课程总结"

    process_multi_part_video(
        collection,
        output_root=tmp_path,
        selected_parts=(collection.parts[0], collection.parts[2]),
        note_mode="course",
        extra_instruction="重点说明章节关系。",
        subtitle_fetcher=fake_fetch,
        notes_generator=fake_notes,
        summary_generator=fake_summary,
    )

    assert [options["extra_instruction"] for options in note_options] == [
        "重点说明章节关系。",
        "重点说明章节关系。",
    ]
    assert [options["mode"] for options in note_options] == ["course", "course"]
    assert "extra_instruction" not in summary_options
    assert "mode" not in summary_options


def test_pipeline_defaults_to_processing_all_parts(tmp_path: Path) -> None:
    collection = _collection()
    fetched_urls: list[str] = []
    note_kwargs: list[dict[str, str]] = []

    def fake_fetch(url: str, **_kwargs: object) -> VideoSubtitle:
        fetched_urls.append(url)
        page_number = int(url.rsplit("=", 1)[1])
        return VideoSubtitle(
            bvid=collection.bvid,
            title=f"第 {page_number} 章",
            description="",
            transcript=_transcript(f"P{page_number} 字幕"),
        )

    def fake_notes(subtitle_text: str, **kwargs: str) -> str:
        note_kwargs.append(kwargs)
        return f"# 视频主题\n{subtitle_text}"

    report = process_multi_part_video(
        collection,
        output_root=tmp_path,
        subtitle_fetcher=fake_fetch,
        notes_generator=fake_notes,
        summary_generator=lambda *_args, **_kwargs: "# 视频整体主题\n课程总结",
    )

    assert fetched_urls == [part.url for part in collection.parts]
    assert [result.page_number for result in report.parts] == [1, 2, 3]
    assert all(result.error_type is None for result in report.parts)
    assert all("mode" not in kwargs for kwargs in note_kwargs)
    assert all("extra_instruction" not in kwargs for kwargs in note_kwargs)


def test_pipeline_skips_unrequested_collection_summary_and_keeps_part_notes(
    tmp_path: Path,
) -> None:
    collection = _collection()
    output_dir = tmp_path / collection.bvid
    output_dir.mkdir()
    historical_summary = output_dir / "summary.md"
    historical_summary.write_text("# 历史合集总结\n", encoding="utf-8")
    events: list[str] = []
    summary_calls: list[object] = []

    def fake_fetch(url: str, **_kwargs: object) -> VideoSubtitle:
        page_number = int(url.rsplit("=", 1)[1])
        return VideoSubtitle(
            bvid=collection.bvid,
            title=f"第 {page_number} 章",
            description="",
            transcript=_transcript(f"P{page_number} 字幕"),
        )

    def fake_summary(*args: object, **_kwargs: object) -> str:
        summary_calls.extend(args)
        return "# 不应生成的合集总结"

    report = process_multi_part_video(
        collection,
        output_root=tmp_path,
        generate_collection_summary=False,
        on_event=events.append,
        subtitle_fetcher=fake_fetch,
        notes_generator=lambda subtitle_text, **_kwargs: f"# 视频主题\n{subtitle_text}",
        summary_generator=fake_summary,
    )

    assert report.collection_summary_requested is False
    assert report.summary_path is None
    assert report.summary_error is None
    assert report.succeeded_count == 3
    assert all(result.error_type is None for result in report.parts)
    assert all(result.transcript is not None for result in report.parts)
    assert sorted(path.name for path in output_dir.glob("P*.md")) == [
        "P01_第_1_章.md",
        "P02_第_2_章.md",
        "P03_第_3_章.md",
    ]
    assert summary_calls == []
    assert historical_summary.read_text(encoding="utf-8") == "# 历史合集总结\n"
    assert "本次已按设置跳过合集总结" in events
