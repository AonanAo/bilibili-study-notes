from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import main


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
    collection = SimpleNamespace(
        is_multi_part=True,
        bvid="BV1DfrdByE2Hx",
        title="Python 多P课程",
        parts=[SimpleNamespace(), SimpleNamespace()],
    )
    report = SimpleNamespace(
        succeeded_count=2,
        failed_count=0,
        summary_path=tmp_path / "BV1DfrdByE2Hx" / "summary.md",
        summary_error=None,
        output_dir=tmp_path / "BV1DfrdByE2Hx",
    )
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
