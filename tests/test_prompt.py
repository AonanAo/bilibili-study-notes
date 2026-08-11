from __future__ import annotations

import pytest

from prompt import (
    DEFAULT_NOTE_MODE,
    NOTE_MODES,
    NoteModeError,
    build_segment_content_prompt,
    build_segment_plan_prompt,
    build_study_notes_prompt,
    get_note_mode,
    get_selectable_note_modes,
)


def test_default_mode_preserves_v01_headings() -> None:
    note_mode = get_note_mode()

    assert note_mode.key == DEFAULT_NOTE_MODE
    assert note_mode.required_headings == (
        "# 视频主题",
        "## 核心知识点",
        "## 关键观点",
        "## 与已有知识关联",
        "## 复习问题",
    )


@pytest.mark.parametrize(
    ("mode", "headings"),
    [
        (
            "technical",
            (
                "# 视频主题",
                "## 核心概念",
                "## 原理解释",
                "## 实践案例",
                "## 常见问题",
                "## 复习问题",
            ),
        ),
        (
            "course",
            (
                "# 视频主题",
                "## 内容概括",
                "## 核心知识点",
                "## 关键观点",
                "## 知识关联",
                "## 总结",
            ),
        ),
    ],
)
def test_selectable_modes_define_required_headings(
    mode: str,
    headings: tuple[str, ...],
) -> None:
    note_mode = get_note_mode(mode)
    prompt = build_study_notes_prompt("字幕", mode=mode)

    assert note_mode.required_headings == headings
    assert all(heading in prompt for heading in headings)


def test_only_technical_and_course_are_selectable() -> None:
    assert [mode.key for mode in get_selectable_note_modes()] == [
        "technical",
        "course",
    ]


def test_academic_mode_interface_is_reserved() -> None:
    academic = NOTE_MODES["academic"]

    assert academic.selectable is False
    assert [section.title for section in academic.sections] == [
        "背景介绍",
        "核心观点",
        "论证过程",
        "学术意义",
        "思考问题",
    ]
    with pytest.raises(NoteModeError, match="尚未开放"):
        get_note_mode("academic")


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(NoteModeError, match="不支持的笔记模式"):
        get_note_mode("unknown")


def test_prompt_includes_extra_instruction_before_subtitle() -> None:
    prompt = build_study_notes_prompt(
        "字幕正文",
        mode="technical",
        extra_instruction="请重点解释代码设计原因。",
    )

    assert "【用户额外学习要求】" in prompt
    assert "请重点解释代码设计原因。" in prompt
    assert prompt.index("【用户额外学习要求】") < prompt.index("【字幕资料开始】")


@pytest.mark.parametrize("extra_instruction", [None, "", "   "])
def test_prompt_omits_empty_extra_instruction(
    extra_instruction: str | None,
) -> None:
    prompt = build_study_notes_prompt(
        "字幕正文",
        extra_instruction=extra_instruction,
    )

    assert "【用户额外学习要求】" not in prompt


def test_segment_plan_prompt_uses_full_srt_and_semantic_boundaries() -> None:
    prompt = build_segment_plan_prompt(
        "1\n00:00:00,000 --> 00:00:01,000\n字幕\n",
        video_title="Python",
        video_description="简介",
    )

    assert "按内容" in prompt or "语义" in prompt
    assert "不要使用固定分钟" in prompt
    assert '"start_seconds"' in prompt
    assert "00:00:00,000 --> 00:00:01,000" in prompt
    assert "Python" in prompt


def test_segment_content_prompt_contains_every_cut_subtitle_once() -> None:
    prompt = build_segment_content_prompt(
        [
            (1, "变量", 0.0, 10.0, "第一段 SRT"),
            (2, "函数", 10.0, 20.0, "第二段 SRT"),
        ],
        video_title="Python",
        extra_instruction="关注代码。",
    )

    assert prompt.count("第一段 SRT") == 1
    assert prompt.count("第二段 SRT") == 1
    assert "自适应" in prompt
    assert "本段概要" in prompt
    assert "不得包含" in prompt
    assert '"summary_points"' in prompt
    assert "关注代码。" in prompt
