"""Streamlit 页面与现有核心模块之间的轻量适配层。"""

from __future__ import annotations

from pathlib import Path

from bilibili import BilibiliError, VideoCollection, VideoPart, get_video_parts
from llm import LLMError
from pipeline import (
    MultiPartReport,
    SinglePartReport,
    process_multi_part_video,
    process_single_part_video,
)
from prompt import NoteMode, get_selectable_note_modes
from selection import PartSelectionError, select_video_parts


OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
GenerationReport = SinglePartReport | MultiPartReport


def load_video_info(video_input: str) -> VideoCollection:
    """调用现有 B 站解析能力，返回视频及全部分 P 信息。"""

    return get_video_parts(video_input)


def get_note_mode_options() -> tuple[NoteMode, ...]:
    """返回网页中允许用户选择的 v0.2.2 笔记模式。"""

    return get_selectable_note_modes()


def select_parts(
    video_info: VideoCollection,
    selection: str | None,
) -> tuple[VideoPart, ...]:
    """使用 ``selection.py`` 的统一规则选择网页要处理的分 P。"""

    return select_video_parts(video_info.parts, selection)


def generate_notes(
    video_info: VideoCollection,
    *,
    selected_parts: tuple[VideoPart, ...],
    note_mode: str | None = None,
    extra_instruction: str | None = None,
    output_root: Path = OUTPUT_DIR,
) -> GenerationReport:
    """把网页参数交给 pipeline，不在网页层复制字幕或模型调用逻辑。"""

    common_options = {
        "output_root": output_root,
        "note_mode": note_mode,
        "extra_instruction": extra_instruction,
    }
    if video_info.is_multi_part:
        return process_multi_part_video(
            video_info,
            selected_parts=selected_parts,
            **common_options,
        )
    return process_single_part_video(video_info, **common_options)
