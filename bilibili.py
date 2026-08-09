"""B 站视频字幕获取模块。

这个模块只负责一件事：把 B 站视频的字幕变成纯文本。
站点解析和接口适配交给成熟的开源项目 yt-dlp 完成。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import YoutubeDLError


# 旧版 BV 号是“BV”加 10 位字符；新版 BV 号（例如
# BV1DfrdByE2Hx）会在“BV”后包含 11 位字符，两种都需要支持。
BV_ID_PATTERN = re.compile(r"^BV[0-9A-Za-z]{10,11}$", re.IGNORECASE)


class BilibiliError(Exception):
    """所有 B 站字幕获取错误的基类。"""


class InvalidVideoURLError(BilibiliError):
    """输入不是支持的 B 站视频链接。"""


class NoSubtitleError(BilibiliError):
    """视频没有可用的 CC/AI 字幕。"""

    def __init__(self, message: str, *, video_title: str = "") -> None:
        super().__init__(message)
        # 多 P 处理时，即使该 P 没有字幕，也能显示它的真实标题。
        self.video_title = video_title


class SubtitleLoginRequiredError(BilibiliError):
    """B 站要求登录后才能读取字幕。"""

    def __init__(self, message: str, *, video_title: str = "") -> None:
        super().__init__(message)
        self.video_title = video_title


class BilibiliFetchError(BilibiliError):
    """访问 B 站或解析视频失败。"""


@dataclass(frozen=True)
class VideoSubtitle:
    """给调用者返回的结构化结果。"""

    bvid: str
    title: str
    description: str
    subtitle_language: str
    subtitle_text: str


@dataclass(frozen=True)
class VideoPart:
    """一个 B 站分 P 的基本信息。"""

    page_number: int
    title: str
    url: str


@dataclass(frozen=True)
class VideoCollection:
    """一个 BV 号包含的所有分 P。"""

    bvid: str
    title: str
    description: str
    parts: tuple[VideoPart, ...]

    @property
    def is_multi_part(self) -> bool:
        """是否包含多个分 P。"""

        return len(self.parts) > 1


class _YtDlpLogger:
    """收集 yt-dlp 警告，避免把冗长的下载日志打印给用户。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def debug(self, message: str) -> None:
        # yt-dlp 要求自定义 logger 实现这些方法。
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def error(self, message: str) -> None:
        self.warnings.append(message)


def extract_bvid(video_input: str) -> str:
    """从 B 站视频链接或直接输入中取出 BV 号。

    支持完整的 bilibili.com/video/BV... 链接，也支持直接输入
    BV 号。其他网站的链接不会交给下载器处理。
    """

    if not isinstance(video_input, str) or not video_input.strip():
        raise InvalidVideoURLError("请输入 B 站视频链接或 BV 号。")

    value = video_input.strip()

    # 如果用户直接输入 BV 号，无需再做 URL 解析。
    if BV_ID_PATTERN.fullmatch(value):
        return "BV" + value[2:]

    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (
        hostname == "bilibili.com" or hostname.endswith(".bilibili.com")
    ):
        raise InvalidVideoURLError(
            "链接必须来自 bilibili.com，例如 "
            "https://www.bilibili.com/video/BVxxxxxxxxxx"
        )

    # 兼容链接末尾的 /，以及 ?p=2 之类的查询参数。
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0].lower() != "video":
        raise InvalidVideoURLError("链接中没有找到 /video/BV... 路径。")

    bvid = path_parts[1]
    if not BV_ID_PATTERN.fullmatch(bvid):
        raise InvalidVideoURLError("链接中的 BV 号格式不正确。")
    return "BV" + bvid[2:]


def _canonical_video_url(bvid: str, page_number: int | None = None) -> str:
    """组装标准 B 站视频链接。"""

    url = f"https://www.bilibili.com/video/{bvid}"
    return f"{url}?p={page_number}" if page_number is not None else url


def _page_number_from_url(url: str, fallback: int) -> int:
    """从 ``?p=N`` 中读取分 P 序号，失败时使用列表顺序。"""

    value = parse_qs(urlparse(url).query).get("p", [None])[-1]
    try:
        page_number = int(value)
    except (TypeError, ValueError):
        return fallback
    return page_number if page_number > 0 else fallback


def get_video_parts(
    video_input: str,
    *,
    cookies_from_browser: str | None = None,
) -> VideoCollection:
    """检测视频是否包含多 P，并返回全部分 P 列表。

    这一步使用 yt-dlp 的平铺播放列表模式，只取分 P 入口，
    不下载视频、音频或字幕。后续仍通过
    :func:`fetch_video_subtitle` 逐 P 获取真实标题和字幕。
    """

    bvid = extract_bvid(video_input)
    request_url = _canonical_video_url(bvid)
    logger = _YtDlpLogger()
    options: dict = {
        "skip_download": True,
        "extract_flat": True,
        "quiet": False,
        "no_warnings": False,
        "logger": logger,
    }
    if cookies_from_browser:
        options["cookiesfrombrowser"] = (cookies_from_browser,)

    try:
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(request_url, download=False)
    except YoutubeDLError as error:
        raise BilibiliFetchError(f"获取分 P 列表失败：{error}") from error
    except (OSError, ValueError) as error:
        raise BilibiliFetchError(f"获取分 P 列表失败：{error}") from error

    collection_title = (info.get("title") or "未知标题").strip()
    collection_description = (info.get("description") or "").strip()

    if info.get("_type") != "playlist":
        return VideoCollection(
            bvid=bvid,
            title=collection_title,
            description=collection_description,
            parts=(
                VideoPart(
                    page_number=1,
                    title=collection_title,
                    url=request_url,
                ),
            ),
        )

    parts: list[VideoPart] = []
    for fallback_number, entry in enumerate(info.get("entries") or (), start=1):
        if not isinstance(entry, dict):
            continue
        entry_url = entry.get("url") or _canonical_video_url(bvid, fallback_number)
        page_number = _page_number_from_url(entry_url, fallback_number)
        parts.append(
            VideoPart(
                page_number=page_number,
                title=(entry.get("title") or f"第 {page_number} 分P").strip(),
                url=entry_url,
            )
        )

    if not parts:
        raise BilibiliFetchError("检测到多 P 视频，但未能读取分 P 列表。")

    parts.sort(key=lambda part: part.page_number)
    return VideoCollection(
        bvid=bvid,
        title=collection_title,
        description=collection_description,
        parts=tuple(parts),
    )


def _srt_to_plain_text(srt_text: str) -> str:
    """去掉 SRT 序号和时间轴，每个字幕片段保留为一行。"""

    cue_texts: list[str] = []
    # SRT 使用空行分隔字幕块，同时兼容 Windows 换行符。
    blocks = re.split(r"\r?\n\s*\r?\n", srt_text.strip().lstrip("\ufeff"))

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        # 标准 SRT 的第一行是序号，第二行是时间轴。
        if lines and lines[0].isdigit():
            lines.pop(0)
        if lines and "-->" in lines[0]:
            lines.pop(0)

        # 同一个字幕块可能分成多行；合并后阅读更自然。
        if lines:
            cue_texts.append(" ".join(lines))

    return "\n".join(cue_texts).strip()


def _select_subtitle_track(subtitles: dict) -> tuple[str, dict] | None:
    """从多个字幕语言中优先选择简体中文。"""

    # danmaku 是弹幕，不是这个工具要的视频字幕。
    available = {
        language: tracks
        for language, tracks in (subtitles or {}).items()
        if language.lower() != "danmaku" and tracks
    }
    if not available:
        return None

    preferred_languages = ("zh-CN", "zh-Hans", "ai-zh", "zh")
    selected_language = next(
        (language for preferred in preferred_languages for language in available if language.lower() == preferred.lower()),
        next(iter(available)),
    )

    # B 站提取器会把字幕转成 SRT，并放在 data 字段中。
    track = next(
        (item for item in available[selected_language] if item.get("data")),
        available[selected_language][0],
    )
    return selected_language, track


def fetch_video_subtitle(
    video_url: str,
    *,
    cookies_from_browser: str | None = None,
) -> VideoSubtitle:
    """获取视频标题、简介和字幕纯文本。

    Args:
        video_url: 标准 B 站视频链接或直接 BV 号。
        cookies_from_browser: 可选的浏览器名称，例如 ``chrome``。
            B 站要求登录时，yt-dlp 会读取该浏览器的已登录 Cookie。

    Returns:
        VideoSubtitle: 结构化的视频与字幕信息。

    Raises:
        InvalidVideoURLError: 链接格式不正确。
        SubtitleLoginRequiredError: 字幕需登录才能读取。
        NoSubtitleError: 视频没有 CC/AI 字幕。
        BilibiliFetchError: 网络请求或视频解析失败。
    """

    bvid = extract_bvid(video_url)
    # yt-dlp 需要一个可访问的网址。直接输入 BV 号时，
    # 在这里组装标准链接；输入本来就是 URL 时则保留 ?p= 等参数。
    request_url = (
        _canonical_video_url(bvid)
        if BV_ID_PATTERN.fullmatch(video_url.strip())
        else video_url.strip()
    )
    logger = _YtDlpLogger()

    # writesubtitles=True 会让 yt-dlp 提取字幕；skip_download=True
    # 保证它不会下载体积很大的视频或音频。
    options: dict = {
        "skip_download": True,
        "writesubtitles": True,
        "quiet": False,
        "no_warnings": False,
        "logger": logger,
        "noplaylist": True,
    }
    if cookies_from_browser:
        # 使用 yt-dlp 公开支持的浏览器 Cookie 能力，不在代码里保存密码。
        options["cookiesfrombrowser"] = (cookies_from_browser,)

    try:
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(request_url, download=False)
    except YoutubeDLError as error:
        # 包括页面解析失败和浏览器 Cookie 读取失败。
        raise BilibiliFetchError(f"获取视频失败：{error}") from error
    except (OSError, ValueError) as error:
        # 浏览器 Cookie 不存在、无权读取等错误会进入这里。
        raise BilibiliFetchError(f"获取视频失败：{error}") from error

    video_title = (info.get("title") or "未知标题").strip()
    selected = _select_subtitle_track(info.get("subtitles") or {})
    if selected is None:
        warning_text = "\n".join(logger.warnings).lower()
        if "subtitles are only available when logged in" in warning_text:
            raise SubtitleLoginRequiredError(
                "B 站要求登录后才能读取该视频字幕。"
                "请使用 --cookies-from-browser 指定一个已登录 B 站的浏览器。",
                video_title=video_title,
            )
        raise NoSubtitleError(
            "该视频没有可用的 CC/AI 字幕。",
            video_title=video_title,
        )

    language, track = selected
    srt_text = track.get("data")
    if not isinstance(srt_text, str) or not srt_text.strip():
        raise BilibiliFetchError(
            "找到了字幕轨道，但 yt-dlp 没有返回字幕内容。"
            "请先升级 yt-dlp 后重试。"
        )

    subtitle_text = _srt_to_plain_text(srt_text)
    if not subtitle_text:
        raise NoSubtitleError(
            "该视频的字幕轨道为空。",
            video_title=video_title,
        )

    return VideoSubtitle(
        bvid=bvid,
        title=video_title,
        description=(info.get("description") or "").strip(),
        subtitle_language=language,
        subtitle_text=subtitle_text,
    )
