"""带时间轴的字幕数据模型、SRT 解析和格式转换。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal


TranscriptSource = Literal["bilibili", "asr"]
SRT_TIMESTAMP_PATTERN = re.compile(
    r"^(?P<start>\d+:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d+:\d{2}:\d{2}[,.]\d{3})(?:\s+.*)?$"
)
SRT_TIME_VALUE_PATTERN = re.compile(
    r"^(?P<hours>\d+):(?P<minutes>\d{2}):(?P<seconds>\d{2})"
    r"[,.](?P<milliseconds>\d{3})$"
)


class TranscriptParseError(ValueError):
    """字幕文本或时间轴无法解析。"""


@dataclass(frozen=True)
class TranscriptCue:
    """一个带开始、结束时间的字幕片段。"""

    start_seconds: float
    end_seconds: float
    text: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.start_seconds) or self.start_seconds < 0:
            raise TranscriptParseError("字幕开始时间必须是非负有限数值。")
        if not math.isfinite(self.end_seconds):
            raise TranscriptParseError("字幕结束时间必须是有限数值。")
        if self.end_seconds <= self.start_seconds:
            raise TranscriptParseError("字幕结束时间必须晚于开始时间。")
        if not self.text.strip():
            raise TranscriptParseError("字幕文本不能为空。")


@dataclass(frozen=True)
class Transcript:
    """一条字幕轨道及其全部带时间轴片段。"""

    source: TranscriptSource
    language: str
    cues: tuple[TranscriptCue, ...]

    def __post_init__(self) -> None:
        if self.source not in {"bilibili", "asr"}:
            raise TranscriptParseError(f"不支持的字幕来源：{self.source}")
        if not self.language.strip():
            raise TranscriptParseError("字幕语言不能为空。")

        previous_start = -1.0
        for cue in self.cues:
            if cue.start_seconds < previous_start:
                raise TranscriptParseError("字幕时间轴必须按开始时间递增排列。")
            previous_start = cue.start_seconds

    @property
    def plain_text(self) -> str:
        """返回与旧版 ``subtitle_text`` 一致的纯文本字幕。"""

        return "\n".join(cue.text for cue in self.cues)

    def slice(self, start_seconds: float, end_seconds: float) -> "Transcript":
        """截取与半开区间 ``[start_seconds, end_seconds)`` 相交的片段。"""

        if not math.isfinite(start_seconds) or start_seconds < 0:
            raise ValueError("截取开始时间必须是非负有限数值。")
        if not math.isfinite(end_seconds):
            raise ValueError("截取结束时间必须是有限数值。")
        if end_seconds <= start_seconds:
            raise ValueError("截取结束时间必须晚于开始时间。")

        selected = tuple(
            cue
            for cue in self.cues
            if cue.end_seconds > start_seconds and cue.start_seconds < end_seconds
        )
        return Transcript(
            source=self.source,
            language=self.language,
            cues=selected,
        )

    def to_srt(self) -> str:
        """在内存中生成标准 SRT 文本，不写入输出目录。"""

        blocks = [
            "\n".join(
                (
                    str(index),
                    f"{_format_srt_timestamp(cue.start_seconds)} --> "
                    f"{_format_srt_timestamp(cue.end_seconds)}",
                    cue.text,
                )
            )
            for index, cue in enumerate(self.cues, start=1)
        ]
        return "\n\n".join(blocks) + ("\n" if blocks else "")


def _parse_srt_timestamp(value: str) -> float:
    """把一个 SRT 时间值转换为秒。"""

    matched = SRT_TIME_VALUE_PATTERN.fullmatch(value.strip())
    if matched is None:
        raise TranscriptParseError(f"无法识别字幕时间：{value}")

    hours = int(matched.group("hours"))
    minutes = int(matched.group("minutes"))
    seconds = int(matched.group("seconds"))
    milliseconds = int(matched.group("milliseconds"))
    if minutes >= 60 or seconds >= 60:
        raise TranscriptParseError(f"字幕时间超出有效范围：{value}")
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def _format_srt_timestamp(seconds: float) -> str:
    """把秒数格式化为 ``HH:MM:SS,mmm``。"""

    total_milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def parse_srt(
    srt_text: str,
    *,
    source: TranscriptSource,
    language: str,
) -> Transcript:
    """解析 SRT 字幕，保留时间轴并生成统一 Transcript。"""

    value = srt_text.strip().lstrip("\ufeff")
    if not value:
        return Transcript(source=source, language=language, cues=())

    cues: list[TranscriptCue] = []
    blocks = re.split(r"\r?\n\s*\r?\n", value)
    for block_number, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if lines[0].isdigit():
            lines.pop(0)
        if not lines:
            raise TranscriptParseError(
                f"第 {block_number} 个字幕片段缺少时间轴。"
            )

        matched = SRT_TIMESTAMP_PATTERN.fullmatch(lines.pop(0))
        if matched is None:
            raise TranscriptParseError(
                f"第 {block_number} 个字幕片段缺少有效时间轴。"
            )

        text = " ".join(lines).strip()
        if not text:
            continue

        try:
            cue = TranscriptCue(
                start_seconds=_parse_srt_timestamp(matched.group("start")),
                end_seconds=_parse_srt_timestamp(matched.group("end")),
                text=text,
            )
        except TranscriptParseError as error:
            raise TranscriptParseError(
                f"第 {block_number} 个字幕片段无效：{error}"
            ) from error
        cues.append(cue)

    return Transcript(source=source, language=language, cues=tuple(cues))
