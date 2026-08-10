from __future__ import annotations

from pathlib import Path

import pytest

import web_service
from bilibili import BilibiliFetchError, VideoCollection, VideoPart


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
    expected = object()
    received: dict[str, object] = {}

    def fake_process(video_info, **kwargs):
        received["video_info"] = video_info
        received.update(kwargs)
        return expected

    monkeypatch.setattr(web_service, "process_single_part_video", fake_process)

    result = web_service.generate_notes(
        collection,
        selected_parts=collection.parts,
        note_mode="technical",
        extra_instruction="重点解释原理。",
        output_root=tmp_path,
    )

    assert result is expected
    assert received == {
        "video_info": collection,
        "output_root": tmp_path,
        "note_mode": "technical",
        "extra_instruction": "重点解释原理。",
    }


def test_generate_notes_dispatches_selected_multi_parts_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _collection(part_count=3)
    selected_parts = (collection.parts[1],)
    expected = object()
    received: dict[str, object] = {}

    def fake_process(video_info, **kwargs):
        received["video_info"] = video_info
        received.update(kwargs)
        return expected

    monkeypatch.setattr(web_service, "process_multi_part_video", fake_process)

    result = web_service.generate_notes(
        collection,
        selected_parts=selected_parts,
        note_mode="course",
        extra_instruction="关注关键观点。",
        output_root=tmp_path,
    )

    assert result is expected
    assert received == {
        "video_info": collection,
        "selected_parts": selected_parts,
        "output_root": tmp_path,
        "note_mode": "course",
        "extra_instruction": "关注关键观点。",
    }
