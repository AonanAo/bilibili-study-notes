from __future__ import annotations

from pathlib import Path

import pytest

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
