from __future__ import annotations

from pathlib import Path

from bilibili import NoSubtitleError, VideoCollection, VideoPart, VideoSubtitle
from llm import LLMError
from pipeline import process_multi_part_video, safe_filename, save_part_notes


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
    assert all("mode" not in kwargs for kwargs in note_kwargs)
