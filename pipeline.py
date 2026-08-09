"""多 P 视频学习笔记的处理流程。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from bilibili import (
    BilibiliError,
    NoSubtitleError,
    VideoCollection,
    VideoSubtitle,
    fetch_video_subtitle,
)
from llm import LLMError, generate_course_summary, generate_study_notes


# Windows/macOS/Linux 都不适合出现在文件名中的字符。
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class PartProcessingResult:
    """一个分 P 的处理结果。"""

    page_number: int
    title: str
    output_path: Path | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.output_path is not None and self.error is None


@dataclass(frozen=True)
class MultiPartReport:
    """整个多 P 视频的处理报告。"""

    output_dir: Path
    parts: tuple[PartProcessingResult, ...]
    summary_path: Path | None
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


def save_course_summary(markdown: str, *, output_dir: Path) -> Path:
    """保存多 P 课程总结。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "summary.md"
    output_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return output_path


def _failure_label(result: PartProcessingResult) -> str:
    """生成传给合集总结的失败说明。"""

    return f"P{result.page_number:02d} {result.title}：{result.error or '未知错误'}"


def process_multi_part_video(
    collection: VideoCollection,
    *,
    output_root: Path,
    cookies_from_browser: str | None = None,
    on_event: Callable[[str], None] | None = None,
    subtitle_fetcher: Callable[..., VideoSubtitle] | None = None,
    notes_generator: Callable[..., str] | None = None,
    summary_generator: Callable[..., str] | None = None,
) -> MultiPartReport:
    """逐 P 生成笔记，然后读取成功文件生成合集总结。

    任何单个分 P 的字幕、模型或文件错误都会被记录，
    不会中断后续分 P。
    """

    fetch_subtitle = subtitle_fetcher or fetch_video_subtitle
    generate_notes = notes_generator or generate_study_notes
    generate_summary = summary_generator or generate_course_summary
    emit = on_event or (lambda _message: None)

    output_dir = output_root / collection.bvid
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[PartProcessingResult] = []
    total = len(collection.parts)

    for part in collection.parts:
        result = PartProcessingResult(
            page_number=part.page_number,
            title=part.title,
        )
        emit(f"[P{part.page_number:02d}/{total:02d}] 正在获取字幕……")

        try:
            video = fetch_subtitle(
                part.url,
                cookies_from_browser=cookies_from_browser,
            )
            result.title = video.title
            emit(f"[P{part.page_number:02d}] 正在调用 DeepSeek：{result.title}")
            markdown = generate_notes(
                video.subtitle_text,
                video_title=video.title,
                video_description=video.description,
            )
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
            emit(f"[P{part.page_number:02d}] {result.error}")
        except (BilibiliError, LLMError, OSError) as error:
            # 一个 P 失败时只记录，继续处理下一个 P。
            result.error = str(error)
            emit(f"[P{part.page_number:02d}] 处理失败，已继续：{error}")

        results.append(result)

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
