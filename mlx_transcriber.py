"""mlx-whisper 的可选适配器。

本模块不被现有 pipeline 直接依赖。任务层应通过
``create_mlx_transcriber`` 创建一个实例，并在多个分 P 间复用该实例。
模型仓库和引擎专属选项集中在这里，避免泄漏到统一 Transcriber 接口。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from transcriber import (
    InvalidTranscriptionError,
    Transcriber,
    TranscriberError,
    TranscriberUnavailableError,
    transcript_from_segments,
)
from transcript import Transcript


DEFAULT_MLX_MODEL = "mlx-community/whisper-small-mlx"
"""当前 Apple Silicon 验证推荐的模型。"""


MlxTranscribeCallable = Callable[..., Mapping[str, object]]


def _load_mlx_transcribe() -> MlxTranscribeCallable:
    """延迟加载可选依赖，避免离线测试和普通安装受 Metal 影响。"""

    try:
        import mlx_whisper
    except Exception as error:  # pragma: no cover - 依赖缺失由运行环境决定
        raise TranscriberUnavailableError(
            "mlx-whisper 当前不可用，请先安装可选 ASR 依赖并确认运行环境支持 Apple Silicon。"
        ) from error
    return mlx_whisper.transcribe


class MlxWhisperTranscriber:
    """把 mlx-whisper 输出转换为统一 ``Transcriber`` 结果。

    mlx-whisper 在进程内按模型路径缓存已加载模型；因此同一个适配器实例
    应被一个任务的多个分 P 复用。这里不暴露 device、dtype 或 beam size
    等引擎参数。
    """

    def __init__(
        self,
        *,
        transcribe_fn: MlxTranscribeCallable | None = None,
    ) -> None:
        self._transcribe_fn = transcribe_fn or _load_mlx_transcribe()

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> Transcript:
        if not audio_path.is_file():
            raise TranscriberError(f"找不到待转录音频：{audio_path}")

        options: dict[str, Any] = {
            "path_or_hf_repo": DEFAULT_MLX_MODEL,
            "verbose": False,
        }
        if language is not None:
            options["language"] = language

        try:
            result = self._transcribe_fn(str(audio_path), **options)
        except TranscriberError:
            raise
        except Exception as error:
            raise TranscriberError(f"mlx-whisper 转录失败：{error}") from error

        segments = result.get("segments")
        if not isinstance(segments, (list, tuple)):
            raise InvalidTranscriptionError("mlx-whisper 没有返回有效的 segments。")

        detected_language = result.get("language")
        result_language = language
        if result_language is None and isinstance(detected_language, str):
            result_language = detected_language

        return transcript_from_segments(segments, language=result_language)


def create_mlx_transcriber() -> Transcriber:
    """创建当前 Apple Silicon 推荐的 MLX 转录器。"""

    return MlxWhisperTranscriber()
