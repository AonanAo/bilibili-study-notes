"""分 P 选择表达式的解析与筛选。

这个模块只处理用户选择规则，不负责命令行输入、字幕获取或文件保存。
因此命令行和未来的网页界面都可以复用同一套规则。
"""

from __future__ import annotations

from collections.abc import Sequence

from bilibili import VideoPart


class PartSelectionError(ValueError):
    """用户输入的分 P 选择表达式无效。"""


def _parse_selected_numbers(selection: str) -> set[int]:
    """把 ``1,3,5-8`` 展开成分 P 编号集合。"""

    selected_numbers: set[int] = set()

    for raw_item in selection.split(","):
        item = raw_item.strip()
        if not item:
            raise PartSelectionError("分 P 选择中存在空项，请使用例如 1,3,5-8 的格式。")

        # 纯数字表示单个分 P。
        if item.isascii() and item.isdigit():
            page_number = int(item)
            if page_number < 1:
                raise PartSelectionError("分 P 编号必须从 1 开始。")
            selected_numbers.add(page_number)
            continue

        # 一个连字符表示连续范围，例如 5-8。
        if item.count("-") == 1:
            start_text, end_text = (value.strip() for value in item.split("-", 1))
            if not (
                start_text.isascii()
                and start_text.isdigit()
                and end_text.isascii()
                and end_text.isdigit()
            ):
                raise PartSelectionError(
                    f"无法识别分 P 选择“{item}”，请使用例如 1,3,5-8 的格式。"
                )

            start = int(start_text)
            end = int(end_text)
            if start < 1 or end < 1:
                raise PartSelectionError("分 P 编号必须从 1 开始。")
            if start > end:
                raise PartSelectionError(
                    f"分 P 范围“{item}”的起始编号不能大于结束编号。"
                )
            selected_numbers.update(range(start, end + 1))
            continue

        raise PartSelectionError(
            f"无法识别分 P 选择“{item}”，请使用例如 1,3,5-8 的格式。"
        )

    return selected_numbers


def select_video_parts(
    parts: Sequence[VideoPart],
    selection: str | None = None,
) -> tuple[VideoPart, ...]:
    """根据表达式筛选分 P，并保持视频中的原始顺序。

    ``selection`` 为 ``None`` 或空白时返回全部分 P，以保持 v0.1 的
    默认行为。显式选择的编号会自动去重，并检查是否真实存在。
    """

    available_parts = tuple(parts)
    if selection is None or not selection.strip():
        return available_parts

    selected_numbers = _parse_selected_numbers(selection.strip())
    available_numbers = {part.page_number for part in available_parts}
    missing_numbers = sorted(selected_numbers - available_numbers)
    if missing_numbers:
        missing = ", ".join(f"P{number}" for number in missing_numbers)
        raise PartSelectionError(f"选择的分 P 不存在：{missing}。")

    # 不按用户输入顺序排列，而是按视频原始分 P 顺序返回。
    return tuple(
        part for part in available_parts if part.page_number in selected_numbers
    )
