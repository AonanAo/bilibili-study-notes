"""mlx-whisper 的可选适配器。

本模块不被现有 pipeline 直接依赖。任务层应通过
``create_mlx_transcriber`` 创建一个实例，并在多个分 P 间复用该实例。
模型仓库和引擎专属选项集中在这里，避免泄漏到统一 Transcriber 接口。
"""

from __future__ import annotations

import atexit
from collections.abc import Callable, Mapping
import os
from pathlib import Path
import shutil
import tempfile
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
_FFMPEG_SHIM_DIR: Path | None = None


def _ensure_ffmpeg_on_path() -> None:
    """在当前进程中发现可选的 imageio-ffmpeg 二进制。

    mlx-whisper 通过 PATH 调用 ffmpeg 读取压缩音频。系统已有 ffmpeg 时
    保持原行为；否则尝试使用同一虚拟环境中已安装的 imageio-ffmpeg，避免
    用户必须为字幕路径和 ASR 路径维护两套启动命令。找不到可选依赖时不在
    这里抛错，后续引擎会给出具体的 ASR 失败原因。
    """

    if shutil.which("ffmpeg"):
        return
    try:
        import imageio_ffmpeg

        ffmpeg_path = Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return
    if not ffmpeg_path.is_file():
        return

    global _FFMPEG_SHIM_DIR
    if ffmpeg_path.name == "ffmpeg":
        ffmpeg_dir = str(ffmpeg_path.parent)
    else:
        # imageio-ffmpeg 在 macOS 上常用带平台和版本的文件名；
        # mlx-whisper 固定调用名为 ``ffmpeg``，所以创建进程级临时别名。
        if _FFMPEG_SHIM_DIR is None:
            shim_dir = Path(tempfile.mkdtemp(prefix="bilibili-ffmpeg-"))
            try:
                (shim_dir / "ffmpeg").symlink_to(ffmpeg_path)
            except OSError:
                shutil.rmtree(shim_dir, ignore_errors=True)
                return
            _FFMPEG_SHIM_DIR = shim_dir
            atexit.register(shutil.rmtree, shim_dir, ignore_errors=True)
        ffmpeg_dir = str(_FFMPEG_SHIM_DIR)

    current_path = os.environ.get("PATH", "")
    entries = current_path.split(os.pathsep) if current_path else []
    if ffmpeg_dir not in entries:
        os.environ["PATH"] = os.pathsep.join((ffmpeg_dir, *entries))


def _keep_nonempty_segments(segments: list[object] | tuple[object, ...]) -> list[object]:
    """丢弃引擎偶尔返回的空文本分段，保留其余时间轴。"""

    kept: list[object] = []
    for segment in segments:
        if isinstance(segment, Mapping):
            text = segment.get("text")
        else:
            text = getattr(segment, "text", None)
        if isinstance(text, str) and not text.strip():
            continue
        kept.append(segment)
    return kept


def _load_mlx_transcribe() -> MlxTranscribeCallable:
    """延迟加载可选依赖，避免离线测试和普通安装受 Metal 影响。"""

    _ensure_ffmpeg_on_path()
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

        return transcript_from_segments(
            _keep_nonempty_segments(segments),
            language=result_language,
        )


def create_mlx_transcriber() -> Transcriber:
    """创建当前 Apple Silicon 推荐的 MLX 转录器。"""

    return MlxWhisperTranscriber()
