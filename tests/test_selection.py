from __future__ import annotations

import pytest

from bilibili import VideoPart
from selection import PartSelectionError, select_video_parts


def _parts(count: int = 8) -> tuple[VideoPart, ...]:
    return tuple(
        VideoPart(
            page_number=number,
            title=f"第 {number} 分P",
            url=f"https://example.test/video?p={number}",
        )
        for number in range(1, count + 1)
    )


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ("3", [3]),
        ("1,3,5", [1, 3, 5]),
        ("2-5", [2, 3, 4, 5]),
        ("1,3,5-8", [1, 3, 5, 6, 7, 8]),
        ("1,3,3,1-3", [1, 2, 3]),
        ("8,2,5", [2, 5, 8]),
        (" 1, 3, 5 - 6 ", [1, 3, 5, 6]),
    ],
)
def test_select_video_parts_parses_supported_expressions(
    selection: str,
    expected: list[int],
) -> None:
    selected = select_video_parts(_parts(), selection)

    assert [part.page_number for part in selected] == expected


@pytest.mark.parametrize("selection", [None, "", "   "])
def test_select_video_parts_defaults_to_all_parts(selection: str | None) -> None:
    parts = _parts(3)

    assert select_video_parts(parts, selection) == parts


@pytest.mark.parametrize(
    ("selection", "message"),
    [
        ("1,a,5", "无法识别"),
        ("1,,5", "空项"),
        ("5-3", "起始编号不能大于"),
        ("0", "必须从 1 开始"),
        ("-1", "无法识别"),
        ("1-", "无法识别"),
        ("1-2-3", "无法识别"),
    ],
)
def test_select_video_parts_rejects_invalid_expressions(
    selection: str,
    message: str,
) -> None:
    with pytest.raises(PartSelectionError, match=message):
        select_video_parts(_parts(), selection)


def test_select_video_parts_rejects_missing_parts() -> None:
    with pytest.raises(PartSelectionError, match=r"P9.*P10"):
        select_video_parts(_parts(), "2,9-10")
