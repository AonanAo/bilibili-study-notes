from __future__ import annotations

from types import SimpleNamespace

import pytest

import llm
from prompt import build_course_summary_prompt, build_study_notes_prompt


VALID_NOTES = """# 视频主题
Python 基础

## 核心知识点
### 1. 变量
- 定义：保存数据的名称。
- 解释：可以在代码中引用数据。
- 重要程度：高，是基础概念。

## 关键观点
- 变量需要有清晰命名。

## 与已有知识关联
- 可以联系数学中的未知数。

## 复习问题
1. 什么是变量？
"""

VALID_SUMMARY = """# 视频整体主题
Python 课程

## 核心知识体系
- 从变量到函数。

## 各章节关系
- P01 是 P02 的基础。

## 关键概念
- 变量

## 学习建议
- 按顺序练习。
"""

VALID_TECHNICAL_NOTES = """# 视频主题
Python 技术学习

## 核心概念
- 变量

## 原理解释
- 名称绑定到数据。

## 实践案例
- 使用变量保存计数。

## 常见问题
- 变量名需要先定义。

## 复习问题
1. 什么是变量？
"""

VALID_COURSE_NOTES = """# 视频主题
Python 普通课程

## 内容概括
- 介绍 Python 基础。

## 核心知识点
- 变量

## 关键观点
- 命名需要清晰。

## 知识关联
- 联系数学中的未知数。

## 总结
- 理解变量是后续学习的基础。
"""


class FakeCompletions:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content),
                    finish_reason=self.finish_reason,
                )
            ]
        )


class FakeOpenAI:
    content = VALID_NOTES
    finish_reason = "stop"
    instances: list["FakeOpenAI"] = []

    def __init__(self, *, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.chat = SimpleNamespace(
            completions=FakeCompletions(self.content, self.finish_reason)
        )
        self.instances.append(self)


def test_prompt_contains_video_information_and_subtitle() -> None:
    result = build_study_notes_prompt(
        "这是字幕。",
        video_title="测试标题",
        video_description="测试简介",
    )

    assert "测试标题" in result
    assert "测试简介" in result
    assert "这是字幕。" in result
    assert "# 视频主题" in result
    assert "## 复习问题" in result


def test_generate_notes_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(llm.MissingAPIKeyError, match="DEEPSEEK_API_KEY"):
        llm.generate_study_notes("有效字幕")


def test_generate_notes_calls_deepseek_and_returns_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOpenAI.instances.clear()
    FakeOpenAI.content = f"```markdown\n{VALID_NOTES}\n```"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)

    result = llm.generate_study_notes(
        "Python 变量字幕",
        video_title="Python 入门",
    )

    assert result == VALID_NOTES.strip()
    client = FakeOpenAI.instances[0]
    assert client.api_key == "test-key"
    assert client.base_url == "https://api.deepseek.com"
    call = client.chat.completions.calls[0]
    assert call["model"] == "deepseek-v4-flash"
    assert call["max_tokens"] == 8192
    assert call["stream"] is False
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "Python 变量字幕" in call["messages"][1]["content"]


def test_generate_notes_allows_model_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOpenAI.instances.clear()
    FakeOpenAI.content = VALID_NOTES
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)

    llm.generate_study_notes("字幕")

    call = FakeOpenAI.instances[0].chat.completions.calls[0]
    assert call["model"] == "deepseek-v4-pro"


def test_generate_notes_rejects_incomplete_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOpenAI.instances.clear()
    FakeOpenAI.content = "# 视频主题\n只有一个章节"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)

    with pytest.raises(llm.InvalidLLMResponseError, match="缺少必要章节"):
        llm.generate_study_notes("字幕")


def test_generate_notes_accepts_specific_level_one_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOpenAI.instances.clear()
    FakeOpenAI.content = VALID_NOTES.replace(
        "# 视频主题",
        "# Agent 的概念、原理与构建模式",
        1,
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)

    result = llm.generate_study_notes("字幕")

    assert result.startswith("# Agent 的概念、原理与构建模式")


def test_generate_notes_restores_omitted_level_one_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOpenAI.instances.clear()
    FakeOpenAI.content = VALID_NOTES.replace("# 视频主题\n", "", 1)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)

    result = llm.generate_study_notes("字幕")

    assert result.startswith("# 视频主题\n\nPython 基础")


def test_generate_notes_reports_truncated_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOpenAI.instances.clear()
    FakeOpenAI.content = VALID_NOTES
    monkeypatch.setattr(FakeOpenAI, "finish_reason", "length")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)

    with pytest.raises(llm.InvalidLLMResponseError, match="长度上限"):
        llm.generate_study_notes("字幕")


def test_generate_notes_retries_one_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyThenValidCompletions(FakeCompletions):
        def create(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append(kwargs)
            content = "" if len(self.calls) == 1 else VALID_NOTES
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=content),
                        finish_reason="stop",
                    )
                ]
            )

    class EmptyThenValidOpenAI(FakeOpenAI):
        def __init__(self, *, api_key: str, base_url: str) -> None:
            self.api_key = api_key
            self.base_url = base_url
            self.chat = SimpleNamespace(completions=EmptyThenValidCompletions(""))
            self.instances.append(self)

    EmptyThenValidOpenAI.instances.clear()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm, "OpenAI", EmptyThenValidOpenAI)

    result = llm.generate_study_notes("字幕")

    assert result == VALID_NOTES.strip()
    completions = EmptyThenValidOpenAI.instances[0].chat.completions
    assert len(completions.calls) == 2


def test_generate_notes_rejects_two_empty_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOpenAI.instances.clear()
    FakeOpenAI.content = ""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)

    with pytest.raises(llm.InvalidLLMResponseError, match="连续两次返回了空"):
        llm.generate_study_notes("字幕")

    assert len(FakeOpenAI.instances[0].chat.completions.calls) == 2


@pytest.mark.parametrize(
    ("mode", "content", "expected_heading"),
    [
        ("technical", VALID_TECHNICAL_NOTES, "## 原理解释"),
        ("course", VALID_COURSE_NOTES, "## 内容概括"),
    ],
)
def test_generate_notes_uses_selected_mode_and_dynamic_validation(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    content: str,
    expected_heading: str,
) -> None:
    FakeOpenAI.instances.clear()
    FakeOpenAI.content = content
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)

    result = llm.generate_study_notes("课程字幕", mode=mode)

    assert result == content.strip()
    call = FakeOpenAI.instances[0].chat.completions.calls[0]
    assert expected_heading in call["messages"][1]["content"]


def test_generate_notes_rejects_headings_from_another_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOpenAI.instances.clear()
    FakeOpenAI.content = VALID_NOTES
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)

    with pytest.raises(llm.InvalidLLMResponseError, match="核心概念"):
        llm.generate_study_notes("字幕", mode="technical")


@pytest.mark.parametrize("mode", ["unknown", "academic"])
def test_generate_notes_rejects_unavailable_mode(mode: str) -> None:
    with pytest.raises(llm.InvalidNoteModeError):
        llm.generate_study_notes("字幕", mode=mode)


def test_generate_notes_adds_extra_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOpenAI.instances.clear()
    FakeOpenAI.content = VALID_TECHNICAL_NOTES
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)

    llm.generate_study_notes(
        "课程字幕",
        mode="technical",
        extra_instruction="请重点解释代码设计原因。",
    )

    call = FakeOpenAI.instances[0].chat.completions.calls[0]
    user_prompt = call["messages"][1]["content"]
    assert "【用户额外学习要求】" in user_prompt
    assert "请重点解释代码设计原因。" in user_prompt


def test_course_summary_prompt_contains_all_part_notes_and_failures() -> None:
    result = build_course_summary_prompt(
        [(1, "变量", "# 视频主题\n变量"), (2, "函数", "# 视频主题\n函数")],
        course_title="Python 课程",
        failed_parts=["P03 类：无字幕"],
    )

    assert "Python 课程" in result
    assert "P01 变量" in result
    assert "P02 函数" in result
    assert "P03 类：无字幕" in result
    assert "# 视频整体主题" in result


def test_generate_course_summary_appends_processing_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOpenAI.instances.clear()
    FakeOpenAI.content = VALID_SUMMARY
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)

    result = llm.generate_course_summary(
        [(1, "变量", VALID_NOTES)],
        course_title="Python 课程",
        failed_parts=["P02 函数：无字幕"],
    )

    assert "## 处理状态" in result
    assert "- 成功：P01 变量" in result
    assert "- 失败：P02 函数：无字幕" in result
    call = FakeOpenAI.instances[0].chat.completions.calls[0]
    assert "P02 函数：无字幕" in call["messages"][1]["content"]
