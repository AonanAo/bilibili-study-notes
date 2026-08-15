"""单个 B 站分 P 的音频下载和临时文件生命周期。"""

from __future__ import annotations

import errno
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from yt_dlp import YoutubeDL


class AudioError(Exception):
    """音频获取相关错误的基类。"""


class AudioDownloadError(AudioError):
    """yt-dlp 无法下载目标音频。"""


class AudioStorageError(AudioError):
    """临时目录或音频文件无法写入。"""


@dataclass(frozen=True)
class AudioArtifact:
    """临时音频文件的最小描述。"""

    path: Path
    video_id: str
    part: int


def _contains_enospc(error: BaseException) -> bool:
    """检查异常链和参数中是否包含 ENOSPC。"""

    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno == errno.ENOSPC:
            return True
        cause = current.__cause__
        context = current.__context__
        if cause is not None:
            pending.append(cause)
        if context is not None:
            pending.append(context)
        pending.extend(
            value
            for value in getattr(current, "args", ())
            if isinstance(value, BaseException)
        )
    return False


def _create_temp_dir(temp_root: Path | None) -> Path:
    """创建本次下载专属的临时子目录。"""

    try:
        directory = tempfile.mkdtemp(
            prefix="bilibili-audio-",
            dir=str(temp_root) if temp_root is not None else None,
        )
    except OSError as error:
        if error.errno == errno.ENOSPC:
            raise AudioStorageError("创建音频临时目录失败：磁盘空间不足。") from error
        raise AudioStorageError(f"创建音频临时目录失败：{error}") from error
    return Path(directory)


def _cleanup_temp_dir(temp_dir: Path) -> None:
    """删除下载目录，包括可能残留的 ``.part`` 文件。"""

    try:
        shutil.rmtree(temp_dir)
    except OSError as error:
        if error.errno == errno.ENOSPC:
            raise AudioStorageError("清理音频临时目录失败：磁盘空间不足。") from error
        raise AudioStorageError(f"清理音频临时目录失败：{error}") from error


def _find_downloaded_file(temp_dir: Path) -> Path:
    """找到 yt-dlp 下载出的唯一最终文件，不把 ``.part`` 当成成品。"""

    files = sorted(
        path
        for path in temp_dir.iterdir()
        if path.is_file() and not path.name.endswith(".part")
    )
    if len(files) != 1:
        raise AudioDownloadError(
            "yt-dlp 下载完成后没有找到唯一的音频文件。"
        )
    return files[0]


def _download_one(
    video_url: str,
    *,
    temp_dir: Path,
    cookies_from_browser: str | None,
) -> Path:
    """在已创建的临时目录中下载一个音频文件。"""

    options: dict[str, object] = {
        # 只选择音频格式；不使用 best 回退，避免意外下载视频流。
        "format": "bestaudio",
        "outtmpl": str(temp_dir / "audio.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    if cookies_from_browser:
        options["cookiesfrombrowser"] = (cookies_from_browser,)

    try:
        with YoutubeDL(options) as downloader:
            downloader.extract_info(video_url, download=True)
        return _find_downloaded_file(temp_dir)
    except Exception as error:
        if _contains_enospc(error):
            raise AudioStorageError("下载音频失败：磁盘空间不足。") from error
        raise AudioDownloadError(f"下载音频失败：{error}") from error


@contextmanager
def download_audio(
    video_url: str,
    *,
    video_id: str,
    part: int,
    cookies_from_browser: str | None = None,
    temp_root: Path | None = None,
) -> Iterator[AudioArtifact]:
    """下载一个分 P 的 audio-only 文件，并在离开上下文时清理它。

    ``temp_root`` 只用于测试或未来调用方明确指定临时文件根目录；默认使用
    系统临时目录。调用方必须在 ``with`` 块内使用返回的音频路径。

    下载失败、调用方在上下文内抛出可捕获异常以及正常结束，都会清理本次
    下载目录。程序被强制终止、断电或系统崩溃时无法承诺执行清理。
    """

    if not video_url.strip():
        raise ValueError("音频下载链接不能为空。")
    if not video_id.strip():
        raise ValueError("video_id 不能为空。")
    if part < 1:
        raise ValueError("分 P 序号必须大于等于 1。")

    temp_dir = _create_temp_dir(temp_root)
    try:
        audio_path = _download_one(
            video_url,
            temp_dir=temp_dir,
            cookies_from_browser=cookies_from_browser,
        )
        artifact = AudioArtifact(path=audio_path, video_id=video_id, part=part)
        try:
            yield artifact
        finally:
            _cleanup_temp_dir(temp_dir)
    except AudioError:
        # 下载和清理函数已经提供了明确错误类型；这里保持其原样。
        if temp_dir.exists():
            _cleanup_temp_dir(temp_dir)
        raise
    except Exception:
        # 上下文调用方的异常不改写，但仍然保证临时文件被清理。
        if temp_dir.exists():
            _cleanup_temp_dir(temp_dir)
        raise
