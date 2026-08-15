"""ASR 转录器的统一接口和离线 benchmark 辅助工具。

本模块只定义 ASR 与现有 ``Transcript`` 数据模型之间的边界，不依赖
具体引擎。具体引擎适配器应在本模块之外实现，并由任务层创建一次后
复用；pipeline 不应暴露或传递引擎专属参数。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import math
from pathlib import Path
from time import perf_counter
from typing import Protocol, TypeAlias

from transcript import Transcript, TranscriptCue, TranscriptParseError


class TranscriberError(Exception):
    """ASR 转录相关错误的基类。"""


class TranscriberUnavailableError(TranscriberError):
    """具体 ASR 引擎或其依赖当前不可用。"""


class InvalidTranscriptionError(TranscriberError):
    """引擎返回的转录结果无法转换为统一 Transcript。"""


class Transcriber(Protocol):
    """任务级复用的 ASR 转录接口。

    实现类可以在构造函数中加载模型。调用方应为一次任务创建一个实例，
    再连续处理多个音频文件；引擎专属的 device、compute type、beam size
    等参数只能留在实现类或工厂内部。
    """

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> Transcript:
        """转录一个音频文件并返回带时间轴的 ASR Transcript。"""


TranscriptSegment: TypeAlias = Mapping[str, object] | object
TranscriberFactory: TypeAlias = Callable[[], Transcriber]


def _segment_value(segment: TranscriptSegment, key: str) -> object:
    """从 dict 或带属性的引擎 segment 对象中读取字段。"""

    if isinstance(segment, Mapping):
        return segment.get(key)
    return getattr(segment, key, None)


def transcript_from_segments(
    segments: Iterable[TranscriptSegment],
    *,
    language: str | None = None,
) -> Transcript:
    """把常见 ASR segment 结果转换为统一 ``Transcript``。

    适配器只需要提供 ``start``、``end`` 和 ``text`` 字段；该函数集中
    处理数值、空文本、时间顺序校验和来源标记；不会主动重新排序，
    避免不同引擎产生不同的数据约定。
    """

    cues: list[TranscriptCue] = []
    for index, segment in enumerate(segments, start=1):
        raw_start = _segment_value(segment, "start")
        raw_end = _segment_value(segment, "end")
        raw_text = _segment_value(segment, "text")
        if raw_start is None or raw_end is None or raw_text is None:
            raise InvalidTranscriptionError(
                f"第 {index} 个 ASR 分段缺少 start、end 或 text。"
            )
        try:
            start = float(raw_start)
            end = float(raw_end)
        except (TypeError, ValueError) as error:
            raise InvalidTranscriptionError(
                f"第 {index} 个 ASR 分段的时间不是有效数值。"
            ) from error
        if not math.isfinite(start) or not math.isfinite(end):
            raise InvalidTranscriptionError(
                f"第 {index} 个 ASR 分段的时间必须是有限数值。"
            )
        text = str(raw_text).strip()
        if not text:
            raise InvalidTranscriptionError(f"第 {index} 个 ASR 分段文本不能为空。")
        try:
            cues.append(TranscriptCue(start, end, text))
        except TranscriptParseError as error:
            raise InvalidTranscriptionError(
                f"第 {index} 个 ASR 分段无效：{error}"
            ) from error

    if not cues:
        raise InvalidTranscriptionError("ASR 没有返回任何有效分段。")

    try:
        return Transcript(
            source="asr",
            language=(language or "und").strip() or "und",
            cues=tuple(cues),
        )
    except TranscriptParseError as error:
        raise InvalidTranscriptionError(f"ASR 时间轴无效：{error}") from error


@dataclass(frozen=True)
class BenchmarkSample:
    """一次 benchmark 使用的音频样本。"""

    name: str
    audio_path: Path

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("benchmark 样本名称不能为空。")
        if not self.audio_path.is_file():
            raise FileNotFoundError(f"找不到 benchmark 音频：{self.audio_path}")


@dataclass(frozen=True)
class BenchmarkSampleResult:
    """一个样本的转录 benchmark 结果。"""

    name: str
    elapsed_seconds: float
    cue_count: int
    text_length: int
    language: str


@dataclass(frozen=True)
class BenchmarkRun:
    """一次创建 Transcriber 后连续处理多个样本的结果。"""

    transcriber_create_seconds: float
    samples: tuple[BenchmarkSampleResult, ...]
    total_seconds: float


def benchmark_transcriber_reuse(
    factory: TranscriberFactory,
    samples: Sequence[BenchmarkSample],
    *,
    language: str | None = None,
) -> BenchmarkRun:
    """验证一次创建后连续复用同一个 Transcriber。

    ``factory`` 在本函数中严格只调用一次；
    ``transcriber_create_seconds`` 只记录 Transcriber 创建耗时，不声称
    覆盖延迟模型加载；每个样本只记录转录耗时。因此可以观察一次创建后
    连续处理多个 P 的成本。真实引擎 benchmark 与自动化测试均可复用这个
    入口，测试时传入 fake factory 即可完全离线运行。
    """

    if not samples:
        raise ValueError("benchmark 至少需要一个音频样本。")

    create_started = perf_counter()
    transcriber = factory()
    transcriber_create_seconds = perf_counter() - create_started

    sample_results: list[BenchmarkSampleResult] = []
    total_started = perf_counter()
    for sample in samples:
        started = perf_counter()
        transcript = transcriber.transcribe(
            sample.audio_path,
            language=language,
        )
        elapsed_seconds = perf_counter() - started
        sample_results.append(
            BenchmarkSampleResult(
                name=sample.name,
                elapsed_seconds=elapsed_seconds,
                cue_count=len(transcript.cues),
                text_length=len(transcript.plain_text),
                language=transcript.language,
            )
        )

    return BenchmarkRun(
        transcriber_create_seconds=transcriber_create_seconds,
        samples=tuple(sample_results),
        total_seconds=perf_counter() - total_started,
    )
