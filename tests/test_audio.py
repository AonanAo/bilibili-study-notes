from __future__ import annotations

import errno
from pathlib import Path

import pytest
import yt_dlp

import audio


class FakeYoutubeDL:
    options: dict[str, object] | None = None
    behavior = "success"

    def __init__(self, options: dict[str, object]) -> None:
        type(self).options = options

    def __enter__(self) -> FakeYoutubeDL:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def extract_info(self, _url: str, *, download: bool) -> dict[str, object]:
        assert download is True
        outtmpl = Path(str(type(self).options["outtmpl"]))
        if type(self).behavior == "download-error":
            outtmpl.with_suffix(outtmpl.suffix + ".part").write_bytes(b"partial")
            raise yt_dlp.utils.DownloadError("network unavailable")
        if type(self).behavior == "storage-error":
            outtmpl.with_suffix(outtmpl.suffix + ".part").write_bytes(b"partial")
            raise OSError(errno.ENOSPC, "No space left on device")
        if type(self).behavior == "wrapped-storage-error":
            raise yt_dlp.utils.DownloadError(
                OSError(errno.ENOSPC, "No space left on device")
            )
        outtmpl.with_name("audio.m4a").write_bytes(b"compressed audio")
        return {"ext": "m4a"}


def test_download_audio_returns_artifact_and_cleans_real_temp_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    FakeYoutubeDL.behavior = "success"
    monkeypatch.setattr(audio, "YoutubeDL", FakeYoutubeDL)

    with audio.download_audio(
        "https://www.bilibili.com/video/BV1abc?p=2",
        video_id="BV1abc",
        part=2,
        temp_root=tmp_path,
    ) as artifact:
        assert artifact.video_id == "BV1abc"
        assert artifact.part == 2
        assert artifact.path.is_file()
        assert artifact.path.suffix == ".m4a"
        assert artifact.path.parent.parent == tmp_path

    assert not any(tmp_path.iterdir())
    assert FakeYoutubeDL.options is not None
    assert FakeYoutubeDL.options["format"] == "bestaudio"
    assert FakeYoutubeDL.options["noplaylist"] is True
    assert "postprocessors" not in FakeYoutubeDL.options
    assert str(tmp_path) in str(FakeYoutubeDL.options["outtmpl"])


def test_download_failure_cleans_partial_file_and_raises_download_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    FakeYoutubeDL.behavior = "download-error"
    monkeypatch.setattr(audio, "YoutubeDL", FakeYoutubeDL)

    with pytest.raises(audio.AudioDownloadError, match="network unavailable"):
        with audio.download_audio(
            "https://www.bilibili.com/video/BV1abc?p=1",
            video_id="BV1abc",
            part=1,
            temp_root=tmp_path,
        ):
            raise AssertionError("download should not yield an artifact")

    assert not any(tmp_path.iterdir())


def test_caller_exception_still_cleans_temp_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    FakeYoutubeDL.behavior = "success"
    monkeypatch.setattr(audio, "YoutubeDL", FakeYoutubeDL)

    with pytest.raises(RuntimeError, match="stop using audio"):
        with audio.download_audio(
            "https://www.bilibili.com/video/BV1abc",
            video_id="BV1abc",
            part=1,
            temp_root=tmp_path,
        ):
            raise RuntimeError("stop using audio")

    assert not any(tmp_path.iterdir())


def test_enospc_while_creating_temp_dir_is_storage_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_mkdtemp(*_args: object, **_kwargs: object) -> str:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(audio.tempfile, "mkdtemp", fail_mkdtemp)

    with pytest.raises(audio.AudioStorageError, match="磁盘空间不足"):
        with audio.download_audio(
            "https://www.bilibili.com/video/BV1abc",
            video_id="BV1abc",
            part=1,
            temp_root=tmp_path,
        ):
            pass


@pytest.mark.parametrize("behavior", ["storage-error", "wrapped-storage-error"])
def test_enospc_during_yt_dlp_download_is_storage_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    behavior: str,
) -> None:
    FakeYoutubeDL.behavior = behavior
    monkeypatch.setattr(audio, "YoutubeDL", FakeYoutubeDL)

    with pytest.raises(audio.AudioStorageError, match="磁盘空间不足"):
        with audio.download_audio(
            "https://www.bilibili.com/video/BV1abc",
            video_id="BV1abc",
            part=1,
            temp_root=tmp_path,
        ):
            pass

    assert not any(tmp_path.iterdir())


def test_invalid_audio_request_is_rejected_before_temp_dir_creation(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        with audio.download_audio(
            " ",
            video_id="BV1abc",
            part=1,
            temp_root=tmp_path,
        ):
            pass
    assert not any(tmp_path.iterdir())
