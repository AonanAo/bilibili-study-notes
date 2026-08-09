from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import main
import pytest
from bilibili import VideoCollection, VideoPart


def _multi_collection() -> VideoCollection:
    return VideoCollection(
        bvid="BV1DfrdByE2Hx",
        title="Python 多P课程",
        description="",
        parts=(
            VideoPart(1, "Python 入门", "https://example.test?p=1"),
            VideoPart(2, "函数", "https://example.test?p=2"),
            VideoPart(3, "类与对象", "https://example.test?p=3"),
        ),
    )


def _multi_report(tmp_path: Path, part_count: int) -> SimpleNamespace:
    return SimpleNamespace(
        succeeded_count=part_count,
        failed_count=0,
        summary_path=tmp_path / "BV1DfrdByE2Hx" / "summary.md",
        summary_error=None,
        output_dir=tmp_path / "BV1DfrdByE2Hx",
    )


def test_save_study_notes_creates_markdown_file(tmp_path: Path) -> None:
    output_path = main.save_study_notes("# 视频主题\n测试", "BV1DfrdByE2Hx", tmp_path)

    assert output_path == tmp_path / "BV1DfrdByE2Hx_study_notes.md"
    assert output_path.read_text(encoding="utf-8") == "# 视频主题\n测试\n"


def test_main_passes_subtitle_to_llm_and_saves_notes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    video = SimpleNamespace(
        bvid="BV1DfrdByE2Hx",
        title="Python 视频",
        description="Python 简介",
        subtitle_text="Python 字幕文本",
    )
    received: dict[str, str] = {}

    monkeypatch.setattr("sys.argv", ["main.py", "BV1DfrdByE2Hx"])
    monkeypatch.setattr(
        main,
        "get_video_parts",
        lambda *args, **kwargs: SimpleNamespace(is_multi_part=False),
    )
    monkeypatch.setattr(main, "fetch_video_subtitle", lambda *args, **kwargs: video)

    def fake_generate(subtitle_text: str, **kwargs: str) -> str:
        received["subtitle_text"] = subtitle_text
        received.update(kwargs)
        return "# 视频主题\n测试笔记"

    def fake_save(markdown: str, bvid: str) -> Path:
        received["markdown"] = markdown
        received["bvid"] = bvid
        return tmp_path / f"{bvid}_study_notes.md"

    monkeypatch.setattr(main, "generate_study_notes", fake_generate)
    monkeypatch.setattr(main, "save_study_notes", fake_save)

    assert main.main() == 0
    assert received == {
        "subtitle_text": "Python 字幕文本",
        "video_title": "Python 视频",
        "video_description": "Python 简介",
        "markdown": "# 视频主题\n测试笔记",
        "bvid": "BV1DfrdByE2Hx",
    }


def test_main_automatically_dispatches_multi_part_video(
    monkeypatch,
    tmp_path: Path,
) -> None:
    collection = _multi_collection()
    report = _multi_report(tmp_path, 3)
    received: dict[str, object] = {}

    monkeypatch.setattr("sys.argv", ["main.py", "BV1DfrdByE2Hx"])
    monkeypatch.setattr(main, "get_video_parts", lambda *args, **kwargs: collection)

    def fake_process(received_collection, **kwargs):
        received["collection"] = received_collection
        received.update(kwargs)
        return report

    monkeypatch.setattr(main, "process_multi_part_video", fake_process)

    assert main.main() == 0
    assert received["collection"] is collection
    assert received["output_root"] == main.OUTPUT_DIR
    assert received["on_event"] is print
    assert received["selected_parts"] == collection.parts
    assert received["note_mode"] is None


def test_main_passes_only_selected_parts_to_pipeline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    collection = _multi_collection()
    received: dict[str, object] = {}

    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "BV1DfrdByE2Hx", "--parts", "3"],
    )
    monkeypatch.setattr(main, "get_video_parts", lambda *args, **kwargs: collection)

    def fake_process(received_collection, **kwargs):
        received["collection"] = received_collection
        received.update(kwargs)
        return _multi_report(tmp_path, 1)

    monkeypatch.setattr(main, "process_multi_part_video", fake_process)

    assert main.main() == 0
    assert received["selected_parts"] == (collection.parts[2],)


def test_main_interactive_mode_prompts_for_part_selection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    collection = _multi_collection()
    answers = iter(["BV1DfrdByE2Hx", "1,3", ""])
    received: dict[str, object] = {}

    monkeypatch.setattr("sys.argv", ["main.py"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(main, "get_video_parts", lambda *args, **kwargs: collection)

    def fake_process(received_collection, **kwargs):
        received.update(kwargs)
        return _multi_report(tmp_path, 2)

    monkeypatch.setattr(main, "process_multi_part_video", fake_process)

    assert main.main() == 0
    assert received["selected_parts"] == (collection.parts[0], collection.parts[2])
    assert received["note_mode"] is None


def test_main_invalid_part_selection_does_not_start_pipeline(
    monkeypatch,
    capsys,
) -> None:
    collection = _multi_collection()
    pipeline_started = False

    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "BV1DfrdByE2Hx", "--parts", "1,a"],
    )
    monkeypatch.setattr(main, "get_video_parts", lambda *args, **kwargs: collection)

    def fake_process(*_args, **_kwargs):
        nonlocal pipeline_started
        pipeline_started = True

    monkeypatch.setattr(main, "process_multi_part_video", fake_process)

    assert main.main() == 1
    assert pipeline_started is False
    assert "无法识别" in capsys.readouterr().err


def test_main_displays_real_part_titles(monkeypatch, tmp_path: Path, capsys) -> None:
    collection = _multi_collection()

    monkeypatch.setattr("sys.argv", ["main.py", "BV1DfrdByE2Hx"])
    monkeypatch.setattr(main, "get_video_parts", lambda *args, **kwargs: collection)
    monkeypatch.setattr(
        main,
        "process_multi_part_video",
        lambda *_args, **_kwargs: _multi_report(tmp_path, 3),
    )

    assert main.main() == 0
    output = capsys.readouterr().out
    assert "P1 Python 入门" in output
    assert "P2 函数" in output
    assert "P3 类与对象" in output


def test_main_passes_cli_mode_to_multi_part_pipeline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    collection = _multi_collection()
    received: dict[str, object] = {}

    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "BV1DfrdByE2Hx", "--parts", "2", "--mode", "technical"],
    )
    monkeypatch.setattr(main, "get_video_parts", lambda *args, **kwargs: collection)

    def fake_process(_collection, **kwargs):
        received.update(kwargs)
        return _multi_report(tmp_path, 1)

    monkeypatch.setattr(main, "process_multi_part_video", fake_process)

    assert main.main() == 0
    assert received["selected_parts"] == (collection.parts[1],)
    assert received["note_mode"] == "technical"


def test_main_passes_cli_mode_to_single_part_llm(
    monkeypatch,
    tmp_path: Path,
) -> None:
    video = SimpleNamespace(
        bvid="BV1DfrdByE2Hx",
        title="Python 视频",
        description="简介",
        subtitle_text="字幕",
    )
    received: dict[str, object] = {}

    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "BV1DfrdByE2Hx", "--mode", "course"],
    )
    monkeypatch.setattr(
        main,
        "get_video_parts",
        lambda *args, **kwargs: SimpleNamespace(is_multi_part=False),
    )
    monkeypatch.setattr(main, "fetch_video_subtitle", lambda *args, **kwargs: video)

    def fake_generate(subtitle_text: str, **kwargs: str) -> str:
        received["subtitle_text"] = subtitle_text
        received.update(kwargs)
        return "# 视频主题\n笔记"

    monkeypatch.setattr(main, "generate_study_notes", fake_generate)
    monkeypatch.setattr(
        main,
        "save_study_notes",
        lambda *_args, **_kwargs: tmp_path / "notes.md",
    )

    assert main.main() == 0
    assert received["mode"] == "course"


def test_main_interactive_mode_accepts_number(
    monkeypatch,
    tmp_path: Path,
) -> None:
    collection = _multi_collection()
    answers = iter(["BV1DfrdByE2Hx", "3", "1"])
    received: dict[str, object] = {}

    monkeypatch.setattr("sys.argv", ["main.py"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(main, "get_video_parts", lambda *args, **kwargs: collection)

    def fake_process(_collection, **kwargs):
        received.update(kwargs)
        return _multi_report(tmp_path, 1)

    monkeypatch.setattr(main, "process_multi_part_video", fake_process)

    assert main.main() == 0
    assert received["selected_parts"] == (collection.parts[2],)
    assert received["note_mode"] == "technical"


def test_parser_rejects_academic_mode() -> None:
    with pytest.raises(SystemExit):
        main.build_parser().parse_args(["BV1DfrdByE2Hx", "--mode", "academic"])
