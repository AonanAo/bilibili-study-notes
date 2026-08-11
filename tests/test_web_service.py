from __future__ import annotations

from pathlib import Path

import pytest

import web_service
from bilibili import (
    BilibiliFetchError,
    NoSubtitleError,
    SubtitleLoginRequiredError,
    VideoCollection,
    VideoPart,
    VideoSubtitle,
)
from llm import LLMError
from pipeline import MultiPartReport, PartProcessingResult, SinglePartReport


def _collection(*, part_count: int) -> VideoCollection:
    return VideoCollection(
        bvid="BV1DfrdByE2Hx",
        title="Python 课程",
        description="课程简介",
        parts=tuple(
            VideoPart(
                page_number=number,
                title=f"第 {number} 章",
                url=f"https://example.test/video?p={number}",
            )
            for number in range(1, part_count + 1)
        ),
    )


@pytest.mark.parametrize(
    "video_input",
    [
        "BV1DfrdByE2Hx",
        "https://www.bilibili.com/video/BV1DfrdByE2Hx",
        "https://www.bilibili.com/video/BV1DfrdByE2Hx/?spm_id_from=xxx",
    ],
)
def test_load_video_info_passes_input_to_existing_parser(
    monkeypatch: pytest.MonkeyPatch,
    video_input: str,
) -> None:
    expected = _collection(part_count=1)
    received: list[str] = []

    def fake_parser(value: str) -> VideoCollection:
        received.append(value)
        return expected

    monkeypatch.setattr(web_service, "get_video_parts", fake_parser)

    result = web_service.load_video_info(video_input)

    assert result is expected
    assert received == [video_input]


def test_load_video_info_preserves_single_part_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _collection(part_count=1)
    monkeypatch.setattr(web_service, "get_video_parts", lambda _value: expected)

    result = web_service.load_video_info("BV1DfrdByE2Hx")

    assert result.bvid == "BV1DfrdByE2Hx"
    assert result.title == "Python 课程"
    assert result.description == "课程简介"
    assert result.is_multi_part is False
    assert result.parts[0].page_number == 1


def test_load_video_info_passes_selected_browser_to_existing_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _collection(part_count=1)
    received: dict[str, object] = {}

    def fake_parser(value: str, **kwargs) -> VideoCollection:
        received["value"] = value
        received.update(kwargs)
        return expected

    monkeypatch.setattr(web_service, "get_video_parts", fake_parser)

    result = web_service.load_video_info(
        "BV1DfrdByE2Hx",
        cookies_from_browser="chrome",
    )

    assert result is expected
    assert received == {
        "value": "BV1DfrdByE2Hx",
        "cookies_from_browser": "chrome",
    }


def test_load_video_info_preserves_multi_part_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _collection(part_count=3)
    monkeypatch.setattr(web_service, "get_video_parts", lambda _value: expected)

    result = web_service.load_video_info("BV1DfrdByE2Hx")

    assert result.is_multi_part is True
    assert [part.page_number for part in result.parts] == [1, 2, 3]
    assert [part.title for part in result.parts] == ["第 1 章", "第 2 章", "第 3 章"]
    assert result.parts[2].url.endswith("?p=3")


def test_load_video_info_preserves_parser_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = BilibiliFetchError("视频解析失败")

    def fake_parser(_value: str) -> VideoCollection:
        raise error

    monkeypatch.setattr(web_service, "get_video_parts", fake_parser)

    with pytest.raises(BilibiliFetchError) as raised:
        web_service.load_video_info("BV1DfrdByE2Hx")

    assert raised.value is error


def test_select_parts_reuses_selection_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = _collection(part_count=3)
    expected = (collection.parts[0], collection.parts[2])
    received: dict[str, object] = {}

    def fake_select(parts, selection):
        received["parts"] = parts
        received["selection"] = selection
        return expected

    monkeypatch.setattr(web_service, "select_video_parts", fake_select)

    result = web_service.select_parts(collection, "1,3")

    assert result is expected
    assert received == {"parts": collection.parts, "selection": "1,3"}


def test_note_mode_options_reuse_prompt_registry() -> None:
    modes = web_service.get_note_mode_options()

    assert [mode.key for mode in modes] == ["technical", "course"]


def test_generate_notes_dispatches_single_part_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=1)
    received: dict[str, object] = {}
    events: list[str] = []
    on_event = events.append
    output_path = tmp_path / "BV1DfrdByE2Hx_study_notes.md"
    output_path.write_text("# 视频主题\n单P笔记\n", encoding="utf-8")
    video = VideoSubtitle(
        bvid=collection.bvid,
        title="真实视频标题",
        description="真实简介",
        subtitle_language="zh-CN",
        subtitle_text="字幕",
    )

    def fake_process(video_info, **kwargs):
        received["video_info"] = video_info
        received.update(kwargs)
        kwargs["on_event"]("单P事件")
        return SinglePartReport(video=video, output_path=output_path)

    monkeypatch.setattr(web_service, "process_single_part_video", fake_process)

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        note_mode="technical",
        extra_instruction="重点解释原理。",
        cookies_from_browser="chrome",
        output_root=tmp_path,
        on_event=on_event,
    )

    assert received["video_info"] is collection
    assert received["output_root"] == tmp_path
    assert received["note_mode"] == "technical"
    assert received["extra_instruction"] == "重点解释原理。"
    assert received["cookies_from_browser"] == "chrome"
    assert received["on_event"] is on_event
    assert events == ["单P事件"]
    assert isinstance(result, web_service.WebGenerationResult)
    assert result.is_multi_part is False
    assert result.succeeded_count == 1
    assert result.parts[0].title == "真实视频标题"
    assert result.parts[0].markdown == "# 视频主题\n单P笔记\n"
    assert result.parts[0].filename == output_path.name


def test_generate_notes_dispatches_selected_multi_parts_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=3)
    selected_parts = (collection.parts[1],)
    received: dict[str, object] = {}
    events: list[str] = []
    on_event = events.append
    output_dir = tmp_path / collection.bvid
    output_dir.mkdir()
    part_path = output_dir / "P02_第_2_章.md"
    part_path.write_text("# 视频主题\n第二章\n", encoding="utf-8")
    summary_path = output_dir / "summary.md"
    summary_path.write_text("# 视频整体主题\n合集总结\n", encoding="utf-8")

    def fake_process(video_info, **kwargs):
        received["video_info"] = video_info
        received.update(kwargs)
        kwargs["on_event"]("多P事件")
        return MultiPartReport(
            output_dir=output_dir,
            parts=(
                PartProcessingResult(
                    page_number=2,
                    title="第 2 章",
                    output_path=part_path,
                ),
            ),
            summary_path=summary_path,
        )

    monkeypatch.setattr(web_service, "process_multi_part_video", fake_process)

    result = web_service.generate_notes(
        collection,
        selected_parts=selected_parts,
        note_mode="course",
        extra_instruction="关注关键观点。",
        generate_collection_summary=True,
        cookies_from_browser="firefox",
        output_root=tmp_path,
        on_event=on_event,
    )

    assert received["video_info"] is collection
    assert received["selected_parts"] == selected_parts
    assert received["output_root"] == tmp_path
    assert received["note_mode"] == "course"
    assert received["extra_instruction"] == "关注关键观点。"
    assert received["generate_collection_summary"] is True
    assert received["cookies_from_browser"] == "firefox"
    assert received["on_event"] is on_event
    assert events == ["多P事件"]
    assert result.is_multi_part is True
    assert result.succeeded_count == 1
    assert result.parts[0].markdown == "# 视频主题\n第二章\n"
    assert result.parts[0].filename == part_path.name
    assert result.summary_markdown == "# 视频整体主题\n合集总结\n"
    assert result.summary_filename == "summary.md"
    assert result.collection_summary_requested is True


def test_generate_notes_skips_historical_summary_when_not_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=2)
    output_dir = tmp_path / collection.bvid
    output_dir.mkdir()
    part_path = output_dir / "P01_第一章.md"
    part_path.write_text("# 视频主题\n第一章\n", encoding="utf-8")
    historical_summary = output_dir / "summary.md"
    historical_summary.write_text("# 历史合集总结\n", encoding="utf-8")
    received: dict[str, object] = {}
    read_paths: list[Path] = []
    original_read_text = Path.read_text

    def tracking_read_text(path: Path, *args: object, **kwargs: object) -> str:
        read_paths.append(path)
        return original_read_text(path, *args, **kwargs)

    def fake_process(video_info, **kwargs):
        received["video_info"] = video_info
        received.update(kwargs)
        return MultiPartReport(
            output_dir=output_dir,
            parts=(PartProcessingResult(1, "第一章", output_path=part_path),),
            summary_path=historical_summary,
            collection_summary_requested=False,
            summary_error="不应展示的历史错误",
        )

    monkeypatch.setattr(Path, "read_text", tracking_read_text)
    monkeypatch.setattr(web_service, "process_multi_part_video", fake_process)

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        output_root=tmp_path,
    )

    assert received["generate_collection_summary"] is False
    assert result.collection_summary_requested is False
    assert result.parts[0].markdown == "# 视频主题\n第一章\n"
    assert result.summary_markdown is None
    assert result.summary_filename is None
    assert result.summary_error is None
    assert part_path in read_paths
    assert historical_summary not in read_paths


def test_generate_notes_converts_single_part_no_subtitle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=1)

    def fake_process(*_args, **_kwargs):
        raise NoSubtitleError("该视频没有字幕", video_title="真实标题")

    monkeypatch.setattr(web_service, "process_single_part_video", fake_process)

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        output_root=tmp_path,
    )

    assert result.succeeded_count == 0
    assert result.no_subtitle_count == 1
    assert result.failed_count == 0
    assert result.parts[0].title == "真实标题"
    assert result.parts[0].error_type == "no_subtitle"
    assert "没有字幕" in result.parts[0].error


def test_generate_notes_converts_single_part_processing_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=1)

    def fake_process(*_args, **_kwargs):
        raise LLMError("模型生成失败")

    monkeypatch.setattr(web_service, "process_single_part_video", fake_process)

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        output_root=tmp_path,
    )

    assert result.succeeded_count == 0
    assert result.no_subtitle_count == 0
    assert result.failed_count == 1
    assert result.parts[0].error_type == "processing_failed"
    assert result.parts[0].error == "模型生成失败"


def test_generate_notes_converts_login_failure_to_web_instruction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=1)

    def fake_process(*_args, **_kwargs):
        raise SubtitleLoginRequiredError(
            "B 站要求登录后才能读取该视频字幕。"
            "请使用 --cookies-from-browser 指定一个已登录 B 站的浏览器。"
        )

    monkeypatch.setattr(web_service, "process_single_part_video", fake_process)

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        cookies_from_browser="chrome",
        output_root=tmp_path,
    )

    part = result.parts[0]
    assert part.error_type == "processing_failed"
    assert "B站登录浏览器" in part.error
    assert "--cookies-from-browser" not in part.error


def test_generate_notes_converts_multi_part_statuses_and_summary_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=3)
    output_dir = tmp_path / collection.bvid
    output_dir.mkdir()
    success_path = output_dir / "P01_第一章.md"
    success_path.write_text("# 视频主题\n第一章\n", encoding="utf-8")

    report = MultiPartReport(
        output_dir=output_dir,
        parts=(
            PartProcessingResult(1, "第一章", output_path=success_path),
            PartProcessingResult(
                2,
                "第二章",
                error="无字幕，已跳过",
                error_type="no_subtitle",
            ),
            PartProcessingResult(
                3,
                "第三章",
                error="模型生成失败",
                error_type="processing_failed",
            ),
        ),
        summary_path=None,
        summary_error="课程总结生成失败",
    )
    monkeypatch.setattr(
        web_service,
        "process_multi_part_video",
        lambda *_args, **_kwargs: report,
    )

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        generate_collection_summary=True,
        output_root=tmp_path,
    )

    assert result.succeeded_count == 1
    assert result.no_subtitle_count == 1
    assert result.failed_count == 1
    assert result.parts[0].markdown == "# 视频主题\n第一章\n"
    assert result.parts[1].error_type == "no_subtitle"
    assert result.parts[2].error_type == "processing_failed"
    assert result.summary_markdown is None
    assert result.summary_error == "课程总结生成失败"
    assert result.collection_summary_requested is True


def test_generate_notes_converts_multi_part_login_failure_to_web_instruction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=2)
    report = MultiPartReport(
        output_dir=tmp_path / collection.bvid,
        parts=(
            PartProcessingResult(
                1,
                "第一章",
                error=(
                    "B 站要求登录后才能读取该视频字幕。"
                    "请使用 --cookies-from-browser 指定浏览器。"
                ),
                error_type="processing_failed",
            ),
        ),
        summary_path=None,
    )
    monkeypatch.setattr(
        web_service,
        "process_multi_part_video",
        lambda *_args, **_kwargs: report,
    )

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        output_root=tmp_path,
    )

    assert "B站登录浏览器" in result.parts[0].error
    assert "--cookies-from-browser" not in result.parts[0].error


def test_generate_notes_isolates_markdown_read_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=2)
    output_dir = tmp_path / collection.bvid
    missing_part_path = output_dir / "P01_不存在.md"
    missing_summary_path = output_dir / "summary.md"
    report = MultiPartReport(
        output_dir=output_dir,
        parts=(
            PartProcessingResult(
                page_number=1,
                title="第一章",
                output_path=missing_part_path,
            ),
        ),
        summary_path=missing_summary_path,
    )
    monkeypatch.setattr(
        web_service,
        "process_multi_part_video",
        lambda *_args, **_kwargs: report,
    )

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        output_root=tmp_path,
    )

    assert result.parts[0].error_type == "processing_failed"
    assert "读取已生成笔记失败" in result.parts[0].error
    assert result.parts[0].filename == missing_part_path.name
    assert result.summary_markdown is None
    assert result.summary_filename == "summary.md"
    assert "读取 summary.md 失败" in result.summary_error
