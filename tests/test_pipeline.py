from __future__ import annotations

from pathlib import Path

from bilibili import NoSubtitleError, VideoCollection, VideoPart, VideoSubtitle
from llm import LLMError
from pipeline import (
    process_multi_part_video,
    process_single_part_video,
    safe_filename,
    save_part_notes,
)


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
        subtitle_language="zh-CN",
        subtitle_text="字幕正文",
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
    assert report.output_path == tmp_path / "BV1DfrdByE2Hx_study_notes.md"
    assert report.output_path.read_text(encoding="utf-8") == "# 视频主题\n单P笔记\n"


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
            subtitle_language="zh-CN",
            subtitle_text=f"P{page} 字幕",
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
            subtitle_language="zh-CN",
            subtitle_text="仅有 P3 字幕",
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
            subtitle_language="zh-CN",
            subtitle_text=f"P{page_number} 字幕",
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
            subtitle_language="zh-CN",
            subtitle_text=f"P{page_number} 字幕",
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
            subtitle_language="zh-CN",
            subtitle_text=f"P{page_number} 字幕",
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
    assert sorted(path.name for path in output_dir.glob("P*.md")) == [
        "P01_第_1_章.md",
        "P02_第_2_章.md",
        "P03_第_3_章.md",
    ]
    assert summary_calls == []
    assert historical_summary.read_text(encoding="utf-8") == "# 历史合集总结\n"
    assert "本次已按设置跳过合集总结" in events
