"""单 P 与多 P 视频学习笔记的处理流程。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable, ContextManager, Literal

from audio import AudioArtifact, AudioError, download_audio

from bilibili import (
    BilibiliError,
    NoSubtitleError,
    SubtitleLoginRequiredError,
    VideoCollection,
    VideoPart,
    VideoSubtitle,
    fetch_video_subtitle,
)
from llm import (
    LLMError,
    generate_course_summary,
    generate_segment_note_contents,
    generate_segment_plan,
    generate_study_notes,
)
from prompt import ResolvedNoteTemplate, resolve_note_template
from segmentation import (
    SegmentNoteContent,
    SegmentPlan,
    SegmentationError,
    assign_cues_to_segments,
    render_segmented_notes,
)
from transcript import Transcript
from transcriber import Transcriber, TranscriberError


# Windows/macOS/Linux 都不适合出现在文件名中的字符。
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
PartErrorType = Literal["no_subtitle", "processing_failed"]


class ASRFallbackError(BilibiliError):
    """字幕不可用且 ASR 回退也失败。"""

    def __init__(self, message: str, *, video_title: str = "") -> None:
        super().__init__(message)
        self.video_title = video_title


AudioDownloader = Callable[..., ContextManager[AudioArtifact]]


def _default_transcriber_factory() -> Transcriber:
    """延迟创建默认 MLX 转录器，避免字幕成功时加载可选依赖。"""

    from mlx_transcriber import create_mlx_transcriber

    return create_mlx_transcriber()


class SubtitleFirstASRFallback:
    """按任务复用转录器的字幕优先解析器。"""

    def __init__(
        self,
        *,
        subtitle_fetcher: Callable[..., VideoSubtitle],
        transcriber_factory: Callable[[], Transcriber] | None = None,
        audio_downloader: AudioDownloader = download_audio,
        asr_language: str | None = None,
    ) -> None:
        self._subtitle_fetcher = subtitle_fetcher
        self._transcriber_factory = transcriber_factory or _default_transcriber_factory
        self._audio_downloader = audio_downloader
        self._asr_language = asr_language
        self._transcriber: Transcriber | None = None

    def fetch(
        self,
        video_url: str,
        *,
        bvid: str,
        part: int,
        fallback_title: str,
        fallback_description: str,
        cookies_from_browser: str | None,
        on_event: Callable[[str], None] | None = None,
    ) -> VideoSubtitle:
        """先获取字幕；缺失或需登录时下载音频并回退到 ASR。"""

        try:
            return self._subtitle_fetcher(
                video_url,
                cookies_from_browser=cookies_from_browser,
            )
        except (NoSubtitleError, SubtitleLoginRequiredError) as subtitle_error:
            title = getattr(subtitle_error, "video_title", "") or fallback_title
            if on_event is not None:
                on_event("字幕不可用，正在下载音频并使用 ASR 回退……")

            try:
                if self._transcriber is None:
                    self._transcriber = self._transcriber_factory()
                with self._audio_downloader(
                    video_url,
                    video_id=bvid,
                    part=part,
                    cookies_from_browser=cookies_from_browser,
                ) as artifact:
                    transcript = self._transcriber.transcribe(
                        artifact.path,
                        language=self._asr_language,
                    )
            except (AudioError, TranscriberError, OSError, ValueError) as error:
                raise ASRFallbackError(
                    f"字幕不可用，ASR 回退失败：{error}",
                    video_title=title,
                ) from error
            except Exception as error:
                # 可选引擎加载和第三方下载器异常也应归入当前分 P，
                # 不能让一个回退失败中断多 P 任务。
                raise ASRFallbackError(
                    f"字幕不可用，ASR 回退失败：{error}",
                    video_title=title,
                ) from error

            if on_event is not None:
                on_event("ASR 回退成功，已获得带时间轴转录。")
            return VideoSubtitle(
                bvid=bvid,
                title=title,
                description=fallback_description,
                transcript=transcript,
            )


def fetch_video_subtitle_with_asr_fallback(
    video_url: str,
    *,
    bvid: str,
    part: int = 1,
    fallback_title: str = "",
    fallback_description: str = "",
    cookies_from_browser: str | None = None,
    subtitle_fetcher: Callable[..., VideoSubtitle] | None = None,
    transcriber_factory: Callable[[], Transcriber] | None = None,
    audio_downloader: AudioDownloader = download_audio,
    asr_language: str | None = None,
    on_event: Callable[[str], None] | None = None,
) -> VideoSubtitle:
    """一次性执行字幕优先、ASR 回退流程。"""

    resolver = SubtitleFirstASRFallback(
        subtitle_fetcher=subtitle_fetcher or fetch_video_subtitle,
        transcriber_factory=transcriber_factory,
        audio_downloader=audio_downloader,
        asr_language=asr_language,
    )
    return resolver.fetch(
        video_url,
        bvid=bvid,
        part=part,
        fallback_title=fallback_title,
        fallback_description=fallback_description,
        cookies_from_browser=cookies_from_browser,
        on_event=on_event,
    )


@dataclass
class PartProcessingResult:
    """一个分 P 的处理结果。"""

    page_number: int
    title: str
    output_path: Path | None = None
    error: str | None = None
    error_type: PartErrorType | None = None
    transcript: Transcript | None = None

    @property
    def succeeded(self) -> bool:
        return self.output_path is not None and self.error is None


@dataclass(frozen=True)
class SinglePartReport:
    """单 P 视频的处理结果。"""

    video: VideoSubtitle
    output_path: Path
    segmented_notes_requested: bool = False
    segmented_output_path: Path | None = None
    segmented_error: str | None = None
    secondary_output_path: Path | None = None
    secondary_template: ResolvedNoteTemplate | None = None
    secondary_error: str | None = None


@dataclass(frozen=True)
class MultiPartReport:
    """整个多 P 视频的处理报告。"""

    output_dir: Path
    parts: tuple[PartProcessingResult, ...]
    summary_path: Path | None
    collection_summary_requested: bool = True
    summary_error: str | None = None

    @property
    def succeeded_count(self) -> int:
        return sum(result.succeeded for result in self.parts)

    @property
    def failed_count(self) -> int:
        return len(self.parts) - self.succeeded_count


def safe_filename(value: str, *, max_length: int = 90) -> str:
    """把视频标题转成安全、可读的文件名片段。"""

    cleaned = INVALID_FILENAME_CHARS.sub("_", value)
    cleaned = re.sub(r"\s+", "_", cleaned).strip(" ._")
    cleaned = re.sub(r"_+", "_", cleaned)
    return (cleaned[:max_length].rstrip(" ._") or "未命名分P")


def save_part_notes(
    markdown: str,
    *,
    output_dir: Path,
    page_number: int,
    title: str,
) -> Path:
    """以 ``P01_标题.md`` 格式保存单个分 P 笔记。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"P{page_number:02d}_{safe_filename(title)}.md"
    output_path = output_dir / filename
    output_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return output_path


def save_single_part_notes(
    markdown: str,
    *,
    output_root: Path,
    bvid: str,
    page_number: int | None = None,
) -> Path:
    """保存总体笔记；合集单选时把 P 号写入文件名。"""

    output_root.mkdir(parents=True, exist_ok=True)
    prefix = _single_part_output_prefix(bvid, page_number)
    output_path = output_root / f"{prefix}_study_notes.md"
    output_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return output_path


def save_secondary_notes(
    markdown: str,
    *,
    output_root: Path,
    bvid: str,
    suffix: str = "B",
    page_number: int | None = None,
) -> Path:
    """保存第二份总体笔记，避免覆盖主笔记。"""

    output_root.mkdir(parents=True, exist_ok=True)
    prefix = _single_part_output_prefix(bvid, page_number)
    output_path = output_root / f"{prefix}_study_notes_{suffix}.md"
    output_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return output_path


def save_segmented_notes(
    markdown: str,
    *,
    output_root: Path,
    bvid: str,
    page_number: int | None = None,
) -> Path:
    """原子保存分段笔记，失败时不留下半成品。"""

    output_root.mkdir(parents=True, exist_ok=True)
    prefix = _single_part_output_prefix(bvid, page_number)
    output_path = output_root / f"{prefix}_segmented_notes.md"
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_root,
            prefix=f".{prefix}_segmented_notes_",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(markdown.rstrip() + "\n")
            temporary_path = Path(temporary_file.name)
        temporary_path.replace(output_path)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return output_path


def _single_part_output_prefix(bvid: str, page_number: int | None) -> str:
    """为普通单 P 和合集单选生成互不覆盖的文件名前缀。"""

    if page_number is None:
        return bvid
    if page_number < 1:
        raise ValueError("分 P 序号必须大于等于 1。")
    return f"{bvid}_P{page_number:02d}"


def save_course_summary(markdown: str, *, output_dir: Path) -> Path:
    """保存多 P 课程总结。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "summary.md"
    output_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return output_path


def _failure_label(result: PartProcessingResult) -> str:
    """生成传给合集总结的失败说明。"""

    return f"P{result.page_number:02d} {result.title}：{result.error or '未知错误'}"


def process_single_part_video(
    collection: VideoCollection,
    *,
    output_root: Path,
    note_mode: str | None = None,
    note_template: ResolvedNoteTemplate | None = None,
    secondary_note_template: ResolvedNoteTemplate | None = None,
    extra_instruction: str | None = None,
    generate_segmented_notes: bool = False,
    output_page_number: int | None = None,
    cookies_from_browser: str | None = None,
    asr_language: str | None = None,
    enable_asr_fallback: bool | None = None,
    transcriber_factory: Callable[[], Transcriber] | None = None,
    audio_downloader: AudioDownloader | None = None,
    on_event: Callable[[str], None] | None = None,
    subtitle_fetcher: Callable[..., VideoSubtitle] | None = None,
    notes_generator: Callable[..., str] | None = None,
    segment_planner: Callable[..., SegmentPlan] | None = None,
    segment_notes_generator: (
        Callable[..., tuple[SegmentNoteContent, ...]] | None
    ) = None,
) -> SinglePartReport:
    """处理单 P 视频，供命令行之外的入口复用现有生成能力。

    该函数不改变命令行当前行为，也不参与多 P 的错误隔离与合集总结。
    """

    if len(collection.parts) != 1:
        raise ValueError("单 P 处理流程只能接收包含一个分 P 的视频信息。")

    fetch_subtitle = subtitle_fetcher or fetch_video_subtitle
    generate_notes = notes_generator or generate_study_notes
    plan_segments = segment_planner or generate_segment_plan
    generate_segment_contents = segment_notes_generator or generate_segment_note_contents
    emit = on_event or (lambda _message: None)
    part = collection.parts[0]

    # 注入式旧调用保持“只测字幕”的行为；正式入口未注入 fetcher 时默认开启。
    fallback_enabled = (
        enable_asr_fallback
        if enable_asr_fallback is not None
        else subtitle_fetcher is None
    )
    fallback = (
        SubtitleFirstASRFallback(
            subtitle_fetcher=fetch_subtitle,
            transcriber_factory=transcriber_factory,
            audio_downloader=audio_downloader or download_audio,
            asr_language=asr_language,
        )
        if fallback_enabled
        else None
    )

    emit("正在获取视频字幕……")
    if fallback is None:
        video = fetch_subtitle(
            part.url,
            cookies_from_browser=cookies_from_browser,
        )
    else:
        video = fallback.fetch(
            part.url,
            bvid=collection.bvid,
            part=part.page_number,
            fallback_title=part.title,
            fallback_description=collection.description,
            cookies_from_browser=cookies_from_browser,
            on_event=emit,
        )
    emit(f"正在调用 DeepSeek：{video.title}")

    note_options = {
        "video_title": video.title,
        "video_description": video.description,
    }
    if note_mode is not None:
        note_options["mode"] = note_mode
    if extra_instruction is not None:
        note_options["extra_instruction"] = extra_instruction

    if note_template is not None:
        note_options["note_template"] = note_template
    markdown = generate_notes(video.subtitle_text, **note_options)
    output_path = save_single_part_notes(
        markdown,
        output_root=output_root,
        bvid=collection.bvid,
        page_number=output_page_number,
    )
    emit(f"学习笔记已保存：{output_path.name}")

    secondary_output_path: Path | None = None
    secondary_error: str | None = None
    if secondary_note_template is not None:
        try:
            emit("正在调用 DeepSeek 生成第二份总体笔记……")
            secondary_markdown = generate_notes(
                video.subtitle_text,
                video_title=video.title,
                video_description=video.description,
                extra_instruction=extra_instruction,
                note_template=secondary_note_template,
            )
            secondary_output_path = save_secondary_notes(
                secondary_markdown,
                output_root=output_root,
                bvid=collection.bvid,
                page_number=output_page_number,
            )
            emit(f"第二份总体笔记已保存：{secondary_output_path.name}")
        except (LLMError, OSError) as error:
            secondary_error = f"第二份总体笔记生成失败，第一份已保留：{error}"
            emit(secondary_error)

    if not generate_segmented_notes:
        return SinglePartReport(
            video=video,
            output_path=output_path,
            secondary_output_path=secondary_output_path,
            secondary_template=secondary_note_template,
            secondary_error=secondary_error,
        )

    try:
        emit("正在调用 DeepSeek 规划语义分段……")
        plan = plan_segments(
            video.transcript,
            video_title=video.title,
            video_description=video.description,
        )
        assigned_segments = assign_cues_to_segments(video.transcript, plan)

        emit("正在调用 DeepSeek 生成全部分段笔记……")
        segment_options = {
            "video_title": video.title,
            "video_description": video.description,
        }
        if extra_instruction is not None:
            segment_options["extra_instruction"] = extra_instruction
        contents = generate_segment_contents(assigned_segments, **segment_options)
        segmented_markdown = render_segmented_notes(
            assigned_segments,
            contents,
            video_title=video.title,
        )
        segmented_output_path = save_segmented_notes(
            segmented_markdown,
            output_root=output_root,
            bvid=collection.bvid,
            page_number=output_page_number,
        )
    except (LLMError, SegmentationError, OSError) as error:
        message = f"分段笔记生成失败，总体笔记已保留：{error}"
        emit(message)
        return SinglePartReport(
            video=video,
            output_path=output_path,
            segmented_notes_requested=True,
            segmented_error=message,
            secondary_output_path=secondary_output_path,
            secondary_template=secondary_note_template,
            secondary_error=secondary_error,
        )

    emit(f"分段笔记已保存：{segmented_output_path.name}")
    return SinglePartReport(
        video=video,
        output_path=output_path,
        segmented_notes_requested=True,
        segmented_output_path=segmented_output_path,
        secondary_output_path=secondary_output_path,
        secondary_template=secondary_note_template,
        secondary_error=secondary_error,
    )


def process_multi_part_video(
    collection: VideoCollection,
    *,
    output_root: Path,
    selected_parts: tuple[VideoPart, ...] | None = None,
    note_mode: str | None = None,
    extra_instruction: str | None = None,
    generate_collection_summary: bool = True,
    cookies_from_browser: str | None = None,
    asr_language: str | None = None,
    enable_asr_fallback: bool | None = None,
    transcriber_factory: Callable[[], Transcriber] | None = None,
    audio_downloader: AudioDownloader | None = None,
    on_event: Callable[[str], None] | None = None,
    subtitle_fetcher: Callable[..., VideoSubtitle] | None = None,
    notes_generator: Callable[..., str] | None = None,
    summary_generator: Callable[..., str] | None = None,
) -> MultiPartReport:
    """逐 P 生成笔记，然后读取成功文件生成合集总结。

    任何单个分 P 的字幕、模型或文件错误都会被记录，
    不会中断后续分 P。``selected_parts`` 为 ``None`` 时保持原有行为，
    处理 ``collection`` 中的全部分 P。``note_mode`` 和
    ``extra_instruction`` 只传给分 P 笔记生成，不改变课程合集总结逻辑。
    ``generate_collection_summary`` 默认为 ``True``，以保持命令行原有行为。
    """

    fetch_subtitle = subtitle_fetcher or fetch_video_subtitle
    generate_notes = notes_generator or generate_study_notes
    generate_summary = summary_generator or generate_course_summary
    emit = on_event or (lambda _message: None)

    output_dir = output_root / collection.bvid
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[PartProcessingResult] = []
    parts_to_process = selected_parts if selected_parts is not None else collection.parts
    total = len(parts_to_process)

    # 同一个 resolver 覆盖整次任务，确保模型/转录器只创建一次并复用。
    fallback_enabled = (
        enable_asr_fallback
        if enable_asr_fallback is not None
        else subtitle_fetcher is None
    )
    fallback = (
        SubtitleFirstASRFallback(
            subtitle_fetcher=fetch_subtitle,
            transcriber_factory=transcriber_factory,
            audio_downloader=audio_downloader or download_audio,
            asr_language=asr_language,
        )
        if fallback_enabled
        else None
    )

    for position, part in enumerate(parts_to_process, start=1):
        result = PartProcessingResult(
            page_number=part.page_number,
            title=part.title,
        )
        emit(
            f"[P{part.page_number:02d}] 正在获取字幕"
            f"（本次 {position}/{total}）……"
        )

        try:
            if fallback is None:
                video = fetch_subtitle(
                    part.url,
                    cookies_from_browser=cookies_from_browser,
                )
            else:
                video = fallback.fetch(
                    part.url,
                    bvid=collection.bvid,
                    part=part.page_number,
                    fallback_title=part.title,
                    fallback_description=collection.description,
                    cookies_from_browser=cookies_from_browser,
                    on_event=lambda message, _part=part: emit(
                        f"[P{_part.page_number:02d}] {message}"
                    ),
                )
            result.title = video.title
            result.transcript = video.transcript
            emit(f"[P{part.page_number:02d}] 正在调用 DeepSeek：{result.title}")
            note_options = {
                "video_title": video.title,
                "video_description": video.description,
            }
            if note_mode is not None:
                note_options["mode"] = note_mode
            if extra_instruction is not None:
                note_options["extra_instruction"] = extra_instruction
            markdown = generate_notes(video.subtitle_text, **note_options)
            result.output_path = save_part_notes(
                markdown,
                output_dir=output_dir,
                page_number=part.page_number,
                title=result.title,
            )
            emit(f"[P{part.page_number:02d}] 已保存：{result.output_path.name}")
        except NoSubtitleError as error:
            result.title = error.video_title or result.title
            result.error = f"无字幕，已跳过：{error}"
            result.error_type = "no_subtitle"
            emit(f"[P{part.page_number:02d}] {result.error}")
        except (BilibiliError, LLMError, OSError) as error:
            # 一个 P 失败时只记录，继续处理下一个 P。
            result.title = getattr(error, "video_title", "") or result.title
            result.error = str(error)
            result.error_type = "processing_failed"
            emit(f"[P{part.page_number:02d}] 处理失败，已继续：{error}")

        results.append(result)

    if not generate_collection_summary:
        emit("本次已按设置跳过合集总结")
        return MultiPartReport(
            output_dir=output_dir,
            parts=tuple(results),
            summary_path=None,
            collection_summary_requested=False,
        )

    # 按要求从已写入磁盘的 Markdown 文件重新读取内容，
    # 再交给 DeepSeek 做课程级总结。
    part_notes: list[tuple[int, str, str]] = []
    for result in results:
        if not result.succeeded:
            continue
        try:
            markdown = result.output_path.read_text(encoding="utf-8")
        except OSError as error:
            result.error = f"读取已生成笔记失败：{error}"
            result.error_type = "processing_failed"
            emit(f"[P{result.page_number:02d}] {result.error}")
            continue
        part_notes.append((result.page_number, result.title, markdown))

    failed_parts = [_failure_label(result) for result in results if not result.succeeded]
    if not part_notes:
        error = "没有任何分 P 成功生成笔记，无法生成 summary.md。"
        emit(error)
        return MultiPartReport(
            output_dir=output_dir,
            parts=tuple(results),
            summary_path=None,
            summary_error=error,
        )

    emit("正在读取所有成功的分 P 笔记并生成课程总结……")
    try:
        summary_markdown = generate_summary(
            part_notes,
            course_title=collection.title,
            failed_parts=failed_parts,
        )
        summary_path = save_course_summary(summary_markdown, output_dir=output_dir)
    except (LLMError, OSError) as error:
        message = f"课程总结生成失败，分 P 笔记已保留：{error}"
        emit(message)
        return MultiPartReport(
            output_dir=output_dir,
            parts=tuple(results),
            summary_path=None,
            summary_error=message,
        )

    emit(f"课程总结已保存：{summary_path.name}")
    return MultiPartReport(
        output_dir=output_dir,
        parts=tuple(results),
        summary_path=summary_path,
    )
