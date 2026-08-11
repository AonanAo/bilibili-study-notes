from __future__ import annotations

import io
import json

import pytest

import bilibili


class FakeYoutubeDL:
    """测试用的 yt-dlp 替身，不访问真实网络。"""

    info: dict = {}
    warning: str | None = None

    def __init__(self, options: dict) -> None:
        self.options = options

    def __enter__(self) -> "FakeYoutubeDL":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def extract_info(self, url: str, download: bool) -> dict:
        if self.warning:
            self.options["logger"].warning(self.warning)
        return self.info


def test_extract_bvid_from_standard_url() -> None:
    url = "https://www.bilibili.com/video/BV1jJ411r7eH/?p=2"
    assert bilibili.extract_bvid(url) == "BV1jJ411r7eH"


def test_extract_new_long_bvid_from_url_with_parameters() -> None:
    url = "https://www.bilibili.com/video/BV1DfrdByE2Hx/?spm_id_from=xxx"
    assert bilibili.extract_bvid(url) == "BV1DfrdByE2Hx"


@pytest.mark.parametrize("bvid", ["BV1jJ411r7eH", "BV1DfrdByE2Hx"])
def test_extract_bvid_from_direct_input(bvid: str) -> None:
    assert bilibili.extract_bvid(bvid) == bvid


@pytest.mark.parametrize(
    "url",
    [
        "",
        "BV123",
        "https://example.com/video/BV1jJ411r7eH",
        "https://www.bilibili.com/read/BV1jJ411r7eH",
        "https://www.bilibili.com/video/not-a-bvid",
    ],
)
def test_extract_bvid_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(bilibili.InvalidVideoURLError):
        bilibili.extract_bvid(url)


def test_srt_to_plain_text() -> None:
    srt_text = """1
00:00:00,000 --> 00:00:02,000
大家好

2
00:00:02,000 --> 00:00:04,000
Welcome to
this course.
"""
    assert bilibili._srt_to_plain_text(srt_text) == "大家好\nWelcome to this course."


def test_fetch_returns_title_description_and_chinese_subtitle(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeYoutubeDL.warning = None
    FakeYoutubeDL.info = {
        "title": "Python 入门",
        "description": "这是视频简介。",
        "subtitles": {
            "en-US": [{"ext": "srt", "data": "1\n00:00:00,000 --> 00:00:01,000\nHello\n"}],
            "zh-CN": [{"ext": "srt", "data": "1\n00:00:00,000 --> 00:00:01,000\n你好\n"}],
            "danmaku": [{"ext": "xml", "url": "https://example.com/danmaku.xml"}],
        },
    }
    monkeypatch.setattr(bilibili, "YoutubeDL", FakeYoutubeDL)

    result = bilibili.fetch_video_subtitle(
        "https://www.bilibili.com/video/BV1jJ411r7eH",
        cookies_from_browser="chrome",
    )

    assert result.title == "Python 入门"
    assert result.description == "这是视频简介。"
    assert result.subtitle_language == "zh-CN"
    assert result.subtitle_text == "你好"


def test_direct_bvid_is_converted_to_standard_url(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_urls: list[str] = []

    class RecordingYoutubeDL(FakeYoutubeDL):
        def extract_info(self, url: str, download: bool) -> dict:
            requested_urls.append(url)
            return super().extract_info(url, download)

    RecordingYoutubeDL.warning = None
    RecordingYoutubeDL.info = {
        "title": "测试视频",
        "description": "",
        "subtitles": {
            "zh-CN": [
                {"ext": "srt", "data": "1\n00:00:00,000 --> 00:00:01,000\n字幕\n"}
            ]
        },
    }
    monkeypatch.setattr(bilibili, "YoutubeDL", RecordingYoutubeDL)

    bilibili.fetch_video_subtitle("BV1DfrdByE2Hx")

    assert requested_urls == ["https://www.bilibili.com/video/BV1DfrdByE2Hx"]


def test_get_video_parts_returns_single_part(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeYoutubeDL.warning = None
    FakeYoutubeDL.info = {
        "id": "BV1DfrdByE2Hx",
        "title": "单P视频",
        "description": "简介",
    }
    monkeypatch.setattr(bilibili, "YoutubeDL", FakeYoutubeDL)

    collection = bilibili.get_video_parts("BV1DfrdByE2Hx")

    assert collection.is_multi_part is False
    assert collection.title == "单P视频"
    assert collection.parts == (
        bilibili.VideoPart(
            page_number=1,
            title="单P视频",
            url="https://www.bilibili.com/video/BV1DfrdByE2Hx",
        ),
    )


def test_get_video_parts_parses_multi_part_playlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MetadataYoutubeDL(FakeYoutubeDL):
        metadata = {
            "code": 0,
            "data": {
                "title": "接口课程标题",
                "desc": "接口返回的课程简介",
                "pages": [
                    {"page": 1, "part": "变量"},
                    {"page": 2, "part": "接口函数标题"},
                    {"page": 3, "part": "类"},
                ],
            },
        }

        def urlopen(self, _url: str) -> io.StringIO:
            return io.StringIO(json.dumps(self.metadata))

    MetadataYoutubeDL.warning = None
    MetadataYoutubeDL.info = {
        "_type": "playlist",
        "id": "BV1DfrdByE2Hx",
        "title": "Python 课程",
        "entries": [
            {
                "_type": "url",
                "url": "https://www.bilibili.com/video/BV1DfrdByE2Hx?p=1",
            },
            {
                "_type": "url",
                "url": "https://www.bilibili.com/video/BV1DfrdByE2Hx?p=2",
                "title": "函数",
            },
            {
                "_type": "url",
                "url": "https://www.bilibili.com/video/BV1DfrdByE2Hx?p=3",
            },
        ],
    }
    monkeypatch.setattr(bilibili, "YoutubeDL", MetadataYoutubeDL)

    collection = bilibili.get_video_parts(
        "https://www.bilibili.com/video/BV1DfrdByE2Hx?p=2"
    )

    assert collection.is_multi_part is True
    assert collection.title == "Python 课程"
    assert collection.description == "接口返回的课程简介"
    assert [part.page_number for part in collection.parts] == [1, 2, 3]
    assert [part.title for part in collection.parts] == ["变量", "函数", "类"]
    assert collection.parts[2].url.endswith("?p=3")


def test_get_video_parts_keeps_fallbacks_when_metadata_request_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingMetadataYoutubeDL(FakeYoutubeDL):
        def urlopen(self, _url: str):
            raise OSError("metadata unavailable")

    FailingMetadataYoutubeDL.warning = None
    FailingMetadataYoutubeDL.info = {
        "_type": "playlist",
        "id": "BV1DfrdByE2Hx",
        "title": "Python 课程",
        "entries": [
            {
                "_type": "url",
                "url": "https://www.bilibili.com/video/BV1DfrdByE2Hx?p=1",
            },
            {
                "_type": "url",
                "url": "https://www.bilibili.com/video/BV1DfrdByE2Hx?p=2",
                "title": "函数",
            },
        ],
    }
    monkeypatch.setattr(bilibili, "YoutubeDL", FailingMetadataYoutubeDL)

    collection = bilibili.get_video_parts("BV1DfrdByE2Hx")

    assert collection.description == ""
    assert [part.title for part in collection.parts] == ["第 1 分P", "函数"]


def test_fetch_reports_no_subtitle(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeYoutubeDL.warning = None
    FakeYoutubeDL.info = {"title": "无字幕视频", "description": "", "subtitles": {}}
    monkeypatch.setattr(bilibili, "YoutubeDL", FakeYoutubeDL)

    with pytest.raises(bilibili.NoSubtitleError, match="没有可用"):
        bilibili.fetch_video_subtitle("https://www.bilibili.com/video/BV1jJ411r7eH")


def test_fetch_reports_login_required(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeYoutubeDL.warning = "Subtitles are only available when logged in."
    FakeYoutubeDL.info = {
        "title": "需登录视频",
        "description": "",
        "subtitles": {"danmaku": [{"ext": "xml"}]},
    }
    monkeypatch.setattr(bilibili, "YoutubeDL", FakeYoutubeDL)

    with pytest.raises(bilibili.SubtitleLoginRequiredError, match="登录"):
        bilibili.fetch_video_subtitle("https://www.bilibili.com/video/BV1jJ411r7eH")


def test_fetch_wraps_ytdlp_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingYoutubeDL:
        def __init__(self, options: dict) -> None:
            raise bilibili.YoutubeDLError("failed to load cookies")

    monkeypatch.setattr(bilibili, "YoutubeDL", FailingYoutubeDL)

    with pytest.raises(bilibili.BilibiliFetchError, match="failed to load cookies"):
        bilibili.fetch_video_subtitle(
            "https://www.bilibili.com/video/BV1jJ411r7eH",
            cookies_from_browser="chrome",
        )
