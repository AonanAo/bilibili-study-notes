"""Streamlit 页面与现有核心模块之间的轻量适配层。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from bilibili import (
    BilibiliError,
    NoSubtitleError,
    SubtitleLoginRequiredError,
    VideoCollection,
    VideoPart,
    get_video_parts,
)
from llm import LLMError
from note_viewer import ViewerContent, validate_viewer_contents
from pipeline import (
    MultiPartReport,
    PartErrorType,
    PartProcessingResult,
    SinglePartReport,
    process_multi_part_video,
    process_single_part_video,
)
from prompt import (
    NoteMode,
    NoteModeError,
    NoteTemplate,
    ResolvedNoteTemplate,
    get_note_template_options,
    get_all_note_template_options,
    get_selectable_note_modes,
    resolve_note_template,
    NOTE_SECTION_LIBRARY,
)
from selection import PartSelectionError, select_video_parts
from transcript import Transcript


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
    transcript: Transcript | None = None

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
    collection_summary_requested: bool = False
    segmented_notes_requested: bool = False
    segmented_markdown: str | None = None
    segmented_filename: str | None = None
    segmented_error: str | None = None
    template: ResolvedNoteTemplate | None = None
    secondary_markdown: str | None = None
    secondary_filename: str | None = None
    secondary_error: str | None = None
    secondary_template: ResolvedNoteTemplate | None = None
    viewer_contents: tuple[ViewerContent, ...] = ()

    @property
    def succeeded_count(self) -> int:
        return sum(part.succeeded for part in self.parts)

    @property
    def no_subtitle_count(self) -> int:
        return sum(part.error_type == "no_subtitle" for part in self.parts)

    @property
    def failed_count(self) -> int:
        return sum(part.error_type == "processing_failed" for part in self.parts)


def load_video_info(
    video_input: str,
    *,
    cookies_from_browser: str | None = None,
) -> VideoCollection:
    """调用现有 B 站解析能力，返回视频及全部分 P 信息。"""

    if cookies_from_browser is not None:
        return get_video_parts(
            video_input,
            cookies_from_browser=cookies_from_browser,
        )
    return get_video_parts(video_input)


def get_note_mode_options() -> tuple[NoteMode, ...]:
    """返回网页中允许用户选择的 v0.2.2 笔记模式。"""

    return get_selectable_note_modes()


def get_note_template_options_for_web() -> tuple[NoteTemplate, ...]:
    """返回网页可配置的总体笔记预设。"""

    return get_all_note_template_options()


def select_parts(
    video_info: VideoCollection,
    selection: str | None,
) -> tuple[VideoPart, ...]:
    """使用 ``selection.py`` 的统一规则选择网页要处理的分 P。"""

    return select_video_parts(video_info.parts, selection)


def estimate_deepseek_calls(
    video_info: VideoCollection,
    *,
    selected_parts: tuple[VideoPart, ...] | None = None,
    generate_collection_summary: bool = False,
    generate_segmented_notes: bool = False,
    generate_secondary_notes: bool = False,
) -> int:
    """返回提交前应展示的最多 DeepSeek 逻辑调用次数。"""

    parts = selected_parts if selected_parts is not None else video_info.parts
    if len(parts) == 1:
        return 1 + int(generate_secondary_notes) + (2 if generate_segmented_notes else 0)
    return len(parts) + int(generate_collection_summary)


def _web_error_message(error: str | None) -> str | None:
    """把要求使用 CLI 参数的登录错误转换为网页操作提示。"""

    if error is None:
        return None
    if "--cookies-from-browser" in error or "要求登录后才能读取" in error:
        return (
            "该视频字幕需要登录。请确认页面中的“B站登录浏览器”已选择"
            "正确的已登录浏览器；必要时关闭该浏览器后重试。"
        )
    return error


def _read_single_part_report(
    report: SinglePartReport,
    *,
    page_number: int,
) -> WebPartResult:
    """读取单 P 报告中的 Markdown，并整理为网页结果。"""

    try:
        markdown = report.output_path.read_text(encoding="utf-8")
    except OSError as error:
        return WebPartResult(
            page_number=page_number,
            title=report.video.title,
            error_type="processing_failed",
            error=f"读取已生成笔记失败：{error}",
            filename=report.output_path.name,
            transcript=report.video.transcript,
        )
    return WebPartResult(
        page_number=page_number,
        title=report.video.title,
        markdown=markdown,
        filename=report.output_path.name,
        transcript=report.video.transcript,
    )


def _read_segmented_report(
    report: SinglePartReport,
) -> tuple[str | None, str | None, str | None]:
    """只读取本次报告明确产出的分段文件，绝不探测历史文件。"""

    if not report.segmented_notes_requested:
        return None, None, None
    if report.segmented_output_path is None:
        return None, None, report.segmented_error or "分段笔记未生成。"
    try:
        markdown = report.segmented_output_path.read_text(encoding="utf-8")
    except OSError as error:
        return (
            None,
            report.segmented_output_path.name,
            f"读取已生成分段笔记失败：{error}",
        )
    return markdown, report.segmented_output_path.name, report.segmented_error


def _read_secondary_report(
    report: SinglePartReport,
) -> tuple[str | None, str | None, str | None]:
    if report.secondary_output_path is None:
        return None, None, report.secondary_error
    try:
        return (
            report.secondary_output_path.read_text(encoding="utf-8"),
            report.secondary_output_path.name,
            report.secondary_error,
        )
    except OSError as error:
        return None, report.secondary_output_path.name, f"读取第二份总体笔记失败：{error}"


def _read_multi_part_result(result: PartProcessingResult) -> WebPartResult:
    """把 pipeline 的一个分 P 处理结果转换为网页结果。"""

    if not result.succeeded:
        return WebPartResult(
            page_number=result.page_number,
            title=result.title,
            error_type=result.error_type or "processing_failed",
            error=_web_error_message(result.error),
            filename=result.output_path.name if result.output_path else None,
            transcript=result.transcript,
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
            transcript=result.transcript,
        )
    return WebPartResult(
        page_number=result.page_number,
        title=result.title,
        markdown=markdown,
        filename=result.output_path.name,
        transcript=result.transcript,
    )


def _read_multi_part_report(
    report: MultiPartReport,
    *,
    include_transcript_in_viewer: bool = True,
) -> WebGenerationResult:
    """读取多 P 报告中的分 P 笔记和合集总结。"""

    parts = tuple(_read_multi_part_result(result) for result in report.parts)
    summary_markdown = None
    summary_filename = None
    summary_error = (
        report.summary_error if report.collection_summary_requested else None
    )

    if report.collection_summary_requested and report.summary_path is not None:
        summary_filename = report.summary_path.name
        try:
            summary_markdown = report.summary_path.read_text(encoding="utf-8")
        except OSError as error:
            summary_error = f"读取 summary.md 失败：{error}"

    viewer_contents: list[ViewerContent] = []
    for part in parts:
        if part.succeeded and part.markdown is not None and part.filename is not None:
            viewer_contents.append(
                ViewerContent(
                    content_id=f"part-{part.page_number}",
                    label=f"P{part.page_number} 总体笔记",
                    kind="part",
                    text=part.markdown,
                    filename=part.filename,
                    template_name=None,
                )
            )
        if include_transcript_in_viewer and part.transcript is not None:
            viewer_contents.append(
                ViewerContent(
                    content_id=f"part-{part.page_number}-transcript",
                    label=f"P{part.page_number} 原始字幕（B站）",
                    kind="transcript",
                    text=part.transcript.plain_text,
                    filename=f"P{part.page_number}_transcript.txt",
                    download_mime="text/plain",
                    transcript_source=part.transcript.source,
                    srt_text=part.transcript.to_srt(),
                    srt_filename=f"P{part.page_number}_transcript.srt",
                )
            )
    if summary_markdown is not None and summary_filename is not None:
        viewer_contents.append(
            ViewerContent(
                content_id="collection-summary",
                label="合集总结",
                kind="overall",
                text=summary_markdown,
                filename=summary_filename,
            )
        )

    return WebGenerationResult(
        is_multi_part=True,
        parts=parts,
        summary_markdown=summary_markdown,
        summary_filename=summary_filename,
        summary_error=summary_error,
        collection_summary_requested=report.collection_summary_requested,
        viewer_contents=validate_viewer_contents(viewer_contents),
    )


def generate_notes(
    video_info: VideoCollection,
    *,
    selected_parts: tuple[VideoPart, ...],
    note_mode: str | None = None,
    extra_instruction: str | None = None,
    generate_collection_summary: bool = False,
    generate_segmented_notes: bool = False,
    include_transcript_in_viewer: bool = True,
    note_template: ResolvedNoteTemplate | None = None,
    secondary_note_template: ResolvedNoteTemplate | None = None,
    cookies_from_browser: str | None = None,
    output_root: Path = OUTPUT_DIR,
    on_event: Callable[[str], None] | None = None,
) -> WebGenerationResult:
    """把网页参数交给 pipeline，不在网页层复制字幕或模型调用逻辑。"""

    if not selected_parts:
        raise PartSelectionError("请至少选择一个分 P。")

    common_options = {
        "output_root": output_root,
        "note_mode": note_mode,
        "extra_instruction": extra_instruction,
        "cookies_from_browser": cookies_from_browser,
        "on_event": on_event,
    }
    if len(selected_parts) >= 2:
        report = process_multi_part_video(
            video_info,
            selected_parts=selected_parts,
            generate_collection_summary=generate_collection_summary,
            **common_options,
        )
        return _read_multi_part_report(
            report,
            include_transcript_in_viewer=include_transcript_in_viewer,
        )

    part = selected_parts[0]
    selected_from_collection = video_info.is_multi_part
    single_part_video_info = video_info
    if selected_from_collection:
        single_part_video_info = VideoCollection(
            bvid=video_info.bvid,
            title=video_info.title,
            description=video_info.description,
            parts=(part,),
        )
    output_page_number = part.page_number if selected_from_collection else None
    try:
        report = process_single_part_video(
            single_part_video_info,
            generate_segmented_notes=generate_segmented_notes,
            note_template=note_template,
            secondary_note_template=secondary_note_template,
            output_page_number=output_page_number,
            **common_options,
        )
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
            segmented_notes_requested=generate_segmented_notes,
        )
    except SubtitleLoginRequiredError as error:
        return WebGenerationResult(
            is_multi_part=False,
            parts=(
                WebPartResult(
                    page_number=part.page_number,
                    title=getattr(error, "video_title", "") or part.title,
                    error_type="processing_failed",
                    error=_web_error_message(str(error)),
                ),
            ),
            segmented_notes_requested=generate_segmented_notes,
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
            segmented_notes_requested=generate_segmented_notes,
        )
    segmented_markdown, segmented_filename, segmented_error = _read_segmented_report(
        report
    )
    secondary_markdown, secondary_filename, secondary_error = _read_secondary_report(report)
    viewer_contents: list[ViewerContent] = []
    part_result = _read_single_part_report(report, page_number=part.page_number)
    if part_result.succeeded and part_result.markdown is not None and part_result.filename:
        viewer_contents.append(
            ViewerContent(
                content_id="overall-a",
                label="总体笔记 A",
                kind="overall",
                text=part_result.markdown,
                filename=part_result.filename,
                template_name=note_template.name if note_template else None,
                section_keys=note_template.section_keys if note_template else (),
            )
        )
    if secondary_markdown is not None and secondary_filename is not None:
        viewer_contents.append(
            ViewerContent(
                content_id="overall-b",
                label="总体笔记 B",
                kind="secondary",
                text=secondary_markdown,
                filename=secondary_filename,
                template_name=(
                    secondary_note_template.name if secondary_note_template else None
                ),
                section_keys=(
                    secondary_note_template.section_keys
                    if secondary_note_template
                    else ()
                ),
            )
        )
    if segmented_markdown is not None and segmented_filename is not None:
        viewer_contents.append(
            ViewerContent(
                content_id="segmented",
                label="内容分段笔记",
                kind="segmented",
                text=segmented_markdown,
                filename=segmented_filename,
            )
        )
    if include_transcript_in_viewer and report.video.transcript is not None:
        transcript = report.video.transcript
        transcript_prefix = report.video.bvid
        if output_page_number is not None:
            transcript_prefix += f"_P{output_page_number:02d}"
        viewer_contents.append(
            ViewerContent(
                content_id="transcript",
                label="原始字幕（B站）",
                kind="transcript",
                text=transcript.plain_text,
                filename=f"{transcript_prefix}_transcript.txt",
                download_mime="text/plain",
                transcript_source=transcript.source,
                srt_text=transcript.to_srt(),
                srt_filename=f"{transcript_prefix}_transcript.srt",
            )
        )

    return WebGenerationResult(
        is_multi_part=False,
        parts=(part_result,),
        segmented_notes_requested=report.segmented_notes_requested,
        segmented_markdown=segmented_markdown,
        segmented_filename=segmented_filename,
        segmented_error=segmented_error,
        template=note_template,
        secondary_markdown=secondary_markdown,
        secondary_filename=secondary_filename,
        secondary_error=secondary_error,
        secondary_template=secondary_note_template,
        viewer_contents=validate_viewer_contents(viewer_contents),
    )
