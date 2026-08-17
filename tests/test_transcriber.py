from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import mlx_transcriber
from transcriber import (
    BenchmarkSample,
    InvalidTranscriptionError,
    benchmark_transcriber_reuse,
    transcript_from_segments,
)
from mlx_transcriber import DEFAULT_MLX_MODEL, MlxWhisperTranscriber
from transcript import Transcript


def test_transcript_from_mapping_segments_marks_asr_source() -> None:
    transcript = transcript_from_segments(
        [
            {"start": 0, "end": 1.5, "text": "第一句"},
            {"start": 1.5, "end": 3, "text": "第二句"},
        ],
        language="zh",
    )

    assert isinstance(transcript, Transcript)
    assert transcript.source == "asr"
    assert transcript.language == "zh"
    assert transcript.plain_text == "第一句\n第二句"


def test_transcript_from_object_segments_supports_engine_objects() -> None:
    class Segment:
        start = 0.0
        end = 2.0
        text = "对象分段"

    transcript = transcript_from_segments([Segment()])

    assert transcript.cues[0].text == "对象分段"
    assert transcript.language == "und"


@pytest.mark.parametrize(
    "segments",
    [
        [],
        [{"start": 0, "end": 1}],
        [{"start": 2, "end": 1, "text": "结束更早"}],
        [{"start": 0, "end": 1, "text": ""}],
        [{"start": float("nan"), "end": 1, "text": "非有限"}],
    ],
)
def test_transcript_from_segments_rejects_invalid_results(segments: list[object]) -> None:
    with pytest.raises(InvalidTranscriptionError):
        transcript_from_segments(segments)


def test_benchmark_reuses_one_transcriber_for_multiple_samples(tmp_path: Path) -> None:
    first = tmp_path / "p01.audio"
    second = tmp_path / "p02.audio"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    state = {"factory_calls": 0, "transcribe_calls": []}

    class FakeTranscriber:
        def transcribe(self, audio_path: Path, *, language: str | None = None) -> Transcript:
            state["transcribe_calls"].append((audio_path, language))
            return transcript_from_segments(
                [{"start": 0, "end": 1, "text": audio_path.stem}],
                language=language,
            )

    def factory() -> FakeTranscriber:
        state["factory_calls"] += 1
        return FakeTranscriber()

    result = benchmark_transcriber_reuse(
        factory,
        [
            BenchmarkSample("P01", first),
            BenchmarkSample("P02", second),
        ],
        language="zh",
    )

    assert state["factory_calls"] == 1
    assert state["transcribe_calls"] == [(first, "zh"), (second, "zh")]
    assert result.transcriber_create_seconds >= 0
    assert [sample.name for sample in result.samples] == ["P01", "P02"]
    assert all(sample.cue_count == 1 for sample in result.samples)


def test_benchmark_requires_existing_audio_sample(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        BenchmarkSample("missing", tmp_path / "missing.audio")


def test_benchmark_requires_at_least_one_sample(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="至少需要一个"):
        benchmark_transcriber_reuse(lambda: object(), [])


def test_uses_bundled_imageio_ffmpeg_when_system_ffmpeg_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg_path = tmp_path / "ffmpeg-macos-aarch64-v7.1"
    ffmpeg_path.write_bytes(b"fake ffmpeg")
    original_path = tmp_path / "original-bin"
    monkeypatch.setenv("PATH", str(original_path))
    monkeypatch.setattr(mlx_transcriber.shutil, "which", lambda _name: None)
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: str(ffmpeg_path)),
    )

    mlx_transcriber._ensure_ffmpeg_on_path()

    shim_dir = Path(os.environ["PATH"].split(os.pathsep)[0])
    assert (shim_dir / "ffmpeg").is_symlink()
    assert (shim_dir / "ffmpeg").resolve() == ffmpeg_path


def test_mlx_loader_prepares_ffmpeg_before_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        mlx_transcriber,
        "_ensure_ffmpeg_on_path",
        lambda: calls.append("prepared"),
    )
    fake_mlx = SimpleNamespace(transcribe=lambda *_args, **_kwargs: {})
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx)

    assert mlx_transcriber._load_mlx_transcribe() is fake_mlx.transcribe
    assert calls == ["prepared"]


def test_mlx_adapter_converts_segments_without_exposing_engine_options(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "part.m4a"
    audio.write_bytes(b"audio")
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_transcribe(audio_value: str, **options: object) -> dict[str, object]:
        calls.append((audio_value, options))
        return {
            "language": "zh",
            "segments": [{"start": 0, "end": 1, "text": "模型输出"}],
        }

    transcriber = MlxWhisperTranscriber(transcribe_fn=fake_transcribe)
    transcript = transcriber.transcribe(audio)

    assert transcript.source == "asr"
    assert transcript.language == "zh"
    assert transcript.plain_text == "模型输出"
    assert calls == [
        (
            str(audio),
            {"path_or_hf_repo": DEFAULT_MLX_MODEL, "verbose": False},
        )
    ]


def test_mlx_adapter_ignores_empty_text_segments(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "part.m4a"
    audio.write_bytes(b"audio")

    def fake_transcribe(_audio_value: str, **_options: object) -> dict[str, object]:
        return {
            "language": "zh",
            "segments": [
                {"start": 0, "end": 1, "text": "有效内容"},
                {"start": 1, "end": 2, "text": "   "},
                {"start": 2, "end": 3, "text": "后续内容"},
            ],
        }

    transcript = MlxWhisperTranscriber(transcribe_fn=fake_transcribe).transcribe(audio)

    assert transcript.plain_text == "有效内容\n后续内容"
    assert [(cue.start_seconds, cue.end_seconds) for cue in transcript.cues] == [
        (0.0, 1.0),
        (2.0, 3.0),
    ]


def test_mlx_adapter_passes_language_and_reuses_one_instance(tmp_path: Path) -> None:
    first = tmp_path / "p01.m4a"
    second = tmp_path / "p02.m4a"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    calls: list[dict[str, object]] = []

    def fake_transcribe(audio_value: str, **options: object) -> dict[str, object]:
        calls.append({"audio": audio_value, **options})
        return {
            "language": "en",
            "segments": [{"start": 0, "end": 1, "text": audio_value}],
        }

    transcriber = MlxWhisperTranscriber(transcribe_fn=fake_transcribe)
    transcriber.transcribe(first, language="zh")
    transcriber.transcribe(second, language="zh")

    assert len(calls) == 2
    assert all(call["path_or_hf_repo"] == DEFAULT_MLX_MODEL for call in calls)
    assert all(call["language"] == "zh" for call in calls)
