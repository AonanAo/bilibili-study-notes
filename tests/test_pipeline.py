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

