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
from prompt import resolve_note_template
from transcript import Transcript, TranscriptCue


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


def _transcript(text: str) -> Transcript:
    return Transcript(
        source="bilibili",
        language="zh-CN",
        cues=(TranscriptCue(0.0, 1.0, text),),
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


def test_estimated_call_count_for_single_and_multi_part() -> None:
    single = _collection(part_count=1)
    multi = _collection(part_count=3)

    assert web_service.estimate_deepseek_calls(single) == 1
    assert (
        web_service.estimate_deepseek_calls(
            single,
            generate_segmented_notes=True,
        )
        == 3
    )
    assert (
        web_service.estimate_deepseek_calls(
            multi,
            selected_parts=(multi.parts[1],),
            generate_collection_summary=True,
            generate_segmented_notes=True,
        )
        == 3
    )
    assert web_service.estimate_deepseek_calls(
        multi,
        selected_parts=(multi.parts[1],),
        generate_secondary_notes=True,
        generate_segmented_notes=True,
    ) == 4
    assert web_service.estimate_deepseek_calls(
        multi,
        selected_parts=multi.parts[:2],
        generate_collection_summary=True,
        generate_secondary_notes=True,
        generate_segmented_notes=True,
    ) == 3
    assert web_service.estimate_deepseek_calls(
        single,
        generate_secondary_notes=True,
        generate_segmented_notes=True,
    ) == 4


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
        transcript=_transcript("字幕"),
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
        asr_language="en",
        output_root=tmp_path,
        on_event=on_event,
    )

    assert received["video_info"] is collection
    assert received["output_root"] == tmp_path
    assert received["note_mode"] == "technical"
    assert received["extra_instruction"] == "重点解释原理。"
    assert received["cookies_from_browser"] == "chrome"
    assert received["asr_language"] == "en"
    assert received["on_event"] is on_event
    assert events == ["单P事件"]
    assert isinstance(result, web_service.WebGenerationResult)
    assert result.is_multi_part is False
    assert result.succeeded_count == 1
    assert result.parts[0].title == "真实视频标题"
    assert result.parts[0].markdown == "# 视频主题\n单P笔记\n"
    assert result.parts[0].filename == output_path.name
    assert result.parts[0].transcript is video.transcript


def test_generate_notes_defaults_web_asr_language_to_chinese(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=1)
    output_path = tmp_path / "BV1DfrdByE2Hx_study_notes.md"
    output_path.write_text("# 笔记\n", encoding="utf-8")
    video = VideoSubtitle(
        collection.bvid,
        "视频",
        "",
        _transcript("字幕"),
    )
    received: dict[str, object] = {}

    def fake_process(video_info, **kwargs):
        received.update(kwargs)
        return SinglePartReport(video, output_path)

    monkeypatch.setattr(web_service, "process_single_part_video", fake_process)

    web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        output_root=tmp_path,
    )

    assert received["asr_language"] == "zh"


def test_generate_notes_without_output_root_cleans_temporary_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = _collection(part_count=1)
    video = VideoSubtitle(
        collection.bvid,
        "视频",
        "",
        _transcript("字幕"),
    )
    received: dict[str, object] = {}

    def fake_process(video_info, **kwargs):
        output_root = kwargs["output_root"]
        assert isinstance(output_root, Path)
        received["output_root"] = output_root
        output_path = output_root / "temporary_note.md"
        output_path.write_text("# 临时笔记\n", encoding="utf-8")
        return SinglePartReport(video, output_path)

    monkeypatch.setattr(web_service, "process_single_part_video", fake_process)

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
    )

    temporary_root = received["output_root"]
    assert isinstance(temporary_root, Path)
    assert result.parts[0].markdown == "# 临时笔记\n"
    assert not temporary_root.exists()


def test_generate_notes_passes_segment_switch_and_reads_only_reported_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=1)
    overall_path = tmp_path / "BV1DfrdByE2Hx_study_notes.md"
    segmented_path = tmp_path / "BV1DfrdByE2Hx_segmented_notes.md"
    overall_path.write_text("# 总体笔记\n", encoding="utf-8")
    segmented_path.write_text("# 本次分段笔记\n", encoding="utf-8")
    video = VideoSubtitle(
        collection.bvid,
        "Python",
        "",
        _transcript("字幕"),
    )
    received: dict[str, object] = {}

    def fake_process(video_info, **kwargs):
        received["video_info"] = video_info
        received.update(kwargs)
        return SinglePartReport(
            video,
            overall_path,
            segmented_notes_requested=True,
            segmented_output_path=segmented_path,
        )

    monkeypatch.setattr(web_service, "process_single_part_video", fake_process)

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        generate_segmented_notes=True,
        output_root=tmp_path,
    )

    assert received["generate_segmented_notes"] is True
    assert result.segmented_notes_requested is True
    assert result.segmented_markdown == "# 本次分段笔记\n"
    assert result.segmented_filename == segmented_path.name
    assert result.segmented_error is None


def test_generate_notes_reads_secondary_reported_file_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=1)
    overall = tmp_path / "BV1DfrdByE2Hx_study_notes.md"
    secondary = tmp_path / "BV1DfrdByE2Hx_study_notes_B.md"
    overall.write_text("# 主总结\n", encoding="utf-8")
    secondary.write_text("# 第二总结\n", encoding="utf-8")
    video = VideoSubtitle(collection.bvid, "Python", "", _transcript("字幕"))
    secondary_template = resolve_note_template("technical")
    monkeypatch.setattr(
        web_service,
        "process_single_part_video",
        lambda *_args, **_kwargs: SinglePartReport(
            video,
            overall,
            secondary_output_path=secondary,
            secondary_template=secondary_template,
        ),
    )

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        secondary_note_template=secondary_template,
        output_root=tmp_path,
    )

    assert result.secondary_markdown == "# 第二总结\n"
    assert result.secondary_filename == secondary.name
    assert result.secondary_error is None


def test_single_part_result_exposes_viewer_contents_without_new_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=1)
    overall = tmp_path / "BV1DfrdByE2Hx_study_notes.md"
    segmented = tmp_path / "BV1DfrdByE2Hx_segmented_notes.md"
    overall.write_text("# 总体\n- 要点\n", encoding="utf-8")
    segmented.write_text("# 分段\n- 重点\n", encoding="utf-8")
    transcript = _transcript("字幕内容")
    video = VideoSubtitle(collection.bvid, "Python", "", transcript)
    template = resolve_note_template("technical")
    monkeypatch.setattr(
        web_service,
        "process_single_part_video",
        lambda *_args, **_kwargs: SinglePartReport(
            video,
            overall,
            segmented_notes_requested=True,
            segmented_output_path=segmented,
        ),
    )

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        generate_segmented_notes=True,
        note_template=template,
        output_root=tmp_path,
    )

    assert [content.content_id for content in result.viewer_contents] == [
        "overall-a",
        "segmented",
        "transcript",
    ]
    assert result.viewer_contents[0].template_name == template.name
    assert result.viewer_contents[-1].kind == "transcript"
    assert result.viewer_contents[-1].transcript_source == "bilibili"
    assert result.viewer_contents[-1].srt_text == transcript.to_srt()


def test_single_part_can_hide_transcript_from_viewer_without_changing_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=1)
    overall = tmp_path / "BV1DfrdByE2Hx_study_notes.md"
    overall.write_text("# 总体\n", encoding="utf-8")
    video = VideoSubtitle(collection.bvid, "Python", "", _transcript("字幕内容"))
    received: dict[str, object] = {}

    def fake_process(*_args, **kwargs):
        received.update(kwargs)
        return SinglePartReport(video, overall)

    monkeypatch.setattr(web_service, "process_single_part_video", fake_process)

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        include_transcript_in_viewer=False,
        output_root=tmp_path,
    )

    assert [content.content_id for content in result.viewer_contents] == ["overall-a"]
    assert "include_transcript_in_viewer" not in received


def test_historical_segmented_file_is_not_read_after_current_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=1)
    overall_path = tmp_path / "BV1DfrdByE2Hx_study_notes.md"
    historical_path = tmp_path / "BV1DfrdByE2Hx_segmented_notes.md"
    overall_path.write_text("# 总体笔记\n", encoding="utf-8")
    historical_path.write_text("# 历史分段笔记\n", encoding="utf-8")
    video = VideoSubtitle(collection.bvid, "Python", "", _transcript("字幕"))
    read_paths: list[Path] = []
    original_read_text = Path.read_text

    def tracking_read_text(path: Path, *args: object, **kwargs: object) -> str:
        read_paths.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracking_read_text)
    monkeypatch.setattr(
        web_service,
        "process_single_part_video",
        lambda *_args, **_kwargs: SinglePartReport(
            video,
            overall_path,
            segmented_notes_requested=True,
            segmented_error="本次规划失败",
        ),
    )

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        generate_segmented_notes=True,
        output_root=tmp_path,
    )

    assert result.parts[0].markdown == "# 总体笔记\n"
    assert result.segmented_markdown is None
    assert result.segmented_error == "本次规划失败"
    assert overall_path in read_paths
    assert historical_path not in read_paths


def test_generate_notes_dispatches_one_selected_part_to_full_single_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=3)
    selected_part = collection.parts[1]
    overall = tmp_path / "BV1DfrdByE2Hx_P02_study_notes.md"
    secondary = tmp_path / "BV1DfrdByE2Hx_P02_study_notes_B.md"
    segmented = tmp_path / "BV1DfrdByE2Hx_P02_segmented_notes.md"
    overall.write_text("# 总体 A\n", encoding="utf-8")
    secondary.write_text("# 总体 B\n", encoding="utf-8")
    segmented.write_text("# 分段\n", encoding="utf-8")
    transcript = _transcript("第二章字幕")
    video = VideoSubtitle(collection.bvid, "第二章", "", transcript)
    primary_template = resolve_note_template("course")
    secondary_template = resolve_note_template("technical")
    received: dict[str, object] = {}

    def fake_single(video_info, **kwargs):
        received["video_info"] = video_info
        received.update(kwargs)
        return SinglePartReport(
            video,
            overall,
            segmented_notes_requested=True,
            segmented_output_path=segmented,
            secondary_output_path=secondary,
            secondary_template=secondary_template,
        )

    monkeypatch.setattr(web_service, "process_single_part_video", fake_single)
    monkeypatch.setattr(
        web_service,
        "process_multi_part_video",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("单选分 P 不应进入批量流程")
        ),
    )

    result = web_service.generate_notes(
        collection,
        selected_parts=(selected_part,),
        note_template=primary_template,
        secondary_note_template=secondary_template,
        generate_segmented_notes=True,
        generate_collection_summary=True,
        output_root=tmp_path,
    )

    single_video_info = received["video_info"]
    assert isinstance(single_video_info, VideoCollection)
    assert single_video_info is not collection
    assert single_video_info.parts == (selected_part,)
    assert received["output_page_number"] == 2
    assert received["note_template"] is primary_template
    assert received["secondary_note_template"] is secondary_template
    assert received["generate_segmented_notes"] is True
    assert "generate_collection_summary" not in received
    assert result.is_multi_part is False
    assert result.parts[0].page_number == 2
    assert result.parts[0].filename == overall.name
    assert result.secondary_filename == secondary.name
    assert result.segmented_filename == segmented.name
    assert [content.filename for content in result.viewer_contents] == [
        "BV1DfrdByE2Hx_P02_study_notes.md",
        "BV1DfrdByE2Hx_P02_study_notes_B.md",
        "BV1DfrdByE2Hx_P02_segmented_notes.md",
        "BV1DfrdByE2Hx_P02_transcript.txt",
    ]
    assert result.viewer_contents[-1].srt_filename == (
        "BV1DfrdByE2Hx_P02_transcript.srt"
    )


def test_collection_single_selection_download_names_do_not_overlap_across_parts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=4)
    transcript = _transcript("字幕")
    primary_template = resolve_note_template("course")
    secondary_template = resolve_note_template("technical")

    def fake_single(video_info, **kwargs):
        page_number = kwargs["output_page_number"]
        prefix = f"{collection.bvid}_P{page_number:02d}"
        overall = tmp_path / f"{prefix}_study_notes.md"
        secondary = tmp_path / f"{prefix}_study_notes_B.md"
        segmented = tmp_path / f"{prefix}_segmented_notes.md"
        overall.write_text(f"# P{page_number} 总体 A\n", encoding="utf-8")
        secondary.write_text(f"# P{page_number} 总体 B\n", encoding="utf-8")
        segmented.write_text(f"# P{page_number} 分段\n", encoding="utf-8")
        return SinglePartReport(
            VideoSubtitle(
                collection.bvid,
                video_info.parts[0].title,
                "",
                transcript,
            ),
            overall,
            segmented_notes_requested=True,
            segmented_output_path=segmented,
            secondary_output_path=secondary,
            secondary_template=secondary_template,
        )

    monkeypatch.setattr(web_service, "process_single_part_video", fake_single)
    filename_sets: list[set[str]] = []
    for selected_part in (collection.parts[2], collection.parts[3]):
        result = web_service.generate_notes(
            collection,
            selected_parts=(selected_part,),
            note_template=primary_template,
            secondary_note_template=secondary_template,
            generate_segmented_notes=True,
            output_root=tmp_path,
        )
        filenames = {content.filename for content in result.viewer_contents}
        transcript_content = result.viewer_contents[-1]
        assert transcript_content.srt_filename is not None
        filenames.add(transcript_content.srt_filename)
        filename_sets.append(filenames)

    assert filename_sets[0] == {
        "BV1DfrdByE2Hx_P03_study_notes.md",
        "BV1DfrdByE2Hx_P03_study_notes_B.md",
        "BV1DfrdByE2Hx_P03_segmented_notes.md",
        "BV1DfrdByE2Hx_P03_transcript.txt",
        "BV1DfrdByE2Hx_P03_transcript.srt",
    }
    assert filename_sets[1] == {
        "BV1DfrdByE2Hx_P04_study_notes.md",
        "BV1DfrdByE2Hx_P04_study_notes_B.md",
        "BV1DfrdByE2Hx_P04_segmented_notes.md",
        "BV1DfrdByE2Hx_P04_transcript.txt",
        "BV1DfrdByE2Hx_P04_transcript.srt",
    }
    assert filename_sets[0].isdisjoint(filename_sets[1])


def test_generate_notes_dispatches_two_or_more_selected_parts_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=3)
    selected_parts = collection.parts[1:]
    received: dict[str, object] = {}
    events: list[str] = []
    on_event = events.append
    output_dir = tmp_path / collection.bvid
    output_dir.mkdir()
    part_path = output_dir / "P02_第_2_章.md"
    part_path.write_text("# 视频主题\n第二章\n", encoding="utf-8")
    summary_path = output_dir / "summary.md"
    summary_path.write_text("# 视频整体主题\n合集总结\n", encoding="utf-8")
    transcript = _transcript("第二章字幕")

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
                    transcript=transcript,
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
    assert result.parts[0].transcript is transcript
    assert result.summary_markdown == "# 视频整体主题\n合集总结\n"
    assert result.summary_filename == "summary.md"
    assert result.collection_summary_requested is True


def test_multi_part_never_receives_segmented_notes_switch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=2)
    received: dict[str, object] = {}

    def fake_process(video_info, **kwargs):
        received["video_info"] = video_info
        received.update(kwargs)
        return MultiPartReport(
            output_dir=tmp_path / collection.bvid,
            parts=(),
            summary_path=None,
            collection_summary_requested=False,
        )

    monkeypatch.setattr(web_service, "process_multi_part_video", fake_process)

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        generate_segmented_notes=True,
        output_root=tmp_path,
    )

    assert "generate_segmented_notes" not in received
    assert result.is_multi_part is True
    assert result.segmented_notes_requested is False


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
                transcript=_transcript("第三章字幕"),
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
    assert result.parts[2].transcript is not None
    assert result.parts[2].transcript.plain_text == "第三章字幕"
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
