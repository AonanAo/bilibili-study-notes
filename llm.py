"""DeepSeek API 调用模块。"""

from __future__ import annotations

import os

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)

from prompt import (
    COURSE_SUMMARY_SYSTEM_PROMPT,
    STUDY_NOTES_SYSTEM_PROMPT,
    build_course_summary_prompt,
    build_study_notes_prompt,
)


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

REQUIRED_MARKDOWN_HEADINGS = (
    "# 视频主题",
    "## 核心知识点",
    "## 关键观点",
    "## 与已有知识关联",
    "## 复习问题",
)

REQUIRED_SUMMARY_HEADINGS = (
    "# 视频整体主题",
    "## 核心知识体系",
    "## 各章节关系",
    "## 关键概念",
    "## 学习建议",
)


class LLMError(Exception):
    """DeepSeek 笔记生成错误的基类。"""


class MissingAPIKeyError(LLMError):
    """未配置 DeepSeek API Key。"""


class LLMRequestError(LLMError):
    """DeepSeek API 请求失败。"""


class InvalidLLMResponseError(LLMError):
    """DeepSeek 返回了空内容或格式不完整的笔记。"""


def _clean_markdown(markdown: str) -> str:
    """移除模型偶尔添加的外层 Markdown 代码块。"""

    content = markdown.strip()
    if content.startswith("```") and content.endswith("```"):
        lines = content.splitlines()
        if len(lines) >= 3:
            content = "\n".join(lines[1:-1]).strip()
    return content


def _validate_markdown(markdown: str, required_headings: tuple[str, ...]) -> None:
    """检查 Markdown 是否包含约定的所有章节。"""

    lines = {line.strip() for line in markdown.splitlines()}
    missing = [heading for heading in required_headings if heading not in lines]
    if missing:
        raise InvalidLLMResponseError(
            "DeepSeek 返回的 Markdown 缺少必要章节：" + "、".join(missing)
        )


def _request_markdown(
    *,
    system_prompt: str,
    user_prompt: str,
    required_headings: tuple[str, ...],
    model: str | None = None,
) -> str:
    """执行一次 DeepSeek 请求，并验证返回的 Markdown。"""

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise MissingAPIKeyError(
            "未找到 DEEPSEEK_API_KEY。请先在环境变量中设置 DeepSeek API Key。"
        )

    selected_model = model or os.environ.get("DEEPSEEK_MODEL", "").strip() or DEFAULT_MODEL
    client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)

    try:
        response = client.chat.completions.create(
            model=selected_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=4096,
            stream=False,
        )
    except AuthenticationError as error:
        raise LLMRequestError("身份验证失败，请检查 DEEPSEEK_API_KEY。") from error
    except RateLimitError as error:
        raise LLMRequestError(
            "DeepSeek 请求频率过高或账户额度不足，请稍后重试。"
        ) from error
    except (APITimeoutError, APIConnectionError) as error:
        raise LLMRequestError("无法连接 DeepSeek API，请检查网络后重试。") from error
    except APIStatusError as error:
        raise LLMRequestError(f"DeepSeek API 返回错误（HTTP {error.status_code}）。") from error
    except OpenAIError as error:
        raise LLMRequestError(f"DeepSeek API 请求失败：{error}") from error

    if not response.choices:
        raise InvalidLLMResponseError("DeepSeek 没有返回可用结果。")

    raw_content = response.choices[0].message.content
    if not isinstance(raw_content, str) or not raw_content.strip():
        raise InvalidLLMResponseError("DeepSeek 返回了空的 Markdown。")

    markdown = _clean_markdown(raw_content)
    _validate_markdown(markdown, required_headings)
    return markdown


def generate_study_notes(
    subtitle_text: str,
    *,
    video_title: str = "",
    video_description: str = "",
    model: str | None = None,
) -> str:
    """调用 DeepSeek，将字幕生成 Markdown 学习笔记。

    API Key 只从 ``DEEPSEEK_API_KEY`` 环境变量读取。如果需要
    替换模型，可以传入 ``model``，或设置 ``DEEPSEEK_MODEL``。
    """

    if not isinstance(subtitle_text, str) or not subtitle_text.strip():
        raise LLMError("字幕文本为空，无法生成学习笔记。")

    user_prompt = build_study_notes_prompt(
        subtitle_text.strip(),
        video_title=video_title.strip(),
        video_description=video_description.strip(),
    )
    return _request_markdown(
        system_prompt=STUDY_NOTES_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        required_headings=REQUIRED_MARKDOWN_HEADINGS,
        model=model,
    )


def generate_course_summary(
    part_notes: list[tuple[int, str, str]],
    *,
    course_title: str = "",
    failed_parts: list[str] | None = None,
    model: str | None = None,
) -> str:
    """读取所有成功的分 P 笔记，生成课程级 Markdown 总结。"""

    if not part_notes:
        raise LLMError("没有可用的分 P 学习笔记，无法生成课程总结。")

    failures = list(failed_parts or [])
    user_prompt = build_course_summary_prompt(
        part_notes,
        course_title=course_title.strip(),
        failed_parts=failures,
    )
    markdown = _request_markdown(
        system_prompt=COURSE_SUMMARY_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        required_headings=REQUIRED_SUMMARY_HEADINGS,
        model=model,
    )

    # 处理状态由程序追加，确保模型不会遗漏失败分 P。
    status_lines = [
        f"- 成功：P{page_number:02d} {title}"
        for page_number, title, _ in part_notes
    ]
    if failures:
        status_lines.extend(f"- 失败：{item}" for item in failures)
    else:
        status_lines.append("- 失败：无")

    return markdown.rstrip() + "\n\n## 处理状态\n" + "\n".join(status_lines)
