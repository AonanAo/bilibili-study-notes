from __future__ import annotations

from dataclasses import dataclass, field

import streamlit_app
from note_viewer import ViewerContent
from web_service import WebGenerationResult, WebPartResult


@dataclass
class _StreamlitRecorder:
    errors: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)

    def markdown(self, _message: str) -> None:
        pass

    def write(self, _message: str) -> None:
        pass

    def success(self, _message: str) -> None:
        pass

    def warning(self, _message: str) -> None:
        pass

    def caption(self, _message: str) -> None:
        pass

    def info(self, message: str) -> None:
        self.infos.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)


def test_multi_part_summary_error_is_visible_when_viewer_has_content(
    monkeypatch,
) -> None:
    recorder = _StreamlitRecorder()
    monkeypatch.setattr(streamlit_app, "st", recorder)
    monkeypatch.setattr(streamlit_app, "render_note_viewer", lambda *_args, **_kwargs: None)
    result = WebGenerationResult(
        is_multi_part=True,
        parts=(
            WebPartResult(
                page_number=1,
                title="第一章",
                markdown="# 第一章\n",
                filename="P01_第一章.md",
            ),
        ),
        summary_error="课程总结生成失败",
        collection_summary_requested=True,
        viewer_contents=(
            ViewerContent(
                "part-1",
                "P1 总体笔记",
                "part",
                "# 第一章\n",
                "P01_第一章.md",
            ),
        ),
    )

    streamlit_app.render_generation_result(result)

    assert recorder.errors == [
        "summary.md 生成失败或无法读取：课程总结生成失败"
    ]


def test_multi_part_summary_skip_is_visible_when_viewer_has_content(
    monkeypatch,
) -> None:
    recorder = _StreamlitRecorder()
    monkeypatch.setattr(streamlit_app, "st", recorder)
    monkeypatch.setattr(streamlit_app, "render_note_viewer", lambda *_args, **_kwargs: None)
    result = WebGenerationResult(
        is_multi_part=True,
        parts=(
            WebPartResult(
                page_number=1,
                title="第一章",
                markdown="# 第一章\n",
                filename="P01_第一章.md",
            ),
        ),
        collection_summary_requested=False,
        viewer_contents=(
            ViewerContent(
                "part-1",
                "P1 总体笔记",
                "part",
                "# 第一章\n",
                "P01_第一章.md",
            ),
        ),
    )

    streamlit_app.render_generation_result(result)

    assert recorder.infos == ["本次已按设置跳过合集总结"]


def test_template_sections_are_restored_when_widget_state_was_removed() -> None:
    state: dict[str, object] = {"summary_template_key": "default"}

    streamlit_app._sync_template_section_state(
        state,
        template_key="default",
        default_section_keys=("core_knowledge", "key_points"),
        section_state_key="summary_section_keys",
        template_state_key="summary_template_key",
    )

    assert state == {
        "summary_template_key": "default",
        "summary_section_keys": ("core_knowledge", "key_points"),
    }


def test_template_sections_keep_current_customization_when_state_is_complete() -> None:
    state: dict[str, object] = {
        "summary_template_key": "default",
        "summary_section_keys": ("key_points",),
    }

    streamlit_app._sync_template_section_state(
        state,
        template_key="default",
        default_section_keys=("core_knowledge", "key_points"),
        section_state_key="summary_section_keys",
        template_state_key="summary_template_key",
    )

    assert state["summary_section_keys"] == ("key_points",)
