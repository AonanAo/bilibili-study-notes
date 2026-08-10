"""Streamlit 页面与现有核心模块之间的轻量适配层。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from bilibili import (
    BilibiliError,
    NoSubtitleError,
    VideoCollection,
    VideoPart,
    get_video_parts,
)
from llm import LLMError
from pipeline import (
    MultiPartReport,
    PartErrorType,
    PartProcessingResult,
    SinglePartReport,
    process_multi_part_video,
    process_single_part_video,
)
from prompt import NoteMode, get_selectable_note_modes
from selection import PartSelectionError, select_video_parts


OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"


@dataclass(frozen=True)
class WebPartResult:
    """网页展示所需的单个分 P 结果。"""

    page_number: int
    title: str
    error_type: PartErrorType | None = None
    error: str | None = None
    markdown: str | None = None
    filename: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error_type is None and self.markdown is not None


@dataclass(frozen=True)
class WebGenerationResult:
    """网页展示单 P、多 P 和合集总结所需的统一结果。"""

    is_multi_part: bool
    parts: tuple[WebPartResult, ...]
    summary_markdown: str | None = None
    summary_filename: str | None = None
    summary_error: str | None = None

    @property
    def succeeded_count(self) -> int:
        return sum(part.succeeded for part in self.parts)

    @property
    def no_subtitle_count(self) -> int:
        return sum(part.error_type == "no_subtitle" for part in self.parts)

    @property
    def failed_count(self) -> int:
        return sum(part.error_type == "processing_failed" for part in self.parts)


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


def _read_single_part_report(report: SinglePartReport) -> WebPartResult:
    """读取单 P 报告中的 Markdown，并整理为网页结果。"""

    try:
        markdown = report.output_path.read_text(encoding="utf-8")
    except OSError as error:
        return WebPartResult(
            page_number=1,
            title=report.video.title,
            error_type="processing_failed",
            error=f"读取已生成笔记失败：{error}",
            filename=report.output_path.name,
        )
    return WebPartResult(
        page_number=1,
        title=report.video.title,
        markdown=markdown,
        filename=report.output_path.name,
    )


def _read_multi_part_result(result: PartProcessingResult) -> WebPartResult:
    """把 pipeline 的一个分 P 处理结果转换为网页结果。"""

    if not result.succeeded:
        return WebPartResult(
            page_number=result.page_number,
            title=result.title,
            error_type=result.error_type or "processing_failed",
            error=result.error,
            filename=result.output_path.name if result.output_path else None,
        )

    try:
        markdown = result.output_path.read_text(encoding="utf-8")
    except OSError as error:
        return WebPartResult(
            page_number=result.page_number,
            title=result.title,
            error_type="processing_failed",
            error=f"读取已生成笔记失败：{error}",
            filename=result.output_path.name,
        )
    return WebPartResult(
        page_number=result.page_number,
        title=result.title,
        markdown=markdown,
        filename=result.output_path.name,
    )


def _read_multi_part_report(report: MultiPartReport) -> WebGenerationResult:
    """读取多 P 报告中的分 P 笔记和合集总结。"""

    parts = tuple(_read_multi_part_result(result) for result in report.parts)
    summary_markdown = None
    summary_filename = None
    summary_error = report.summary_error

    if report.summary_path is not None:
        summary_filename = report.summary_path.name
        try:
            summary_markdown = report.summary_path.read_text(encoding="utf-8")
        except OSError as error:
            summary_error = f"读取 summary.md 失败：{error}"

    return WebGenerationResult(
        is_multi_part=True,
        parts=parts,
        summary_markdown=summary_markdown,
        summary_filename=summary_filename,
        summary_error=summary_error,
    )


def generate_notes(
    video_info: VideoCollection,
    *,
    selected_parts: tuple[VideoPart, ...],
    note_mode: str | None = None,
    extra_instruction: str | None = None,
    output_root: Path = OUTPUT_DIR,
    on_event: Callable[[str], None] | None = None,
) -> WebGenerationResult:
    """把网页参数交给 pipeline，不在网页层复制字幕或模型调用逻辑。"""

    common_options = {
        "output_root": output_root,
        "note_mode": note_mode,
        "extra_instruction": extra_instruction,
        "on_event": on_event,
    }
    if video_info.is_multi_part:
        report = process_multi_part_video(
            video_info,
            selected_parts=selected_parts,
            **common_options,
        )
        return _read_multi_part_report(report)

    part = video_info.parts[0]
    try:
        report = process_single_part_video(video_info, **common_options)
    except NoSubtitleError as error:
        return WebGenerationResult(
            is_multi_part=False,
            parts=(
                WebPartResult(
                    page_number=part.page_number,
                    title=error.video_title or part.title,
                    error_type="no_subtitle",
                    error=f"无字幕，无法生成笔记：{error}",
                ),
            ),
        )
    except (BilibiliError, LLMError, OSError) as error:
        return WebGenerationResult(
            is_multi_part=False,
            parts=(
                WebPartResult(
                    page_number=part.page_number,
                    title=getattr(error, "video_title", "") or part.title,
                    error_type="processing_failed",
                    error=str(error),
                ),
            ),
        )
    return WebGenerationResult(
        is_multi_part=False,
        parts=(_read_single_part_report(report),),
    )
